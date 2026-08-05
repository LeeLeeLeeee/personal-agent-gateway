from pathlib import Path

from personal_agent_gateway.agent_session_link import (
    AgentSessionContext,
    AgentSessionLinkService,
)
from personal_agent_gateway.agents import AgentRegistry
from personal_agent_gateway.approval import ApprovalStore
from personal_agent_gateway.archive import ArchiveService
from personal_agent_gateway.config import AppConfig, ConfigError
from personal_agent_gateway.events import EventBus
from personal_agent_gateway.execution_contract import (
    CompiledExecution,
    ExecutionContractError,
    ExecutionRequirements,
    compile_execution,
)
from personal_agent_gateway.jobs import JobService
from personal_agent_gateway.personas import persona_system_prompt
from personal_agent_gateway.remote_model_client import HttpModelClient
from personal_agent_gateway.runtime import AgentRuntime
from personal_agent_gateway.session_config import SessionAgentConfigService
from personal_agent_gateway.source_staging import SourceStager, StagedInputs
from personal_agent_gateway.space_policies import SpacePolicy, SpacePolicyService
from personal_agent_gateway.tools import WorkspaceTools
from personal_agent_gateway.transcript import TranscriptStore


class ExecutionContextFactory:
    def __init__(self, stager: SourceStager | None = None) -> None:
        self._stager = stager or SourceStager()
        self._cache: dict[tuple[object, ...], CompiledExecution] = {}
        self._staged_inputs: dict[tuple[tuple[Path, ...], Path], StagedInputs] = {}

    def for_session(
        self,
        policy: SpacePolicy,
        capabilities,
        consumer_workspace: Path,
        *,
        network: str = "unspecified",
        permission_mode: str = "",
    ) -> CompiledExecution:
        if policy.write_mode == "isolated":
            try:
                consumer_workspace.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise ExecutionContractError(
                    "invalid_execution_path",
                    "Failed to prepare isolated workspace",
                ) from exc
        source_roots = (
            (Path(policy.read_path).resolve(),)
            if policy.read_mode == "selected" and policy.read_path
            else ()
        )
        workspace_root = (
            Path(policy.workspace_path).resolve()
            if policy.write_mode == "full_access" and policy.workspace_path
            else consumer_workspace.resolve()
        )
        key = (
            policy.read_mode,
            policy.read_path,
            policy.write_mode,
            policy.workspace_path,
            capabilities,
            workspace_root,
            network,
            permission_mode,
        )
        cached = self._cache.get(key)
        if cached is not None:
            if cached.input_manifest_path is not None:
                self._stager.verify(
                    StagedInputs(
                        manifest_path=cached.input_manifest_path,
                        manifest_sha256=cached.input_manifest_sha256 or "",
                        read_roots=cached.read_roots,
                    )
                )
            return cached
        compiled = compile_execution(
            ExecutionRequirements(
                source_roots=source_roots,
                requires_sources=policy.read_mode != "none",
                workspace_mode=policy.write_mode,
                workspace_root=workspace_root,
                network=network,
                permission_mode=permission_mode,
            ),
            policy,
            capabilities,
            self,
        )
        self._cache[key] = compiled
        return compiled

    def stage(self, roots: tuple[Path, ...], workspace_root: Path) -> StagedInputs:
        key = (roots, workspace_root)
        staged = self._staged_inputs.get(key)
        if staged is not None:
            self._stager.verify(staged)
            return staged
        staged = self._stager.stage(roots, workspace_root)
        self._staged_inputs[key] = staged
        return staged

    def staged_inputs_for(self, workspace_root: Path) -> StagedInputs | None:
        resolved = workspace_root.resolve()
        for (_roots, staged_workspace), staged in reversed(
            self._staged_inputs.items()
        ):
            if staged_workspace.resolve() == resolved:
                return staged
        return None

    @staticmethod
    def wire_execution(
        compiled: CompiledExecution,
        provider: str,
        options: dict[str, object],
    ) -> dict[str, object]:
        execution: dict[str, object] = {
            "workspace_root": str(compiled.workspace_root),
            "read_roots": [str(path) for path in compiled.read_roots],
            "sandbox": compiled.sandbox,
            "approval_policy": compiled.approval_policy,
            "permission_mode": compiled.permission_mode,
            "network": compiled.network,
        }
        execution["effort"] = str(options.get("effort") or "high")
        if provider == "codex":
            execution["profile"] = str(options.get("profile") or "")
        elif provider == "claude":
            execution["agent"] = str(options.get("agent") or "")
        return execution


