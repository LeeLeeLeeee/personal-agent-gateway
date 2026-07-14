from personal_agent_gateway.approval import ApprovalStore
from personal_agent_gateway.agent_session_link import AgentSessionLinkService
from personal_agent_gateway.config import AppConfig, ConfigError
from personal_agent_gateway.events import EventBus
from personal_agent_gateway.jobs import JobService
from personal_agent_gateway.model_client import ClaudeModelClient, CodexModelClient, OpenAIModelClient
from personal_agent_gateway.runtime import AgentRuntime
from personal_agent_gateway.session_config import SessionAgentConfigService
from personal_agent_gateway.tools import WorkspaceTools
from personal_agent_gateway.transcript import TranscriptStore


class AgentRuntimeFactory:
    def __init__(
        self,
        config: AppConfig,
        transcript: TranscriptStore,
        job_service: JobService | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._config = config
        self._transcript = transcript
        self._job_service = job_service
        self._event_bus = event_bus

    def create_default_runtime(self) -> AgentRuntime:
        return self._create_runtime_for_app_config()

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
        agent_id, model, options = self._effective_session_runtime_config(session_config)
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
                CodexModelClient(
                    binary=self._config.codex_binary,
                    model=model,
                    workspace_root=self._config.workspace_root,
                    sandbox=str(options["sandbox"]),
                    approval_policy=str(options["approval_policy"]),
                    profile=str(options["profile"]) if options.get("profile") else None,
                    effort=str(options["effort"]) if options.get("effort") else None,
                    upstream_session_id=link.upstream_session_id if link is not None else None,
                    timeout_seconds=self._config.codex_timeout_seconds,
                    idle_timeout_seconds=self._config.codex_idle_timeout_seconds,
                    on_event=publish_codex_event,
                ),
                history_mode=history_mode,
                on_upstream_session_id=record_upstream_session,
                session_id=session_id,
            )

        if agent_id == "claude":
            return self._runtime(
                ClaudeModelClient(
                    binary=self._config.claude_binary,
                    model=model,
                    workspace_root=self._config.workspace_root,
                    effort=str(options["effort"]),
                    permission_mode=str(options["permission_mode"]),
                    agent=str(options["agent"]) if options.get("agent") else None,
                    upstream_session_id=link.upstream_session_id if link is not None else None,
                    timeout_seconds=self._config.codex_timeout_seconds,
                ),
                history_mode=history_mode,
                on_upstream_session_id=record_upstream_session,
                session_id=session_id,
            )

        raise ConfigError(f"Unsupported session agent: {agent_id}")

    def _create_runtime_for_app_config(self, session_id: str | None = None) -> AgentRuntime:
        config = self._config
        effective_session_id = session_id if session_id is not None else self._transcript.active_id()
        if config.model_provider == "codex":

            async def publish_codex_event(event: dict[str, object]) -> None:
                if self._event_bus is not None:
                    await self._event_bus.publish(
                        {"type": "codex.event", **event, "session_id": effective_session_id}
                    )

            return self._runtime(
                CodexModelClient(
                    binary=config.codex_binary,
                    model=config.model,
                    workspace_root=config.workspace_root,
                    sandbox=config.codex_sandbox,
                    approval_policy=config.codex_approval_policy,
                    effort="high",
                    timeout_seconds=config.codex_timeout_seconds,
                    idle_timeout_seconds=config.codex_idle_timeout_seconds,
                    on_event=publish_codex_event,
                ),
                session_id=effective_session_id,
            )

        if config.model_provider != "openai":
            raise ConfigError(f"Unsupported model provider: {config.model_provider}")
        if not config.openai_api_key:
            raise ConfigError("OPENAI_API_KEY is required when AGENT_MODEL_PROVIDER=openai")

        return self._runtime(
            OpenAIModelClient(api_key=config.openai_api_key or "", model=config.model),
            session_id=effective_session_id,
        )

    def _runtime(
        self,
        model,
        history_mode: str = "full",
        on_upstream_session_id=None,
        session_id: str | None = None,
    ) -> AgentRuntime:
        return AgentRuntime(
            transcript=self._transcript,
            tools=WorkspaceTools(self._config.workspace_root, ApprovalStore()),
            model=model,
            job_service=self._job_service,
            event_bus=self._event_bus,
            history_mode=history_mode,
            on_upstream_session_id=on_upstream_session_id,
            session_id=session_id,
        )

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
