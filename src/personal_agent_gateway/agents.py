import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import Any, Literal

from pydantic import BaseModel

from personal_agent_gateway.config import AppConfig
from personal_agent_gateway.lmg_client import (
    LMGProtocolMismatch,
    LmgQueryResult,
    ProviderExecutionCapabilities,
    fetch_capabilities,
    parse_provider_execution_capabilities,
    parse_provider_readiness,
)

AgentId = Literal["codex", "claude"]


class AgentOption(BaseModel):
    name: str
    kind: str
    choices: list[str] = []
    required: bool = False


class AgentModel(BaseModel):
    id: str
    label: str
    description: str = ""
    efforts: list[str] = []
    default_effort: str = ""


class AgentDescriptor(BaseModel):
    id: AgentId
    label: str
    kind: Literal["local_cli"] = "local_cli"
    binary: str
    available: bool
    availability_error: str | None = None
    models: list[str]
    model_options: list[AgentModel]
    default_model: str
    options_schema: list[AgentOption]
    defaults: dict[str, Any]
    version: str = ""
    capability_source: list[str] = []
    execution_capabilities: ProviderExecutionCapabilities | None = None
    ready: bool = False
    readiness_error: str | None = None
    snapshot_status: Literal["fresh", "stale", "unavailable"] = "unavailable"
    detected_at: str = ""


@dataclass(frozen=True)
class CliProbeResult:
    available: bool
    error: str | None


Probe = Callable[[str], CliProbeResult]
CapabilityLoader = Callable[
    [AppConfig],
    LmgQueryResult[dict[str, object]] | dict[str, object] | None,
]


