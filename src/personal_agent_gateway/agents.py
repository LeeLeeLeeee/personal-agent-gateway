import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from personal_agent_gateway.config import AppConfig
from personal_agent_gateway.lmg_client import fetch_capabilities

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


@dataclass(frozen=True)
class CliProbeResult:
    available: bool
    error: str | None


Probe = Callable[[str], CliProbeResult]
CapabilityLoader = Callable[[AppConfig], dict[str, object] | None]


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
    ) -> None:
        self._config = config
        self._probe = probe or probe_cli
        self._capability_loader = (
            capability_loader
            if capability_loader is not None
            else (fetch_capabilities if probe is None else lambda _config: None)
        )
        self._catalog: list[AgentDescriptor] | None = None

    def catalog(self) -> list[AgentDescriptor]:
        if self._catalog is None:
            detected = self._capability_loader(self._config) or {}
            providers = detected.get("providers")
            provider_map = providers if isinstance(providers, dict) else {}
            self._catalog = [
                self._codex(_provider_payload(provider_map, "codex")),
                self._claude(_provider_payload(provider_map, "claude")),
            ]
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
    ) -> dict[str, Any]:
        descriptor = self.get(agent_id)
        if not descriptor.available:
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

    def _codex(self, capabilities: dict[str, object]) -> AgentDescriptor:
        probe = self._probe(self._config.codex_binary)
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
        )

    def _claude(self, capabilities: dict[str, object]) -> AgentDescriptor:
        probe = self._probe(self._config.claude_binary)
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
        )


def _provider_payload(providers: dict[object, object], provider: str) -> dict[str, object]:
    value = providers.get(provider)
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


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
    available = probe.available and detected_available is not False
    if available:
        return True, None
    detected_error = _string(capabilities.get("error"))
    return False, probe.error or detected_error or "unavailable"
