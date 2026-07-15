from dataclasses import dataclass
from datetime import datetime, timezone

from personal_agent_gateway.audit import AuditService
from personal_agent_gateway.intake import IntakeGate
from personal_agent_gateway.job_worker import JobWorker
from personal_agent_gateway.run_state import SessionRunRegistry, TeamRunRegistry
from personal_agent_gateway.teams import TeamRunService


@dataclass(frozen=True)
class EmergencyStopResult:
    stopped_at: str
    changed: bool
    session_ids: list[str]
    team_run_ids: list[str]
    job_ids: list[str]
    failures: list[str]


class EmergencyStopService:
    def __init__(
        self,
        *,
        intake_gate: IntakeGate,
        session_runs: SessionRunRegistry,
        team_runs: TeamRunRegistry,
        team_run_service: TeamRunService,
        job_worker: JobWorker,
        audit: AuditService,
    ) -> None:
        self._intake_gate = intake_gate
        self._session_runs = session_runs
        self._team_runs = team_runs
        self._team_run_service = team_run_service
        self._job_worker = job_worker
        self._audit = audit

    async def stop(
        self,
        *,
        actor_id: str,
        correlation_id: str | None,
    ) -> EmergencyStopResult:
        changed = self._intake_gate.close()
        failures: list[str] = []
        session_ids: list[str] = []
        team_run_ids: list[str] = []
        job_ids: list[str] = []

        try:
            session_ids = await self._session_runs.cancel_all()
        except Exception as exc:
            failures.append(f"sessions:{type(exc).__name__}")
        try:
            team_run_ids = await self._team_runs.cancel_all(reason="emergency_stop")
            for team_run_id in team_run_ids:
                try:
                    self._team_run_service.interrupt_run(
                        team_run_id,
                        include_canceled=True,
                    )
                except Exception as exc:
                    failures.append(f"team:{team_run_id}:{type(exc).__name__}")
        except Exception as exc:
            failures.append(f"teams:{type(exc).__name__}")
        try:
            job_ids = await self._job_worker.emergency_stop()
        except Exception as exc:
            failures.append(f"jobs:{type(exc).__name__}")

        stopped_at = datetime.now(timezone.utc).isoformat()
        self._audit.record(
            event_type="security.emergency_stop",
            action="operations.emergency_stop",
            status="partial_failure" if failures else "success",
            severity="critical" if failures else "warning",
            actor_type="owner",
            actor_id=actor_id,
            correlation_id=correlation_id,
            resource_type="gateway",
            metadata={
                "changed": changed,
                "session_count": len(session_ids),
                "team_run_count": len(team_run_ids),
                "job_count": len(job_ids),
                "failures": failures,
            },
        )
        return EmergencyStopResult(
            stopped_at=stopped_at,
            changed=changed,
            session_ids=session_ids,
            team_run_ids=team_run_ids,
            job_ids=job_ids,
            failures=failures,
        )

    def resume(
        self,
        *,
        actor_id: str,
        correlation_id: str | None,
    ) -> bool:
        changed = self._intake_gate.open()
        self._audit.record(
            event_type="security.intake_resumed",
            action="operations.resume_intake",
            status="success",
            actor_type="owner",
            actor_id=actor_id,
            correlation_id=correlation_id,
            resource_type="gateway",
            metadata={"changed": changed},
        )
        return changed
