from personal_agent_gateway.agents import AgentRegistry
from personal_agent_gateway.lmg_client import (
    LMGProtocolMismatch,
    ProviderExecutionCapabilities,
    parse_provider_execution_capabilities,
)
from personal_agent_gateway.teams import TeamRunCycle, TeamRunService


class ProviderRecoveryRequired(RuntimeError):
    def __init__(self, provider: str, reason_code: str) -> None:
        super().__init__(f"{provider}: {reason_code}")
        self.provider = provider
        self.reason_code = reason_code


def capability_payload(
    capabilities: ProviderExecutionCapabilities,
) -> dict[str, object]:
    return {
        "resume": capabilities.resume,
        "external_read_only_roots": capabilities.external_read_only_roots,
        "network_modes": list(capabilities.network_modes),
        "sandbox_modes": list(capabilities.sandbox_modes),
        "permission_modes": list(capabilities.permission_modes),
    }


def capabilities_for_cycle(
    cycle: TeamRunCycle,
    provider: str,
) -> ProviderExecutionCapabilities:
    metadata = (
        cycle.execution_metadata
        if isinstance(cycle.execution_metadata, dict)
        else {}
    )
    snapshots = metadata.get("provider_capabilities")
    snapshot = (
        snapshots.get(provider)
        if isinstance(snapshots, dict)
        else None
    )
    try:
        return parse_provider_execution_capabilities(snapshot)
    except LMGProtocolMismatch as exc:
        raise ProviderRecoveryRequired(
            provider,
            _recovery_reason(snapshot),
        ) from exc


class TeamProviderRecovery:
    def __init__(
        self,
        teams: TeamRunService,
        registry: AgentRegistry,
    ) -> None:
        self._teams = teams
        self._registry = registry

    def freeze_cycle(self, cycle_id: str) -> TeamRunCycle:
        cycle = self._teams.get_cycle(cycle_id)
        providers = sorted(
            {
                agent.backend
                for agent in self._teams.list_agents(cycle.team_run_id)
            }
        )
        snapshots: dict[str, dict[str, object]] = {}
        for provider in providers:
            try:
                descriptor = self._registry.get(provider)
            except ValueError as exc:
                raise ProviderRecoveryRequired(
                    provider,
                    "capabilities_unavailable",
                ) from exc
            capabilities = descriptor.execution_capabilities
            if capabilities is None:
                raise ProviderRecoveryRequired(
                    provider,
                    descriptor.readiness_error or "capabilities_unavailable",
                )
            snapshots[provider] = {
                "ready": descriptor.ready,
                "readiness_error": descriptor.readiness_error,
                "snapshot_status": descriptor.snapshot_status,
                "detected_at": descriptor.detected_at,
                "execution": capability_payload(capabilities),
            }

        existing = (
            cycle.execution_metadata
            if isinstance(cycle.execution_metadata, dict)
            else {}
        )
        return self._teams.set_cycle_execution_metadata(
            cycle.id,
            {**existing, "provider_capabilities": snapshots},
        )


def _recovery_reason(snapshot: object) -> str:
    if isinstance(snapshot, dict):
        reason = snapshot.get("readiness_error")
        if isinstance(reason, str) and reason:
            return reason
    return "capabilities_unavailable"
