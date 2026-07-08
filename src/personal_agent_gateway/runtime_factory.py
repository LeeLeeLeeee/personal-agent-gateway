from personal_agent_gateway.approval import ApprovalStore
from personal_agent_gateway.config import AppConfig, ConfigError
from personal_agent_gateway.events import EventBus
from personal_agent_gateway.jobs import JobService
from personal_agent_gateway.model_client import CodexModelClient, OpenAIModelClient
from personal_agent_gateway.runtime import AgentRuntime
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

    def _create_runtime_for_app_config(self) -> AgentRuntime:
        config = self._config
        if config.model_provider == "codex":

            async def publish_codex_event(event: dict[str, object]) -> None:
                if self._event_bus is not None:
                    await self._event_bus.publish({"type": "codex.event", **event})

            return self._runtime(
                CodexModelClient(
                    binary=config.codex_binary,
                    model=config.model,
                    workspace_root=config.workspace_root,
                    sandbox=config.codex_sandbox,
                    approval_policy=config.codex_approval_policy,
                    timeout_seconds=config.codex_timeout_seconds,
                    on_event=publish_codex_event,
                )
            )

        if config.model_provider != "openai":
            raise ConfigError(f"Unsupported model provider: {config.model_provider}")
        if not config.openai_api_key:
            raise ConfigError("OPENAI_API_KEY is required when AGENT_MODEL_PROVIDER=openai")

        return self._runtime(OpenAIModelClient(api_key=config.openai_api_key or "", model=config.model))

    def _runtime(self, model) -> AgentRuntime:
        return AgentRuntime(
            transcript=self._transcript,
            tools=WorkspaceTools(self._config.workspace_root, ApprovalStore()),
            model=model,
            job_service=self._job_service,
            event_bus=self._event_bus,
        )
