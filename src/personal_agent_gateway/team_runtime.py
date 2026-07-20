import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass

from personal_agent_gateway.events import EventBus
from personal_agent_gateway.model_client import ModelClient
from personal_agent_gateway.redaction import redact_text
from personal_agent_gateway.teams import TeamAgent, TeamRun, TeamRunService, TeamTask

PLANNING_PROMPT = """You are the leader agent for a personal-agent-gateway Team Run.
Return ONLY JSON array of task objects.
Each object must have "title" and "description".
Goal: {goal}
Persona snapshot: {persona_snapshot_json}"""

WORKER_PROMPT = """You are an agent in a personal-agent-gateway Team Run.
Persona:
{persona_snapshot_json}

Perform the concrete assignment below now. It is the complete user request.
Do not ask the user what work to do and do not substitute unrelated repository work.

CONCRETE ASSIGNMENT
Goal: {goal}
Assigned task: {task_title}
Task description: {task_description}

If you need information from another team member to proceed, end your reply with
ONLY this fenced block and nothing after it:
```json
{{"needs_info": {{"topic": "<short topic>", "question": "<your question>"}}}}
```
Otherwise, return a concise final result tailored to the assigned task and cite the
evidence you used."""

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

First answer from the goal, frozen rules, prior user decisions, and completed outputs.
Return ONLY one JSON object in one of these forms:
{{"resolution":{{"kind":"answer","answer":"concise instruction for the worker"}}}}
{{"resolution":{{"kind":"ask_user","topic":"short topic","question":"one concrete question","why_needed":"why work cannot safely continue","options":[{{"id":"stable-id","label":"label","impact":"tradeoff"}}],"recommended_option_id":"stable-id or null","blocking_scope":"task or run"}}}}
Use ask_user only when the user must decide. Prefer task scope; use run scope only when remaining work would be invalid or cause major rework."""

ADD_WORK_PROMPT = """You are the leader agent for a personal-agent-gateway Team Run.
The user is adding work to an in-flight run. Break the request into concrete tasks.
Return ONLY a JSON array of task objects. Each object must have "title" and "description".
Goal: {goal}
Existing tasks: {existing_titles}
User request: {instruction}"""

AGENT_REINVOCATION_CAP = 3


@dataclass(frozen=True)
class UserDecisionResolution:
    decision: dict[str, object]


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
    return "TEAM RULES (frozen at run start):\n" + "\n".join(lines).strip() + "\n\n"


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

    async def start(self, team_run_id: str, cycle_id: str | None = None) -> TeamRun:
        run = self._teams.get_team_run(team_run_id)
        self._validate_cycle(run, cycle_id)
        leader: TeamAgent | None = None
        try:
            if cycle_id is not None:
                self._activate_cycle(cycle_id)
            leader = _find_leader(self._teams.list_agents(run.id))
            run = self._teams.set_run_status(run.id, "planning")
            leader = self._teams.set_agent_status(leader.id, "running")
            await self._publish({"type": "team.run.started", "team_run_id": run.id})

            await self._plan(run, leader, cycle_id)

            run = self._teams.get_team_run(run.id)
            if run.run_mode != "plan_and_execute":
                self._teams.set_agent_status(leader.id, "completed")
                run = self._teams.set_run_status(run.id, "completed")
                if cycle_id is not None:
                    self._teams.set_cycle_status(cycle_id, "completed")
                await self._publish({"type": "team.run.completed", "team_run_id": run.id})
                return run

            workers = _find_workers(self._teams.list_agents(run.id))
            if not workers:
                error = "plan_and_execute run has no worker agents (empty member_persona_ids)"
                run = self._teams.set_run_status(run.id, "failed", error_message=error)
                if cycle_id is not None:
                    self._teams.set_cycle_status(cycle_id, "failed", error_message=error)
                self._teams.set_agent_status(leader.id, "failed")
                await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": error})
                return run

            run = self._teams.set_run_status(run.id, "running")
            await self._publish({"type": "team.run.executing", "team_run_id": run.id})
            return await self._execute_and_synthesize(run, leader, workers, cycle_id)
        except asyncio.CancelledError:
            if run is not None:
                self._settle_canceled(run, cycle_id)
            raise
        except Exception as exc:  # noqa: BLE001
            error = redact_text(exc) or type(exc).__name__
            run = self._teams.set_run_status(run.id, "failed", error_message=error)
            if cycle_id is not None:
                self._teams.set_cycle_status(cycle_id, "failed", error_message=error)
            if leader is not None:
                self._teams.set_agent_status(leader.id, "failed")
            await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": error})
            return run

    async def _plan(
        self, run: TeamRun, leader: TeamAgent, cycle_id: str | None = None
    ) -> list[dict[str, str]]:
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
            created = self._teams.create_task(
                run.id, task["title"], task["description"], cycle_id=cycle_id
            )
            await self._publish({"type": "team.task.created", "team_run_id": run.id, "task_id": created.id})
        self._teams.append_message(
            run.id,
            leader.id,
            None,
            "plan_note",
            f"Planning completed with {len(tasks)} tasks.",
            {},
            cycle_id=cycle_id,
        )
        return tasks

    async def _execute(
        self,
        run: TeamRun,
        leader: TeamAgent,
        workers: list[TeamAgent],
        cycle_id: str | None = None,
    ) -> None:
        counter = 0
        while True:
            pending = [
                task
                for task in self._teams.list_tasks(run.id, cycle_id)
                if task.status == "pending"
            ]
            if not pending:
                return
            task = pending[0]
            worker = workers[counter % len(workers)]
            counter += 1
            task, worker = self._teams.start_task(task.id, worker.id)
            await self._publish(
                {
                    "type": "team.task.updated",
                    "team_run_id": run.id,
                    "task_id": task.id,
                    "agent_id": worker.id,
                }
            )
            try:
                result = await self._run_task(run, leader, worker, task)
                if isinstance(result, UserDecisionResolution):
                    request = self._teams.defer_task_for_user_decision(
                        task.id, worker.id, result.decision
                    )
                    task = self._teams.get_task(task.id)
                    worker = self._teams.get_agent(worker.id)
                    await self._publish(
                        {
                            "type": "team.task.updated",
                            "team_run_id": run.id,
                            "task_id": task.id,
                            "agent_id": worker.id,
                            "decision_request_id": request.id,
                        }
                    )
                    if result.decision.get("blocking_scope") == "run":
                        return
                    continue
                self._teams.append_message(
                    run.id,
                    worker.id,
                    None,
                    "agent_output",
                    result,
                    {"task_id": task.id},
                    cycle_id=cycle_id,
                )
                task, worker = self._teams.finish_task(
                    task.id, worker.id, "completed", result=result
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                error = redact_text(exc) or type(exc).__name__
                task, worker = self._teams.finish_task(
                    task.id, worker.id, "failed", error_message=error
                )
            await self._publish(
                {
                    "type": "team.task.updated",
                    "team_run_id": run.id,
                    "task_id": task.id,
                    "agent_id": worker.id,
                }
            )

    async def _run_task(
        self, run: TeamRun, leader: TeamAgent, worker: TeamAgent, task: TeamTask
    ) -> str | UserDecisionResolution:
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
            if task.cycle_id is not None:
                cycle = self._teams.get_cycle(task.cycle_id)
                rounds_used = cycle.rounds_used
                rounds_budget = cycle.rounds_budget
            else:
                rounds_used = run.rounds_used
                rounds_budget = run.rounds_budget
            if rounds_used >= rounds_budget or worker_agent.reinvocations >= AGENT_REINVOCATION_CAP:
                return await self._resume_worker(
                    worker.id,
                    "No more consultation is available. Produce your best-effort final "
                    "result now, without a needs_info block.",
                )
            query_message = self._teams.append_message(
                run.id, worker.id, leader.id, "query", req["question"],
                {"task_id": task.id, "topic": req["topic"]},
                cycle_id=task.cycle_id,
            )
            resolution = await self._mediate(run, leader, task, req["question"])
            if task.cycle_id is not None:
                rounds_used = self._teams.increment_cycle_rounds_used(
                    task.cycle_id
                ).rounds_used
            else:
                run = self._teams.increment_rounds_used(run.id)
                rounds_used = run.rounds_used
            if resolution["kind"] == "ask_user":
                resolution["query_message_id"] = query_message.id
                return UserDecisionResolution(resolution)
            answer = str(resolution["answer"])
            self._teams.append_message(
                run.id,
                leader.id,
                worker.id,
                "answer",
                answer,
                {"round": rounds_used, "query_id": query_message.id},
                cycle_id=task.cycle_id,
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

    async def _mediate(
        self, run: TeamRun, leader: TeamAgent, task: TeamTask, question: str
    ) -> dict[str, object]:
        leader_agent = self._teams.get_agent(leader.id)
        model = self._model_factory(leader_agent)
        prompt = MEDIATION_PROMPT.format(
            goal=run.goal,
            task_title=task.title,
            question=question,
            outputs=self._collect_outputs(run, task.cycle_id),
        )
        response = await model.complete([{"role": "user", "content": prompt}])
        if response.upstream_session_id:
            self._teams.set_agent_session(leader_agent.id, response.upstream_session_id)
        return _parse_mediation_resolution(response.content)

    def _collect_outputs(self, run: TeamRun, cycle_id: str | None = None) -> str:
        lines = [
            f"[{task.title}]\n{task.result}"
            for task in self._teams.list_tasks(run.id, cycle_id)
            if task.status == "completed" and task.result
        ]
        return "\n\n".join(lines) if lines else "(no completed task outputs yet)"

    def _worker_prompt(self, run: TeamRun, worker: TeamAgent, task: TeamTask) -> str:
        prompt = _rules_block(run.rules_snapshot, include_persona_baseline=True) + WORKER_PROMPT.format(
            persona_snapshot_json=json.dumps(worker.persona_snapshot, ensure_ascii=False),
            goal=run.goal,
            task_title=task.title,
            task_description=task.description,
        )
        decision_context = self._teams.decision_context_for_task(run.id, task.id)
        if decision_context:
            prompt += f"\n\nResolved user decisions for this task:\n{decision_context}"
        return prompt

    async def _execute_and_synthesize(
        self,
        run: TeamRun,
        leader: TeamAgent,
        workers: list[TeamAgent],
        cycle_id: str | None = None,
    ) -> TeamRun:
        while True:
            await self._execute(run, leader, workers, cycle_id)
            request = self._teams.get_active_decision_request(run.id, cycle_id)
            if request is not None and request.status == "collecting":
                request = self._teams.publish_decision_request(run.id, cycle_id)
                if cycle_id is not None:
                    self._teams.set_cycle_status(cycle_id, "waiting_for_user")
                run = self._teams.get_team_run(run.id)
                await self._publish(
                    {
                        "type": "team.run.input_requested",
                        "team_run_id": run.id,
                        "decision_request_id": request.id,
                        "question_count": len(request.items),
                    }
                )
                return run
            tasks = self._teams.list_tasks(run.id, cycle_id)
            status = _terminal_status(tasks)
            if status == "failed":
                run = self._teams.set_run_status(run.id, "failed", error_message="All tasks failed")
                if cycle_id is not None:
                    self._teams.set_cycle_status(
                        cycle_id, "failed", error_message="All tasks failed"
                    )
                self._teams.set_agent_status(leader.id, "failed")
                await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": "All tasks failed"})
                return run
            run = self._teams.set_run_status(run.id, "summarizing")
            await self._publish({"type": "team.run.summarizing", "team_run_id": run.id})
            summary = await self._leader_synthesis(run, leader, tasks)
            if any(
                task.status == "pending"
                for task in self._teams.list_tasks(run.id, cycle_id)
            ):
                run = self._teams.set_run_status(run.id, "running")
                await self._publish({"type": "team.run.executing", "team_run_id": run.id})
                continue
            run = self._teams.set_run_status(run.id, status, summary=summary)
            if cycle_id is not None:
                self._teams.set_cycle_status(cycle_id, status, summary=summary)
            self._teams.set_agent_status(leader.id, "completed")
            await self._publish({"type": "team.run.completed", "team_run_id": run.id})
            return run

    async def resume(self, team_run_id: str, cycle_id: str | None = None) -> TeamRun:
        run = self._teams.get_team_run(team_run_id)
        self._validate_cycle(run, cycle_id)
        if not self._teams.list_tasks(run.id, cycle_id):
            return await self.start(team_run_id, cycle_id)
        leader: TeamAgent | None = None
        try:
            if cycle_id is not None:
                self._activate_cycle(cycle_id)
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
                if cycle_id is not None:
                    self._teams.set_cycle_status(cycle_id, "failed", error_message=error)
                self._teams.set_agent_status(leader.id, "failed")
                await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": error})
                return run
            return await self._execute_and_synthesize(run, leader, workers, cycle_id)
        except asyncio.CancelledError:
            if run is not None:
                self._settle_canceled(run, cycle_id)
            raise
        except Exception as exc:  # noqa: BLE001
            error = redact_text(exc) or type(exc).__name__
            run = self._teams.set_run_status(run.id, "failed", error_message=error)
            if cycle_id is not None:
                self._teams.set_cycle_status(cycle_id, "failed", error_message=error)
            if leader is not None:
                self._teams.set_agent_status(leader.id, "failed")
            await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": error})
            return run

    async def add_work(
        self, team_run_id: str, instruction: str, cycle_id: str | None = None
    ) -> list[TeamTask]:
        run = self._teams.get_team_run(team_run_id)
        self._validate_cycle(run, cycle_id)
        leader = _find_leader(self._teams.list_agents(run.id))
        leader_agent = self._teams.get_agent(leader.id)
        model = self._model_factory(leader_agent)
        existing = (
            ", ".join(
                task.title for task in self._teams.list_tasks(run.id, cycle_id)
            )
            or "(none)"
        )
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
            task = self._teams.create_task(
                run.id, spec["title"], spec["description"], cycle_id=cycle_id
            )
            created.append(task)
            await self._publish({"type": "team.task.created", "team_run_id": run.id, "task_id": task.id})
        self._teams.append_message(
            run.id,
            leader.id,
            None,
            "plan_note",
            f"Added {len(created)} task(s) from user request.",
            {},
            cycle_id=cycle_id,
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
        cycle_id = tasks[0].cycle_id if tasks else None
        self._teams.append_message(
            run.id,
            leader.id,
            None,
            "synthesis",
            response.content,
            {},
            cycle_id=cycle_id,
        )
        return response.content

    def _settle_canceled(self, run: TeamRun, cycle_id: str | None = None) -> None:
        for task in self._teams.list_tasks(run.id, cycle_id):
            if task.status == "in_progress":
                if task.owner_agent_id:
                    self._teams.finish_task(task.id, task.owner_agent_id, "canceled")
                else:
                    self._teams.set_task_status(task.id, "canceled")
        for agent in self._teams.list_agents(run.id):
            if agent.status == "running":
                self._teams.set_agent_status(agent.id, "canceled")
        self._teams.set_run_status(run.id, "canceled")
        if cycle_id is not None:
            self._teams.set_cycle_status(cycle_id, "canceled")

    def _validate_cycle(self, run: TeamRun, cycle_id: str | None) -> None:
        if run.lifecycle_mode == "continuous" and cycle_id is None:
            raise ValueError("Continuous team runs require a cycle")
        if cycle_id is not None:
            cycle = self._teams.get_cycle(cycle_id)
            if cycle.team_run_id != run.id:
                raise ValueError("Cycle belongs to a different team run")

    def _activate_cycle(self, cycle_id: str) -> None:
        cycle = self._teams.get_cycle(cycle_id)
        if cycle.status == "queued":
            self._teams.reset_agent_reinvocations(cycle.team_run_id)
        self._teams.set_cycle_status(cycle_id, "running")

    async def _publish(self, event: dict[str, object]) -> None:
        if self._event_bus is not None:
            enriched = dict(event)
            team_run_id = event.get("team_run_id")
            if isinstance(team_run_id, str) and str(event.get("type", "")).startswith(
                "team.run."
            ):
                try:
                    enriched["run"] = _run_delta(self._teams.get_team_run(team_run_id))
                except KeyError:
                    pass
            task_id = event.get("task_id")
            if isinstance(task_id, str):
                try:
                    enriched["task"] = _task_delta(self._teams.get_task(task_id))
                except KeyError:
                    pass
            agent_id = event.get("agent_id")
            if isinstance(agent_id, str):
                try:
                    enriched["agent"] = _agent_delta(self._teams.get_agent(agent_id))
                except KeyError:
                    pass
            await self._event_bus.publish(enriched)


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


def _run_delta(run: TeamRun) -> dict[str, object]:
    return {
        "id": run.id,
        "status": run.status,
        "summary": run.summary,
        "error_message": run.error_message,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "updated_at": run.updated_at,
    }


def _task_delta(task: TeamTask) -> dict[str, object]:
    return {
        "id": task.id,
        "team_run_id": task.team_run_id,
        "title": task.title,
        "description": task.description,
        "owner_agent_id": task.owner_agent_id,
        "status": task.status,
        "error_message": task.error_message,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
    }


def _agent_delta(agent: TeamAgent) -> dict[str, object]:
    return {
        "id": agent.id,
        "team_run_id": agent.team_run_id,
        "status": agent.status,
        "current_task_id": agent.current_task_id,
        "started_at": agent.started_at,
        "finished_at": agent.finished_at,
        "updated_at": agent.updated_at,
    }


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


def _parse_mediation_resolution(content: str) -> dict[str, object]:
    stripped = content.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1 and stripped.endswith("```"):
            stripped = stripped[first_newline + 1 : -3].strip()
    try:
        raw = json.loads(stripped)
    except json.JSONDecodeError:
        return {"kind": "answer", "answer": content.strip()}
    if not isinstance(raw, dict) or not isinstance(raw.get("resolution"), dict):
        return {"kind": "answer", "answer": content.strip()}
    resolution = raw["resolution"]
    if resolution.get("kind") != "ask_user":
        answer = resolution.get("answer")
        return {
            "kind": "answer",
            "answer": answer.strip() if isinstance(answer, str) else content.strip(),
        }
    question = resolution.get("question")
    if not isinstance(question, str) or not question.strip():
        return {"kind": "answer", "answer": content.strip()}
    options: list[dict[str, str]] = []
    raw_options = resolution.get("options")
    if isinstance(raw_options, list):
        for option in raw_options:
            if not isinstance(option, dict):
                continue
            option_id = option.get("id")
            label = option.get("label")
            if not isinstance(option_id, str) or not option_id.strip():
                continue
            if not isinstance(label, str) or not label.strip():
                continue
            impact = option.get("impact")
            options.append(
                {
                    "id": option_id.strip(),
                    "label": label.strip(),
                    "impact": impact.strip() if isinstance(impact, str) else "",
                }
            )
    topic = resolution.get("topic")
    why_needed = resolution.get("why_needed")
    recommended = resolution.get("recommended_option_id")
    return {
        "kind": "ask_user",
        "topic": topic.strip() if isinstance(topic, str) else "",
        "question": question.strip(),
        "why_needed": why_needed.strip() if isinstance(why_needed, str) else "",
        "options": options,
        "recommended_option_id": recommended.strip() if isinstance(recommended, str) else None,
        "blocking_scope": "run" if resolution.get("blocking_scope") == "run" else "task",
    }


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
