import asyncio
import json
from collections.abc import Callable

from personal_agent_gateway.events import EventBus
from personal_agent_gateway.model_client import ModelClient
from personal_agent_gateway.teams import TeamAgent, TeamRun, TeamRunService, TeamTask

PLANNING_PROMPT = """You are the leader agent for a personal-agent-gateway Team Run.
Return ONLY JSON array of task objects.
Each object must have "title" and "description".
Goal: {goal}
Persona snapshot: {persona_snapshot_json}"""

WORKER_PROMPT = """You are an agent in a personal-agent-gateway Team Run.
Persona:
{persona_snapshot_json}
Goal: {goal}
Assigned task: {task_title}
Task description: {task_description}

If you need information from another team member to proceed, end your reply with
ONLY this fenced block and nothing after it:
```json
{{"needs_info": {{"topic": "<short topic>", "question": "<your question>"}}}}
```
Otherwise, return your concise final result: changed files and verification evidence."""

SYNTHESIS_PROMPT = """You are the leader of a personal-agent-gateway Team Run.
Summarize the outcome for the user.
Goal: {goal}
Task results:
{results}
Write a concise summary of what was accomplished and note any failures."""

MEDIATION_PROMPT = """You are the leader mediating a Team Run.
Goal: {goal}
A worker on task "{task_title}" asks: {question}

Team outputs so far:
{outputs}

Answer concisely to unblock the worker. If the information is unavailable, say so plainly."""

ADD_WORK_PROMPT = """You are the leader agent for a personal-agent-gateway Team Run.
The user is adding work to an in-flight run. Break the request into concrete tasks.
Return ONLY a JSON array of task objects. Each object must have "title" and "description".
Goal: {goal}
Existing tasks: {existing_titles}
User request: {instruction}"""

AGENT_REINVOCATION_CAP = 3


def _rules_block(snapshot: dict | None, include_persona_baseline: bool) -> str:
    if not snapshot:
        return ""
    sections: list[tuple[str, dict | None]] = [
        ("GLOBAL RULES", snapshot.get("global")),
        ("TEAM RULES", snapshot.get("team")),
    ]
    if include_persona_baseline:
        sections.append(("PERSONA BASELINE", snapshot.get("persona_baseline")))
    lines: list[str] = []
    for title, section in sections:
        if not section:
            continue
        personality = (section.get("personality") or "").strip()
        rules = section.get("rules") or []
        if not personality and not rules:
            continue
        lines.append(f"[{title}]")
        if personality:
            lines.append(personality)
        for rule in rules:
            prefix = "MUST" if rule.get("level") == "REQUIRED" else "SHOULD"
            lines.append(f"- {prefix}: {rule.get('text', '')}")
        lines.append("")
    if not lines:
        return ""
    return "TEAM CHARTER (frozen at run start):\n" + "\n".join(lines).strip() + "\n\n"


