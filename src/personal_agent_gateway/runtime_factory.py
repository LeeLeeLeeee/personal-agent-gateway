from pathlib import Path

from personal_agent_gateway.approval import ApprovalStore
from personal_agent_gateway.agent_session_link import AgentSessionLinkService
from personal_agent_gateway.config import AppConfig, ConfigError
from personal_agent_gateway.events import EventBus
from personal_agent_gateway.jobs import JobService
from personal_agent_gateway.personas import persona_system_prompt
from personal_agent_gateway.remote_model_client import HttpModelClient
from personal_agent_gateway.runtime import AgentRuntime
from personal_agent_gateway.session_config import SessionAgentConfigService
from personal_agent_gateway.space_policies import SpacePolicyService
from personal_agent_gateway.tools import WorkspaceTools
from personal_agent_gateway.transcript import TranscriptStore


class AgentRuntimeFactory:
    def __init__(
        self,
        config: AppConfig,
        transcript: TranscriptStore,
        job_service: JobService | None = None,
        event_bus: EventBus | None = None,
        space_policies: SpacePolicyService | None = None,
    ) -> None:
        self._config = config
        self._transcript = transcript
        self._job_service = job_service
        self._event_bus = event_bus
        self._space_policies = space_policies

    def create_default_runtime(self) -> AgentRuntime:
        return self._create_runtime_for_app_config()

    def create_headless_runtime(
        self,
        backend: str,
        model: str,
        options: dict[str, object],
        *,
        hook_run_id: str,
        system_prompt: str | None = None,
        persona_id: str | None = None,
    ) -> AgentRuntime:
        workspace_root, read_roots, write_mode = self._space_context(persona_id)
        if backend == "claude":
            client = self._remote_client(
                "claude", model,
                self._claude_execution(workspace_root, read_roots, write_mode, options),
            )
            session_id = self._transcript.start_new(
                origin="hook",
                hook_run_id=hook_run_id,
                activate=False,
            )
            return self._runtime(
                client,
                session_id=session_id,
                system_prompt=system_prompt,
                workspace_root=workspace_root,
                read_roots=read_roots,
            )
        if backend == "codex":
            client = self._remote_client(
                "codex", model,
                self._codex_execution(workspace_root, read_roots, write_mode, options),
            )
            session_id = self._transcript.start_new(
                origin="hook",
                hook_run_id=hook_run_id,
                activate=False,
            )
            return self._runtime(
                client,
                session_id=session_id,
                system_prompt=system_prompt,
                workspace_root=workspace_root,
                read_roots=read_roots,
            )
        raise ConfigError(f"Unsupported hook backend: {backend}")

    def create_runtime_for_active_session(self) -> AgentRuntime:
        session_id = self._transcript.active_id()
        if session_id is None:
            return self._create_runtime_for_app_config()

        return self._create_runtime_for_session_id(session_id)

    def create_runtime_for_session(self, session_id: str) -> AgentRuntime:
        return self._create_runtime_for_session_id(session_id)

    def _create_runtime_for_session_id(self, session_id: str) -> AgentRuntime:
        events = self._transcript.load(session_id)
        has_explicit_session_config = any(event.kind == "session_config_set" for event in events)
        if not has_explicit_session_config and self._config.model_provider != "codex":
            return self._create_runtime_for_app_config(session_id=session_id)

        session_config = SessionAgentConfigService(self._transcript).effective_config(session_id)
        workspace_root, read_roots, write_mode = self._space_context(session_config.persona_id)
        agent_id, model, options = self._effective_session_runtime_config(session_config)
        system_prompt = persona_system_prompt(session_config.persona_snapshot)
        link_service = AgentSessionLinkService(self._transcript)
        link = link_service.latest(
            session_id=session_id,
            agent_id=agent_id,
            model=model,
            options=options,
        )
        history_mode = "latest_user" if link is not None else "full"

        def record_upstream_session(upstream_session_id: str) -> None:
            link_service.record(
                session_id=session_id,
                agent_id=agent_id,
                model=model,
                options=options,
                upstream_session_id=upstream_session_id,
            )

        if agent_id == "codex":

            async def publish_codex_event(event: dict[str, object]) -> None:
                if self._event_bus is not None:
                    await self._event_bus.publish({"type": "codex.event", **event, "session_id": session_id})

            return self._runtime(
                self._remote_client(
                    "codex", model,
                    self._codex_execution(workspace_root, read_roots, write_mode, options),
                    on_event=publish_codex_event,
                    upstream_session_id=link.upstream_session_id if link is not None else None,
                ),
                history_mode=history_mode,
                on_upstream_session_id=record_upstream_session,
                session_id=session_id,
                system_prompt=system_prompt,
                workspace_root=workspace_root,
                read_roots=read_roots,
            )

        if agent_id == "claude":
            return self._runtime(
                self._remote_client(
                    "claude", model,
                    self._claude_execution(workspace_root, read_roots, write_mode, options),
                    upstream_session_id=link.upstream_session_id if link is not None else None,
                ),
                history_mode=history_mode,
                on_upstream_session_id=record_upstream_session,
                session_id=session_id,
                system_prompt=system_prompt,
                workspace_root=workspace_root,
                read_roots=read_roots,
            )

        raise ConfigError(f"Unsupported session agent: {agent_id}")

    def _create_runtime_for_app_config(self, session_id: str | None = None) -> AgentRuntime:
        config = self._config
        workspace_root, read_roots, write_mode = self._space_context(None)
        effective_session_id = session_id if session_id is not None else self._transcript.active_id()
        if config.model_provider == "codex":

            async def publish_codex_event(event: dict[str, object]) -> None:
                if self._event_bus is not None:
                    await self._event_bus.publish(
                        {"type": "codex.event", **event, "session_id": effective_session_id}
                    )

            return self._runtime(
                self._remote_client(
                    "codex", config.model,
                    self._codex_execution(workspace_root, read_roots, write_mode, {}),
                    on_event=publish_codex_event,
                ),
                session_id=effective_session_id,
                workspace_root=workspace_root,
                read_roots=read_roots,
            )

        if config.model_provider != "openai":
            raise ConfigError(f"Unsupported model provider: {config.model_provider}")
        if not config.openai_api_key:
            raise ConfigError("OPENAI_API_KEY is required when AGENT_MODEL_PROVIDER=openai")

        return self._runtime(
            self._remote_client(
                "openai", config.model,
                {"workspace_root": str(workspace_root)},
            ),
            session_id=effective_session_id,
            workspace_root=workspace_root,
            read_roots=read_roots,
        )

    def _codex_execution(self, workspace_root, read_roots, write_mode, options) -> dict[str, object]:
        return {
            "workspace_root": str(workspace_root),
            "read_roots": [str(p) for p in (read_roots or [])],
            "sandbox": (
                "danger-full-access" if write_mode == "full_access"
                else "workspace-write" if write_mode is not None
                else str(options.get("sandbox") or self._config.codex_sandbox)
            ),
            "approval_policy": str(options.get("approval_policy") or self._config.codex_approval_policy),
            "effort": str(options.get("effort") or "high"),
            "profile": str(options["profile"]) if options.get("profile") else "",
        }

    def _claude_execution(self, workspace_root, read_roots, write_mode, options) -> dict[str, object]:
        return {
            "workspace_root": str(workspace_root),
            "read_roots": [str(p) for p in (read_roots or [])],
            "permission_mode": (
                "bypassPermissions" if write_mode == "full_access"
                else self._config.claude_permission_mode if write_mode is not None
                else str(options.get("permission_mode") or self._config.claude_permission_mode)
            ),
            "effort": str(options.get("effort") or "high"),
            "agent": str(options["agent"]) if options.get("agent") else "",
        }

    def _remote_client(self, provider, model, execution, *, on_event=None, upstream_session_id=None) -> HttpModelClient:
        return HttpModelClient(
            base_url=self._config.lmg_base_url,
            provider=provider,
            model=model,
            execution=execution,
            on_event=on_event,
            upstream_session_id=upstream_session_id,
            timeout_seconds=self._config.codex_timeout_seconds,
            idle_timeout_seconds=self._config.codex_idle_timeout_seconds,
        )

    def _runtime(
        self,
        model,
        history_mode: str = "full",
        on_upstream_session_id=None,
        session_id: str | None = None,
        system_prompt: str | None = None,
        workspace_root: Path | None = None,
        read_roots: list[Path] | None = None,
    ) -> AgentRuntime:
        return AgentRuntime(
            transcript=self._transcript,
            tools=WorkspaceTools(
                workspace_root or self._config.workspace_root,
                ApprovalStore(),
                read_roots=read_roots,
            ),
            model=model,
            job_service=self._job_service,
            event_bus=self._event_bus,
            history_mode=history_mode,
            on_upstream_session_id=on_upstream_session_id,
            session_id=session_id,
            system_prompt=system_prompt,
        )

    def _space_context(self, persona_id: str | None) -> tuple[Path, list[Path], str | None]:
        if self._space_policies is None:
            return self._config.workspace_root, [], None
        effective = self._space_policies.resolve(persona_id=persona_id)
        policy = effective.policy
        workspace_root = (
            Path(policy.workspace_path).resolve()
            if policy.write_mode == "full_access" and policy.workspace_path
            else self._config.workspace_root
        )
        read_roots = [Path(policy.read_path).resolve()] if policy.read_path else []
        return workspace_root, read_roots, policy.write_mode

    def _effective_session_runtime_config(self, session_config) -> tuple[str, str, dict[str, object]]:
        if session_config.agent_id == "codex":
            return self._effective_codex_session_runtime_config(session_config)
        if session_config.agent_id == "claude":
            return self._effective_claude_session_runtime_config(session_config)
        raise ConfigError(f"Unsupported session agent: {session_config.agent_id}")

    def _effective_codex_session_runtime_config(self, session_config) -> tuple[str, str, dict[str, object]]:
        if session_config.source == "default":
            return (
                "codex",
                self._config.model,
                {
                    "effort": "high",
                    "sandbox": self._config.codex_sandbox,
                    "approval_policy": self._config.codex_approval_policy,
                },
            )
        options = dict(session_config.options)
        effective_options: dict[str, object] = {
            "effort": str(options.get("effort") or "high"),
            "sandbox": str(options.get("sandbox") or self._config.codex_sandbox),
            "approval_policy": str(options.get("approval_policy") or self._config.codex_approval_policy),
        }
        if options.get("profile"):
            effective_options["profile"] = str(options["profile"])
        return ("codex", session_config.model, effective_options)

    def _effective_claude_session_runtime_config(self, session_config) -> tuple[str, str, dict[str, object]]:
        options = dict(session_config.options)
        effective_options: dict[str, object] = {
            "effort": str(options.get("effort") or "medium"),
            "permission_mode": str(options.get("permission_mode") or "manual"),
        }
        if options.get("agent"):
            effective_options["agent"] = str(options["agent"])
        return ("claude", session_config.model, effective_options)
