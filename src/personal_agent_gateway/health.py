from dataclasses import dataclass

from personal_agent_gateway.db import Database
from personal_agent_gateway.intake import IntakeGate


@dataclass(frozen=True)
class ComponentHealth:
    name: str
    ready: bool
    detail: str

    def payload(self) -> dict[str, object]:
        return {"name": self.name, "ready": self.ready, "detail": self.detail}


class HealthService:
    def __init__(
        self,
        database: Database,
        worker: object,
        scheduler: object,
        agent_registry: object,
        required_agent_id: str,
        intake_gate: IntakeGate,
        hook_loop: object,
        hook_runner: object,
    ) -> None:
        self._database = database
        self._worker = worker
        self._scheduler = scheduler
        self._agent_registry = agent_registry
        self._required_agent_id = required_agent_id
        self._intake_gate = intake_gate
        self._hook_loop = hook_loop
        self._hook_runner = hook_runner

    def components(self) -> list[ComponentHealth]:
        return [
            self._database_health(),
            ComponentHealth(
                "worker",
                bool(getattr(self._worker, "alive", False)),
                "ready" if getattr(self._worker, "alive", False) else "not running",
            ),
            ComponentHealth(
                "scheduler",
                bool(getattr(self._scheduler, "alive", False)),
                "ready" if getattr(self._scheduler, "alive", False) else "not running",
            ),
            self._background_health("hook_loop", self._hook_loop),
            self._background_health("hook_runner", self._hook_runner),
            self._cli_health(),
            ComponentHealth(
                "intake",
                self._intake_gate.is_open,
                "open" if self._intake_gate.is_open else "stopped",
            ),
        ]

    @staticmethod
    def _background_health(name: str, component: object) -> ComponentHealth:
        alive = bool(getattr(component, "alive", False))
        if not alive:
            return ComponentHealth(name, False, "not running")
        last_error = getattr(component, "last_error", None)
        detail = f"degraded: {str(last_error)[:500]}" if last_error else "ready"
        return ComponentHealth(name, True, detail)

    def _database_health(self) -> ComponentHealth:
        try:
            with self._database.connection() as connection:
                connection.execute("create temp table health_probe (value integer)")
                connection.execute("insert into health_probe values (1)")
                row = connection.execute("select value from health_probe").fetchone()
            ready = row is not None and row["value"] == 1
            return ComponentHealth("database", ready, "ready" if ready else "probe failed")
        except Exception:
            return ComponentHealth("database", False, "probe failed")

    def _cli_health(self) -> ComponentHealth:
        try:
            agents = self._agent_registry.catalog()
            agent = next(
                (item for item in agents if item.id == self._required_agent_id),
                None,
            )
            if agent is None or not agent.available:
                return ComponentHealth("cli", False, "required CLI unavailable")
            return ComponentHealth("cli", True, "ready")
        except Exception:
            return ComponentHealth("cli", False, "probe failed")