class TeamRuntime:
    def __init__(
        self,
        teams: TeamRunService,
        model_factory: Callable[[TeamAgent], ModelClient],
        event_bus: EventBus | None = None,
    ) -> None:
        self._teams = teams
        self._model_factory = model_factory
        self._event_bus = event_bus

    async def start(self, team_run_id: str) -> TeamRun:
        run = self._teams.get_team_run(team_run_id)
        leader: TeamAgent | None = None
        try:
            leader = _find_leader(self._teams.list_agents(run.id))
            run = self._teams.set_run_status(run.id, "planning")
            leader = self._teams.set_agent_status(leader.id, "running")
            await self._publish({"type": "team.run.started", "team_run_id": run.id})

            await self._plan(run, leader)

            run = self._teams.get_team_run(run.id)
            if run.run_mode != "plan_and_execute":
                self._teams.set_agent_status(leader.id, "completed")
                run = self._teams.set_run_status(run.id, "completed")
                await self._publish({"type": "team.run.completed", "team_run_id": run.id})
                return run

            workers = _find_workers(self._teams.list_agents(run.id))
            if not workers:
                error = "plan_and_execute run has no worker agents (empty member_persona_ids)"
                run = self._teams.set_run_status(run.id, "failed", error_message=error)
                self._teams.set_agent_status(leader.id, "failed")
                await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": error})
                return run

            return await self._execute_and_synthesize(run, leader, workers)
        except asyncio.CancelledError:
            if run is not None:
                self._settle_canceled(run)
            raise
        except Exception as exc:  # noqa: BLE001
            run = self._teams.set_run_status(run.id, "failed", error_message=str(exc))
            if leader is not None:
                self._teams.set_agent_status(leader.id, "failed")
            await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": str(exc)})
            return run

    async def _plan(self, run: TeamRun, leader: TeamAgent) -> list[dict[str, str]]:
        leader_agent = self._teams.get_agent(leader.id)
        model = self._model_factory(leader_agent)
        prompt = _rules_block(run.rules_snapshot, include_persona_baseline=False) + PLANNING_PROMPT.format(
            goal=run.goal,
            persona_snapshot_json=json.dumps(leader_agent.persona_snapshot, ensure_ascii=False),
        )
        response = await model.complete([{"role": "user", "content": prompt}])
        if response.upstream_session_id:
            self._teams.set_agent_session(leader_agent.id, response.upstream_session_id)
        try:
            tasks = _parse_task_plan(response.content)
        except ValueError:
            retry = await model.complete(
                [{"role": "user", "content": prompt + "\nReturn ONLY a JSON array. No prose, no code fences."}]
            )
            if retry.upstream_session_id:
                self._teams.set_agent_session(leader_agent.id, retry.upstream_session_id)
            tasks = _parse_task_plan(retry.content)
        for task in tasks:
            created = self._teams.create_task(run.id, task["title"], task["description"])
            await self._publish({"type": "team.task.created", "team_run_id": run.id, "task_id": created.id})
        self._teams.append_message(
            run.id, leader.id, None, "plan_note", f"Planning completed with {len(tasks)} tasks.", {}
        )
        return tasks

    async def _execute(self, run: TeamRun, leader: TeamAgent, workers: list[TeamAgent]) -> None:
        counter = 0
        while True:
            pending = [task for task in self._teams.list_tasks(run.id) if task.status == "pending"]
            if not pending:
                return
            task = pending[0]
            worker = workers[counter % len(workers)]
            counter += 1
            self._teams.set_task_status(task.id, "in_progress")
            self._teams.set_agent_status(worker.id, "running")
            try:
                result = await self._run_task(run, leader, worker, task)
                self._teams.append_message(
                    run.id, worker.id, None, "agent_output", result, {"task_id": task.id}
                )
                self._teams.set_task_status(task.id, "completed", result=result)
                self._teams.set_agent_status(worker.id, "completed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._teams.set_task_status(task.id, "failed", error_message=str(exc))
                self._teams.set_agent_status(worker.id, "failed")
            await self._publish(
                {"type": "team.task.updated", "team_run_id": run.id, "task_id": task.id}
            )

    async def _run_task(
        self, run: TeamRun, leader: TeamAgent, worker: TeamAgent, task: TeamTask
    ) -> str:
        worker_agent = self._teams.get_agent(worker.id)
        model = self._model_factory(worker_agent)
        response = await model.complete(
            [{"role": "user", "content": self._worker_prompt(run, worker_agent, task)}]
        )
        if response.upstream_session_id:
            self._teams.set_agent_session(worker_agent.id, response.upstream_session_id)
        content = response.content

        while True:
            req = _parse_needs_info(content)
            if req is None:
                return content
            run = self._teams.get_team_run(run.id)
            worker_agent = self._teams.get_agent(worker.id)
            if run.rounds_used >= run.rounds_budget or worker_agent.reinvocations >= AGENT_REINVOCATION_CAP:
                return await self._resume_worker(
                    worker.id,
                    "No more consultation is available. Produce your best-effort final "
                    "result now, without a needs_info block.",
                )
            self._teams.append_message(
                run.id, worker.id, leader.id, "query", req["question"],
                {"task_id": task.id, "topic": req["topic"]},
            )
            answer = await self._mediate(run, leader, task, req["question"])
            run = self._teams.increment_rounds_used(run.id)
            self._teams.append_message(
                run.id, leader.id, worker.id, "answer", answer, {"round": run.rounds_used}
            )
            content = await self._resume_worker(
                worker.id,
                f"Answer to your question: {answer}\n\nContinue and produce your final "
                "result, or ask again only if essential.",
            )
            self._teams.increment_agent_reinvocations(worker.id)

    async def _resume_worker(self, worker_id: str, instruction: str) -> str:
        worker_agent = self._teams.get_agent(worker_id)
        model = self._model_factory(worker_agent)
        response = await model.complete([{"role": "user", "content": instruction}])
        if response.upstream_session_id:
            self._teams.set_agent_session(worker_agent.id, response.upstream_session_id)
        return response.content

    async def _mediate(self, run: TeamRun, leader: TeamAgent, task: TeamTask, question: str) -> str:
        leader_agent = self._teams.get_agent(leader.id)
        model = self._model_factory(leader_agent)
        prompt = MEDIATION_PROMPT.format(
            goal=run.goal,
            task_title=task.title,
            question=question,
            outputs=self._collect_outputs(run),
        )
        response = await model.complete([{"role": "user", "content": prompt}])
        if response.upstream_session_id:
            self._teams.set_agent_session(leader_agent.id, response.upstream_session_id)
        return response.content

    def _collect_outputs(self, run: TeamRun) -> str:
        lines = [
            f"[{task.title}]\n{task.result}"
            for task in self._teams.list_tasks(run.id)
            if task.status == "completed" and task.result
        ]
        return "\n\n".join(lines) if lines else "(no completed task outputs yet)"

    def _worker_prompt(self, run: TeamRun, worker: TeamAgent, task: TeamTask) -> str:
        return _rules_block(run.rules_snapshot, include_persona_baseline=True) + WORKER_PROMPT.format(
            persona_snapshot_json=json.dumps(worker.persona_snapshot, ensure_ascii=False),
            goal=run.goal,
            task_title=task.title,
            task_description=task.description,
        )

    async def _execute_and_synthesize(self, run: TeamRun, leader: TeamAgent, workers: list[TeamAgent]) -> TeamRun:
        while True:
            await self._execute(run, leader, workers)
            tasks = self._teams.list_tasks(run.id)
            status = _terminal_status(tasks)
            if status == "failed":
                run = self._teams.set_run_status(run.id, "failed", error_message="All tasks failed")
                self._teams.set_agent_status(leader.id, "failed")
                await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": "All tasks failed"})
                return run
            run = self._teams.set_run_status(run.id, "summarizing")
            summary = await self._leader_synthesis(run, leader, tasks)
            if any(task.status == "pending" for task in self._teams.list_tasks(run.id)):
                run = self._teams.set_run_status(run.id, "running")
                continue
            run = self._teams.set_run_status(run.id, status, summary=summary)
            self._teams.set_agent_status(leader.id, "completed")
            await self._publish({"type": "team.run.completed", "team_run_id": run.id})
            return run

    async def resume(self, team_run_id: str) -> TeamRun:
        run = self._teams.get_team_run(team_run_id)
        if not self._teams.list_tasks(run.id):
            return await self.start(team_run_id)
        leader: TeamAgent | None = None
        try:
            leader = _find_leader(self._teams.list_agents(run.id))
            run = self._teams.set_run_status(run.id, "running")
            leader = self._teams.set_agent_status(leader.id, "running")
            await self._publish({"type": "team.run.reopened", "team_run_id": run.id})
            workers = sorted(
                _find_workers(self._teams.list_agents(run.id)),
                key=lambda agent: agent.status != "pending",
            )
            if not workers:
                error = "resume has no worker agents"
                run = self._teams.set_run_status(run.id, "failed", error_message=error)
                self._teams.set_agent_status(leader.id, "failed")
                await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": error})
                return run
            return await self._execute_and_synthesize(run, leader, workers)
        except asyncio.CancelledError:
            if run is not None:
                self._settle_canceled(run)
            raise
        except Exception as exc:  # noqa: BLE001
            run = self._teams.set_run_status(run.id, "failed", error_message=str(exc))
            if leader is not None:
                self._teams.set_agent_status(leader.id, "failed")
            await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": str(exc)})
            return run

    async def add_work(self, team_run_id: str, instruction: str) -> list[TeamTask]:
        run = self._teams.get_team_run(team_run_id)
        leader = _find_leader(self._teams.list_agents(run.id))
        leader_agent = self._teams.get_agent(leader.id)
        model = self._model_factory(leader_agent)
        existing = ", ".join(task.title for task in self._teams.list_tasks(run.id)) or "(none)"
        prompt = ADD_WORK_PROMPT.format(goal=run.goal, existing_titles=existing, instruction=instruction)
        response = await model.complete([{"role": "user", "content": prompt}])
        if response.upstream_session_id:
            self._teams.set_agent_session(leader_agent.id, response.upstream_session_id)
        try:
            specs = _parse_task_plan(response.content)
        except ValueError:
            retry = await model.complete(
                [{"role": "user", "content": prompt + "\nReturn ONLY a JSON array. No prose, no code fences."}]
            )
            if retry.upstream_session_id:
                self._teams.set_agent_session(leader_agent.id, retry.upstream_session_id)
            specs = _parse_task_plan(retry.content)
        created: list[TeamTask] = []
        for spec in specs:
            task = self._teams.create_task(run.id, spec["title"], spec["description"])
            created.append(task)
            await self._publish({"type": "team.task.created", "team_run_id": run.id, "task_id": task.id})
        self._teams.append_message(
            run.id, leader.id, None, "plan_note", f"Added {len(created)} task(s) from user request.", {}
        )
        return created

    async def _leader_synthesis(self, run: TeamRun, leader: TeamAgent, tasks: list[TeamTask]) -> str:
        results = "\n\n".join(
            f"[{task.status}] {task.title}\n{task.result or task.error_message or ''}"
            for task in tasks
        )
        leader_agent = self._teams.get_agent(leader.id)
        model = self._model_factory(leader_agent)
        response = await model.complete(
            [{"role": "user", "content": SYNTHESIS_PROMPT.format(goal=run.goal, results=results)}]
        )
        if response.upstream_session_id:
            self._teams.set_agent_session(leader_agent.id, response.upstream_session_id)
        self._teams.append_message(run.id, leader.id, None, "synthesis", response.content, {})
        return response.content

    def _settle_canceled(self, run: TeamRun) -> None:
        for agent in self._teams.list_agents(run.id):
            if agent.status == "running":
                self._teams.set_agent_status(agent.id, "canceled")
        for task in self._teams.list_tasks(run.id):
            if task.status == "in_progress":
                self._teams.set_task_status(task.id, "canceled")
        self._teams.set_run_status(run.id, "canceled")

    async def _publish(self, event: dict[str, object]) -> None:
        if self._event_bus is not None:
            await self._event_bus.publish(event)


def _find_leader(agents: list[TeamAgent]) -> TeamAgent:
    for agent in agents:
        if agent.role == "leader":
            return agent
    raise ValueError("Team run has no leader agent")


def _find_workers(agents: list[TeamAgent]) -> list[TeamAgent]:
    return [agent for agent in agents if agent.role != "leader"]


def _terminal_status(tasks: list[TeamTask]) -> str:
    if not tasks:
        return "completed"
    statuses = [task.status for task in tasks]
    if all(status == "failed" for status in statuses):
        return "failed"
    if any(status == "failed" for status in statuses):
        return "completed_with_failures"
    return "completed"


def _parse_task_plan(content: str) -> list[dict[str, str]]:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    raw = json.loads(stripped)
    if not isinstance(raw, list):
        raise ValueError("Planner response must be a JSON array")
    tasks = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Planner task must be an object")
        title = item.get("title")
        description = item.get("description")
        if not isinstance(title, str) or not isinstance(description, str):
            raise ValueError("Planner task requires title and description")
        tasks.append({"title": title, "description": description})
    return tasks


def _parse_needs_info(content: str) -> dict[str, str] | None:
    block = _last_json_block(content)
    if block is None:
        return None
    try:
        data = json.loads(block)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    req = data.get("needs_info")
    if not isinstance(req, dict):
        return None
    question = req.get("question")
    if not isinstance(question, str) or not question.strip():
        return None
    topic = req.get("topic")
    return {"topic": topic if isinstance(topic, str) else "", "question": question.strip()}


def _last_json_block(content: str) -> str | None:
    fence = "```json"
    idx = content.rfind(fence)
    if idx != -1:
        rest = content[idx + len(fence):]
        end = rest.find("```")
        if end != -1:
            return rest[:end].strip()
    # 펜스가 없으면 마지막 중괄호 그룹 시도
    start = content.rfind("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        return content[start:end + 1].strip()
    return None
