import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from personal_agent_gateway.config import AppConfig

AgentId = Literal["codex", "claude"]


class AgentOption(BaseModel):
    name: str
    kind: str
    choices: list[str] = []
    required: bool = False


class AgentDescriptor(BaseModel):
    id: AgentId
    label: str
    kind: Literal["local_cli"] = "local_cli"
    binary: str
    available: bool
    availability_error: str | None = None
    models: list[str]
    default_model: str
    options_schema: list[AgentOption]
    defaults: dict[str, Any]


@dataclass(frozen=True)
class CliProbeResult:
    available: bool
    error: str | None


Probe = Callable[[str], CliProbeResult]


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
    def __init__(self, config: AppConfig, probe: Probe | None = None) -> None:
        self._config = config
        self._probe = probe or probe_cli

    def catalog(self) -> list[AgentDescriptor]:
        return [self._codex(), self._claude()]

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
        return {"agent_id": descriptor.id, "model": model, "options": dict(options)}

    def _codex(self) -> AgentDescriptor:
        probe = self._probe(self._config.codex_binary)
        return AgentDescriptor(
            id="codex",
            label="Codex CLI",
            binary=self._config.codex_binary,
            available=probe.available,
            availability_error=probe.error,
            models=["default"],
            default_model="default",
            options_schema=[
                AgentOption(
                    name="sandbox",
                    kind="select",
                    choices=["read-only", "workspace-write", "danger-full-access"],
                ),
                AgentOption(
                    name="approval_policy",
                    kind="select",
                    choices=["untrusted", "on-request", "never"],
                ),
                AgentOption(name="profile", kind="text"),
            ],
            defaults={
                "sandbox": self._config.codex_sandbox,
                "approval_policy": self._config.codex_approval_policy,
            },
        )

    def _claude(self) -> AgentDescriptor:
        probe = self._probe(self._config.claude_binary)
        return AgentDescriptor(
            id="claude",
            label="Claude Code",
            binary=self._config.claude_binary,
            available=probe.available,
            availability_error=probe.error,
            models=["sonnet", "opus"],
            default_model="sonnet",
            options_schema=[
                AgentOption(
                    name="effort",
                    kind="select",
                    choices=["low", "medium", "high", "xhigh", "max"],
                ),
                AgentOption(
                    name="permission_mode",
                    kind="select",
                    choices=[
                        "acceptEdits",
                        "auto",
                        "bypassPermissions",
                        "manual",
                        "dontAsk",
                        "plan",
                    ],
                ),
                AgentOption(name="agent", kind="text"),
            ],
            defaults={"effort": "medium", "permission_mode": "manual"},
        )
