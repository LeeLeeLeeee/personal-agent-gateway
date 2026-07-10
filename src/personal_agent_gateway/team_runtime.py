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
Return concise result, changed files, and verification evidence."""

AGENT_REINVOCATION_CAP = 3


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
        self._worker_models: dict[str, ModelClient] = {}

    async def start(self, team_run_id: str) -> TeamRun:
        run = self._teams.get_team_run(team_run_id)
        leader: TeamAgent | None = None
        try:
            leader = _find_leader(self._teams.list_agents(run.id))
            run = self._teams.set_run_status(run.id, "planning")
            leader = self._teams.set_agent_status(leader.id, "running")
            await self._publish({"type": "team.run.started", "team_run_id": run.id})

            tasks = await self._plan(run, leader)

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

            await self._execute(run, leader, workers)
            return await self._synthesize(run, leader)
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
        prompt = PLANNING_PROMPT.format(
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
        tasks = self._teams.list_tasks(run.id)
        # One model instance per worker for this execute pass, so a worker assigned
        # multiple tasks keeps a single underlying session/conversation across them.
        self._worker_models = {
            worker.id: self._model_factory(self._teams.get_agent(worker.id)) for worker in workers
        }
        for index, task in enumerate(tasks):
            worker = workers[index % len(workers)]
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
        model = self._worker_models[worker_agent.id]
        response = await model.complete(
            [{"role": "user", "content": self._worker_prompt(run, worker_agent, task)}]
        )
        if response.upstream_session_id:
            self._teams.set_agent_session(worker_agent.id, response.upstream_session_id)
        return response.content

    def _worker_prompt(self, run: TeamRun, worker: TeamAgent, task: TeamTask) -> str:
        return WORKER_PROMPT.format(
            persona_snapshot_json=json.dumps(worker.persona_snapshot, ensure_ascii=False),
            goal=run.goal,
            task_title=task.title,
            task_description=task.description,
        )

    async def _synthesize(self, run: TeamRun, leader: TeamAgent) -> TeamRun:
        tasks = self._teams.list_tasks(run.id)
        status = _terminal_status(tasks)
        if status == "failed":
            run = self._teams.set_run_status(run.id, "failed", error_message="All tasks failed")
            self._teams.set_agent_status(leader.id, "failed")
            await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": "All tasks failed"})
            return run
        run = self._teams.set_run_status(run.id, "summarizing")
        done = sum(1 for t in tasks if t.status == "completed")
        summary = f"{done}/{len(tasks)} tasks completed."
        run = self._teams.set_run_status(run.id, status, summary=summary)
        self._teams.set_agent_status(leader.id, "completed")
        await self._publish({"type": "team.run.completed", "team_run_id": run.id})
        return run

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