def probe_cli(binary: str) -> CliProbeResult:
    try:
        completed = subprocess.run(
            [binary, "--help"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except FileNotFoundError:
        return CliProbeResult(False, "not found on PATH")
    except subprocess.TimeoutExpired:
        return CliProbeResult(False, "probe timed out")
    except OSError as exc:
        return CliProbeResult(False, str(exc))
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        return CliProbeResult(False, detail[:200] or f"exit {completed.returncode}")
    return CliProbeResult(True, None)


class AgentRegistry:
    def __init__(
        self,
        config: AppConfig,
        probe: Probe | None = None,
        capability_loader: CapabilityLoader | None = None,
        *,
        cache_ttl_seconds: float = 30.0,
        failure_ttl_seconds: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._probe = probe or probe_cli
        self._capability_loader = (
            capability_loader
            if capability_loader is not None
            else fetch_capabilities
            if probe is None
            else lambda _config: None
        )
        self._catalog: list[AgentDescriptor] | None = None
        self._last_known_good_catalog: list[AgentDescriptor] | None = None
        self._cache_ttl_seconds = cache_ttl_seconds
        self._failure_ttl_seconds = failure_ttl_seconds
        self._clock = clock
        self._catalog_expires_at = 0.0
        self._lock = RLock()

    def catalog(self) -> list[AgentDescriptor]:
        with self._lock:
            now = self._clock()
            if self._catalog is not None and now < self._catalog_expires_at:
                return self._catalog

            loaded = self._capability_loader(self._config)
            detected, gateway_status = _loaded_capabilities(loaded)
            completed_at = self._clock()
            if (
                gateway_status in {"unreachable", "not_ready"}
                and not detected
                and self._last_known_good_catalog is not None
            ):
                self._catalog = [
                    descriptor.model_copy(
                        update={
                            "ready": False,
                            "readiness_error": f"gateway_{gateway_status}",
                            "snapshot_status": "stale",
                        }
                    )
                    for descriptor in self._last_known_good_catalog
                ]
                self._catalog_expires_at = completed_at + max(
                    self._failure_ttl_seconds,
                    0.0,
                )
                return self._catalog

            usable_snapshot = bool(detected) and gateway_status in {
                None,
                "ready",
                "not_ready",
            }
            providers = detected.get("providers")
            provider_map = providers if isinstance(providers, dict) else {}
            snapshot_status: Literal["fresh", "stale", "unavailable"] = (
                _snapshot_status(detected) if usable_snapshot else "unavailable"
            )
            detected_at = _string(detected.get("detected_at")) if usable_snapshot else ""
            self._catalog = [
                self._codex(
                    _provider_payload(provider_map, "codex"),
                    gateway_status=gateway_status,
                    snapshot_status=snapshot_status,
                    detected_at=detected_at,
                ),
                self._claude(
                    _provider_payload(provider_map, "claude"),
                    gateway_status=gateway_status,
                    snapshot_status=snapshot_status,
                    detected_at=detected_at,
                ),
            ]
            if usable_snapshot and any(
                descriptor.execution_capabilities is not None
                for descriptor in self._catalog
            ):
                self._last_known_good_catalog = [
                    descriptor.model_copy(deep=True) for descriptor in self._catalog
                ]
            ttl = (
                self._cache_ttl_seconds
                if gateway_status in {None, "ready"}
                else self._failure_ttl_seconds
            )
            self._catalog_expires_at = completed_at + max(ttl, 0.0)
            return self._catalog

    def get(self, agent_id: str) -> AgentDescriptor:
        for descriptor in self.catalog():
            if descriptor.id == agent_id:
                return descriptor
        raise ValueError(f"Unknown agent: {agent_id}")

    def validate_config(
        self,
        agent_id: str,
        model: str,
        options: dict[str, Any],
        *,
        require_available: bool = True,
    ) -> dict[str, Any]:
        descriptor = self.get(agent_id)
        if require_available and not descriptor.available:
            raise ValueError(f"Agent unavailable: {agent_id}")
        if model not in descriptor.models:
            raise ValueError(f"Unsupported model for {agent_id}: {model}")
        schema = {option.name: option for option in descriptor.options_schema}
        for key, value in options.items():
            option = schema.get(key)
            if option is None:
                raise ValueError(f"Unsupported option for {agent_id}: {key}")
            if option.choices and value not in option.choices:
                raise ValueError(
                    f"Unsupported option value for {agent_id}: {key}={value}"
                )
        effort = options.get("effort")
        selected_model = next(
            (candidate for candidate in descriptor.model_options if candidate.id == model),
            None,
        )
        if (
            effort
            and selected_model is not None
            and selected_model.efforts
            and effort not in selected_model.efforts
        ):
            raise ValueError(f"Unsupported effort for {agent_id} model {model}: {effort}")
        return {"agent_id": descriptor.id, "model": model, "options": dict(options)}

    def _codex(
        self,
        capabilities: dict[str, object],
        *,
        gateway_status: str | None,
        snapshot_status: Literal["fresh", "stale", "unavailable"],
        detected_at: str,
    ) -> AgentDescriptor:
        probe = _gateway_probe(gateway_status, capabilities) or self._probe(
            self._config.codex_binary
        )
        fallback_models = _fallback_models(
            ["default", "gpt-5.5", "gpt-5.4"],
            ["low", "medium", "high", "xhigh"],
            "high",
        )
        models = _detected_models(capabilities, fallback_models)
        effort_choices = _model_efforts(models)
        options = _options(capabilities)
        sandbox_choices = _string_list(options.get("sandbox")) or [
            "read-only",
            "workspace-write",
            "danger-full-access",
        ]
        approval_choices = _string_list(options.get("approval_policy")) or [
            "untrusted",
            "on-request",
            "never",
        ]
        profile_choices = _string_list(options.get("profile"))
        detected_defaults = _defaults(capabilities)
        available, error = _availability(probe, capabilities)
        ready, readiness_error = _readiness(probe, capabilities)
        return AgentDescriptor(
            id="codex",
            label="Codex CLI",
            binary=self._config.codex_binary,
            available=available,
            availability_error=error,
            models=[model.id for model in models],
            model_options=models,
            default_model=_supported_default(detected_defaults.get("model"), models, "default"),
            options_schema=[
                AgentOption(
                    name="effort",
                    kind="select",
                    choices=effort_choices,
                ),
                AgentOption(
                    name="sandbox",
                    kind="select",
                    choices=sandbox_choices,
                ),
                AgentOption(
                    name="approval_policy",
                    kind="select",
                    choices=approval_choices,
                ),
                AgentOption(
                    name="profile",
                    kind="select" if profile_choices else "text",
                    choices=profile_choices,
                ),
            ],
            defaults={
                "effort": _supported_choice(
                    detected_defaults.get("effort"), effort_choices, "high"
                ),
                "sandbox": _supported_choice(
                    self._config.codex_sandbox, sandbox_choices, "workspace-write"
                ),
                "approval_policy": _supported_choice(
                    self._config.codex_approval_policy, approval_choices, "never"
                ),
            },
            version=_string(capabilities.get("version")),
            capability_source=_string_list(capabilities.get("source")) or ["fallback"],
            execution_capabilities=_execution_capabilities(capabilities),
            ready=ready,
            readiness_error=readiness_error,
            snapshot_status=snapshot_status,
            detected_at=detected_at,
        )

    def _claude(
        self,
        capabilities: dict[str, object],
        *,
        gateway_status: str | None,
        snapshot_status: Literal["fresh", "stale", "unavailable"],
        detected_at: str,
    ) -> AgentDescriptor:
        probe = _gateway_probe(gateway_status, capabilities) or self._probe(
            self._config.claude_binary
        )
        fallback_models = _fallback_models(
            ["default", "best", "sonnet", "opus", "haiku", "sonnet[1m]", "opus[1m]", "opusplan"],
            ["low", "medium", "high", "xhigh", "max"],
            "medium",
        )
        models = _detected_models(capabilities, fallback_models)
        effort_choices = _model_efforts(models)
        options = _options(capabilities)
        permission_choices = _string_list(options.get("permission_mode")) or [
            "acceptEdits",
            "auto",
            "bypassPermissions",
            "manual",
            "dontAsk",
            "plan",
        ]
        agent_choices = _string_list(options.get("agent"))
        detected_defaults = _defaults(capabilities)
        available, error = _availability(probe, capabilities)
        ready, readiness_error = _readiness(probe, capabilities)
        return AgentDescriptor(
            id="claude",
            label="Claude Code",
            binary=self._config.claude_binary,
            available=available,
            availability_error=error,
            models=[model.id for model in models],
            model_options=models,
            default_model=_supported_default(detected_defaults.get("model"), models, "sonnet"),
            options_schema=[
                AgentOption(
                    name="effort",
                    kind="select",
                    choices=effort_choices,
                ),
                AgentOption(
                    name="permission_mode",
                    kind="select",
                    choices=permission_choices,
                ),
                AgentOption(
                    name="agent",
                    kind="select" if agent_choices else "text",
                    choices=agent_choices,
                ),
            ],
            defaults={
                "effort": _supported_choice(
                    detected_defaults.get("effort"), effort_choices, "medium"
                ),
                "permission_mode": _supported_choice(
                    self._config.claude_permission_mode,
                    permission_choices,
                    "manual",
                ),
            },
            version=_string(capabilities.get("version")),
            capability_source=_string_list(capabilities.get("source")) or ["fallback"],
            execution_capabilities=_execution_capabilities(capabilities),
            ready=ready,
            readiness_error=readiness_error,
            snapshot_status=snapshot_status,
            detected_at=detected_at,
        )


def _provider_payload(providers: dict[object, object], provider: str) -> dict[str, object]:
    value = providers.get(provider)
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _loaded_capabilities(
    loaded: LmgQueryResult[dict[str, object]] | dict[str, object] | None,
) -> tuple[dict[str, object], str | None]:
    if isinstance(loaded, LmgQueryResult):
        return loaded.data or {}, loaded.status
    return loaded or {}, None


def _execution_capabilities(
    provider: dict[str, object],
) -> ProviderExecutionCapabilities | None:
    if "execution" not in provider:
        return None
    try:
        return parse_provider_execution_capabilities(provider)
    except LMGProtocolMismatch:
        return None


def _gateway_probe(
    status: str | None,
    capabilities: dict[str, object],
) -> CliProbeResult | None:
    if status is None:
        return None
    if status in {"ready", "not_ready"} and capabilities:
        return CliProbeResult(True, None)
    if status in {"ready", "not_ready"}:
        return CliProbeResult(False, "provider_not_detected")
    return CliProbeResult(False, f"gateway_{status}")


def _readiness(
    probe: CliProbeResult,
    capabilities: dict[str, object],
) -> tuple[bool, str | None]:
    try:
        readiness = parse_provider_readiness(capabilities)
    except LMGProtocolMismatch:
        return probe.available, probe.error
    return readiness.ready, readiness.error_code


def _snapshot_status(
    detected: dict[str, object],
) -> Literal["fresh", "stale"]:
    return "stale" if detected.get("snapshot_status") == "stale" else "fresh"


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item for item in value if isinstance(item, str) and item))


def _fallback_models(ids: list[str], efforts: list[str], default_effort: str) -> list[AgentModel]:
    return [
        AgentModel(
            id=model_id,
            label="Default" if model_id == "default" else model_id,
            efforts=efforts,
            default_effort=default_effort,
        )
        for model_id in ids
    ]


def _detected_models(
    capabilities: dict[str, object],
    fallback: list[AgentModel],
) -> list[AgentModel]:
    raw_models = capabilities.get("models")
    if not isinstance(raw_models, list):
        return fallback
    models: list[AgentModel] = []
    for raw in raw_models:
        if not isinstance(raw, dict):
            continue
        model_id = _string(raw.get("id"))
        if not model_id or any(model.id == model_id for model in models):
            continue
        models.append(
            AgentModel(
                id=model_id,
                label=_string(raw.get("label")) or model_id,
                description=_string(raw.get("description")),
                efforts=_string_list(raw.get("efforts")),
                default_effort=_string(raw.get("default_effort")),
            )
        )
    return models or fallback


def _model_efforts(models: list[AgentModel]) -> list[str]:
    return list(dict.fromkeys(effort for model in models for effort in model.efforts))


def _options(capabilities: dict[str, object]) -> dict[str, object]:
    value = capabilities.get("options")
    return {str(key): item for key, item in value.items()} if isinstance(value, dict) else {}


def _defaults(capabilities: dict[str, object]) -> dict[str, object]:
    value = capabilities.get("defaults")
    return {str(key): item for key, item in value.items()} if isinstance(value, dict) else {}


def _supported_choice(value: object, choices: list[str], fallback: str) -> str:
    candidate = _string(value)
    if candidate in choices:
        return candidate
    if fallback in choices:
        return fallback
    return choices[0] if choices else ""


def _supported_default(value: object, models: list[AgentModel], fallback: str) -> str:
    choices = [model.id for model in models]
    return _supported_choice(value, choices, fallback)


def _availability(
    probe: CliProbeResult,
    capabilities: dict[str, object],
) -> tuple[bool, str | None]:
    detected_available = capabilities.get("available")
    execution_capabilities = _execution_capabilities(capabilities)
    available = (
        probe.available
        and bool(capabilities)
        and detected_available is not False
        and execution_capabilities is not None
    )
    if available:
        return True, None
    detected_error = _string(capabilities.get("error"))
    return False, probe.error or detected_error or "unavailable"
