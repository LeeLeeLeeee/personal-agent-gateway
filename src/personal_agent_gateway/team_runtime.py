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
        run = self._teams.set_run_status(team_run_id, "planning")
        await self._publish({"type": "team.run.started", "team_run_id": run.id})

        leader = _find_leader(self._teams.list_agents(run.id))
        leader = self._teams.set_agent_status(leader.id, "running")

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
        if run.run_mode == "planning_only":
            run = self._teams.set_run_status(run.id, "completed")
            await self._publish({"type": "team.run.completed", "team_run_id": run.id})

        return run

    async def _publish(self, event: dict[str, object]) -> None:
        if self._event_bus is not None:
            await self._event_bus.publish(event)


def _find_leader(agents: list[TeamAgent]) -> TeamAgent:
    for agent in agents:
        if agent.role == "leader":
            return agent
    raise ValueError("Team run has no leader agent")


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
