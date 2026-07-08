import json
from collections.abc import Callable

from personal_agent_gateway.events import EventBus
from personal_agent_gateway.model_client import ModelClient
from personal_agent_gateway.teams import TeamAgent, TeamRun, TeamRunService

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

            model = self._model_factory(leader)
            prompt = PLANNING_PROMPT.format(
                goal=run.goal,
                persona_snapshot_json=json.dumps(leader.persona_snapshot, ensure_ascii=False),
            )
            response = await model.complete([{"role": "user", "content": prompt}])
            tasks = _parse_task_plan(response.content)

            for task in tasks:
                created = self._teams.create_task(run.id, task["title"], task["description"])
                await self._publish(
                    {"type": "team.task.created", "team_run_id": run.id, "task_id": created.id}
                )

            self._teams.append_message(
                run.id,
                leader.id,
                None,
                "note",
                f"Planning completed with {len(tasks)} tasks.",
                {},
            )

            run = self._teams.get_team_run(run.id)
            if run.run_mode != "plan_and_execute":
                run = self._teams.set_run_status(run.id, "completed")
                self._teams.set_agent_status(leader.id, "completed")
                await self._publish({"type": "team.run.completed", "team_run_id": run.id})
                return run

            workers = _find_workers(self._teams.list_agents(run.id))
            if not workers:
                error = "plan_and_execute run has no worker agents (empty member_persona_ids)"
                run = self._teams.set_run_status(run.id, "failed", error_message=error)
                self._teams.set_agent_status(leader.id, "failed")
                await self._publish(
                    {"type": "team.run.failed", "team_run_id": run.id, "error": error}
                )
                return run

            created_tasks = self._teams.list_tasks(run.id)
            for index, task in enumerate(created_tasks):
                worker = workers[index % len(workers)] if workers else None
                if worker is None:
                    continue

                self._teams.set_task_status(task.id, "in_progress")
                self._teams.set_agent_status(worker.id, "running")

                worker_model = self._model_factory(worker)
                worker_prompt = WORKER_PROMPT.format(
                    persona_snapshot_json=json.dumps(
                        worker.persona_snapshot, ensure_ascii=False
                    ),
                    goal=run.goal,
                    task_title=task.title,
                    task_description=task.description,
                )
                worker_response = await worker_model.complete(
                    [{"role": "user", "content": worker_prompt}]
                )

                self._teams.set_task_status(task.id, "completed", result=worker_response.content)
                await self._publish(
                    {"type": "team.task.updated", "team_run_id": run.id, "task_id": task.id}
                )

                message = self._teams.append_message(
                    run.id,
                    worker.id,
                    None,
                    "agent_output",
                    worker_response.content,
                    {"task_id": task.id},
                )
                await self._publish(
                    {
                        "type": "team.message.created",
                        "team_run_id": run.id,
                        "message_id": message.id,
                    }
                )

                self._teams.set_agent_status(worker.id, "completed")

            self._teams.set_agent_status(leader.id, "completed")
            run = self._teams.set_run_status(run.id, "summarizing")
            run = self._teams.set_run_status(
                run.id,
                "completed",
                summary=f"Completed {len(created_tasks)} tasks.",
            )
            await self._publish({"type": "team.run.completed", "team_run_id": run.id})

            return run
        except Exception as exc:  # noqa: BLE001 - graceful containment, matches AgentRuntime
            run = self._teams.set_run_status(run.id, "failed", error_message=str(exc))
            if leader is not None:
                self._teams.set_agent_status(leader.id, "failed")
            await self._publish(
                {"type": "team.run.failed", "team_run_id": run.id, "error": str(exc)}
            )
            return run

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