class AgentRuntimeFactory:
    def __init__(
        self,
        config: AppConfig,
        transcript: TranscriptStore,
        job_service: JobService | None = None,
        event_bus: EventBus | None = None,
        space_policies: SpacePolicyService | None = None,
        archive_service: ArchiveService | None = None,
        agent_registry: AgentRegistry | None = None,
        execution_contexts: ExecutionContextFactory | None = None,
    ) -> None:
        self._config = config
        self._transcript = transcript
        self._job_service = job_service
        self._event_bus = event_bus
        self._space_policies = space_policies
        self._archive_service = archive_service
        self._agent_registry = agent_registry or AgentRegistry(config)
        self._execution_contexts = execution_contexts or ExecutionContextFactory()

    def create_default_runtime(self) -> AgentRuntime:
        return self._create_runtime_for_app_config()

    @property
    def execution_contexts(self) -> ExecutionContextFactory:
        return self._execution_contexts

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
        if backend not in {"codex", "claude"}:
            raise ConfigError(f"Unsupported hook backend: {backend}")
        session_id = self._transcript.start_new(
            origin="hook",
            hook_run_id=hook_run_id,
            activate=False,
        )
        compiled, execution = self._compiled_execution(
            backend,
            persona_id,
            session_id,
            options,
        )
        client = self._remote_client(backend, model, execution)
        return self._runtime(
            client,
            session_id=session_id,
            system_prompt=system_prompt,
            persona_id=persona_id,
            workspace_root=compiled.workspace_root,
            read_roots=list(compiled.read_roots),
        )

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
        system_prompt = persona_system_prompt(session_config.persona_snapshot)
        compiled, execution = self._compiled_execution(
            agent_id,
            session_config.persona_id,
            session_id,
            options,
        )
        context = AgentSessionContext(
            agent_id=agent_id,
            model=model,
            execution=execution,
            persona_id=session_config.persona_id,
            persona_snapshot=session_config.persona_snapshot,
            system_prompt=system_prompt,
        )
        link_service = AgentSessionLinkService(self._transcript)
        link = link_service.latest(session_id, context)
        history_mode = "latest_user" if link is not None else "full"
        publish_model_event = self._chat_model_event_callback(
            session_id,
            link_service,
            context,
        )

        if agent_id == "codex":
            return self._runtime(
                self._remote_client(
                    "codex", model,
                    execution,
                    on_event=publish_model_event,
                    upstream_session_id=link.upstream_session_id if link is not None else None,
                    consumer_session_id=session_id,
                    consumer_context_fingerprint=context.fingerprint(),
                ),
                history_mode=history_mode,
                session_id=session_id,
                system_prompt=system_prompt,
                persona_id=session_config.persona_id,
                workspace_root=compiled.workspace_root,
                read_roots=list(compiled.read_roots),
            )

        if agent_id == "claude":
            return self._runtime(
                self._remote_client(
                    "claude", model,
                    execution,
                    on_event=publish_model_event,
                    upstream_session_id=link.upstream_session_id if link is not None else None,
                    consumer_session_id=session_id,
                    consumer_context_fingerprint=context.fingerprint(),
                ),
                history_mode=history_mode,
                session_id=session_id,
                system_prompt=system_prompt,
                persona_id=session_config.persona_id,
                workspace_root=compiled.workspace_root,
                read_roots=list(compiled.read_roots),
            )

        raise ConfigError(f"Unsupported session agent: {agent_id}")

    def _create_runtime_for_app_config(self, session_id: str | None = None) -> AgentRuntime:
        config = self._config
        effective_session_id = session_id if session_id is not None else self._transcript.active_id()
        if config.model_provider == "codex":
            compiled, execution = self._compiled_execution(
                "codex",
                None,
                effective_session_id,
                {},
            )
            return self._create_app_remote_runtime(
                "codex",
                config.model,
                execution,
                effective_session_id,
                compiled.workspace_root,
                list(compiled.read_roots),
            )

        if config.model_provider != "openai":
            raise ConfigError(f"Unsupported model provider: {config.model_provider}")
        if not config.openai_api_key:
            raise ConfigError("OPENAI_API_KEY is required when AGENT_MODEL_PROVIDER=openai")

        return self._create_app_remote_runtime(
            "openai",
            config.model,
            {"workspace_root": str(config.workspace_root)},
            effective_session_id,
            config.workspace_root,
            [],
        )

    def _create_app_remote_runtime(
        self,
        provider: str,
        model: str,
        execution: dict[str, object],
        session_id: str | None,
        workspace_root: Path,
        read_roots: list[Path],
    ) -> AgentRuntime:
        context = AgentSessionContext(
            agent_id=provider,
            model=model,
            execution=execution,
            persona_id=None,
            persona_snapshot=None,
            system_prompt=None,
        )
        link_service = AgentSessionLinkService(self._transcript)
        is_chat_session = (
            session_id is not None
            and self._transcript.session_origin(session_id) == "chat"
        )
        link = (
            link_service.latest(session_id, context)
            if is_chat_session and session_id is not None
            else None
        )
        on_event = (
            self._chat_model_event_callback(session_id, link_service, context)
            if is_chat_session and session_id is not None
            else self._model_event_callback(session_id)
        )
        return self._runtime(
            self._remote_client(
                provider,
                model,
                execution,
                on_event=on_event,
                upstream_session_id=link.upstream_session_id if link is not None else None,
                consumer_session_id=session_id if is_chat_session else None,
                consumer_context_fingerprint=(
                    context.fingerprint() if is_chat_session else None
                ),
            ),
            history_mode="latest_user" if link is not None else "full",
            session_id=session_id,
            workspace_root=workspace_root,
            read_roots=read_roots,
        )

    def _chat_model_event_callback(
        self,
        session_id: str,
        link_service: AgentSessionLinkService,
        context: AgentSessionContext,
    ):
        async def on_event(event: dict[str, object]) -> None:
            upstream_session_id = event.get("upstream_session_id")
            if (
                event.get("kind") == "session.updated"
                and isinstance(upstream_session_id, str)
                and upstream_session_id
            ):
                link_service.record(session_id, context, upstream_session_id)
            if self._event_bus is not None:
                await self._event_bus.publish(
                    {"type": "model.event", **event, "session_id": session_id}
                )

        return on_event

    def _model_event_callback(self, session_id: str | None):
        async def on_event(event: dict[str, object]) -> None:
            if self._event_bus is not None:
                await self._event_bus.publish(
                    {"type": "model.event", **event, "session_id": session_id}
                )

        return on_event

    def _remote_client(
        self,
        provider,
        model,
        execution,
        *,
        on_event=None,
        upstream_session_id=None,
        consumer_session_id: str | None = None,
        consumer_context_fingerprint: str | None = None,
    ) -> HttpModelClient:
        return HttpModelClient(
            base_url=self._config.lmg_base_url,
            provider=provider,
            model=model,
            execution=execution,
            on_event=on_event,
            upstream_session_id=upstream_session_id,
            local_token=self._config.lmg_local_token,
            consumer="personal-agent-gateway",
            consumer_session_id=consumer_session_id,
            consumer_context_fingerprint=consumer_context_fingerprint,
            timeout_seconds=self._config.codex_timeout_seconds,
            idle_timeout_seconds=self._config.codex_idle_timeout_seconds,
        )

    def _runtime(
        self,
        model,
        history_mode: str = "full",
        session_id: str | None = None,
        system_prompt: str | None = None,
        persona_id: str | None = None,
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
            session_id=session_id,
            system_prompt=system_prompt,
            archive_service=self._archive_service,
            persona_id=persona_id,
        )

    def _compiled_execution(
        self,
        provider: str,
        persona_id: str | None,
        session_id: str | None,
        options: dict[str, object],
        *,
        network: str = "unspecified",
    ) -> tuple[CompiledExecution, dict[str, object]]:
        descriptor = self._agent_registry.get(provider)
        capabilities = descriptor.execution_capabilities
        if not descriptor.available or capabilities is None:
            raise ExecutionContractError(
                "provider_not_ready",
                "The selected provider has no usable execution capability snapshot",
            )
        policy = (
            self._space_policies.resolve(persona_id=persona_id).policy
            if self._space_policies is not None
            else _default_space_policy()
        )
        consumer_workspace = (
            self._config.session_dir / "runs" / session_id / "workspace"
            if session_id is not None
            else self._config.workspace_root
        )
        compiled = self._execution_contexts.for_session(
            policy,
            capabilities,
            consumer_workspace,
            network=network,
        )
        return (
            compiled,
            self._execution_contexts.wire_execution(compiled, provider, options),
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


def _default_space_policy() -> SpacePolicy:
    return SpacePolicy(
        scope="global",
        scope_id="",
        read_mode="none",
        read_path=None,
        write_mode="isolated",
        workspace_path=None,
        created_at="",
        updated_at="",
    )
