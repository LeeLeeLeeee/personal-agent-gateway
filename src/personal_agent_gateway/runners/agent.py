from personal_agent_gateway.runners.base import RunResult
from personal_agent_gateway.runtime_factory import AgentRuntimeFactory


class AgentRunner:
    def __init__(self, runtime_factory: AgentRuntimeFactory) -> None:
        self._runtime_factory = runtime_factory

    def preview_command(self, capability_id: str, input_json: dict[str, object]) -> list[str]:
        if capability_id != "agent.instruct":
            raise ValueError(f"Unsupported agent capability: {capability_id}")
        prompt = input_json.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt is required")
        return [prompt]

    async def run(self, capability_id: str, input_json: dict[str, object]) -> RunResult:
        prompt = self.preview_command(capability_id, input_json)[0]
        runtime = self._runtime_factory.create_default_runtime()
        result = await runtime.handle_user_message(prompt)
        response_text = "\n".join(
            str(message["content"])
            for message in result.messages
            if message.get("content")
        )
        if result.pending_approval is not None:
            # Scheduled/headless runs cannot answer a mid-turn tool approval, so
            # report failure instead of a misleading empty "succeeded".
            return RunResult(
                exit_code=1,
                stdout=response_text,
                stderr="Agent turn paused awaiting tool approval; scheduled runs cannot approve tool calls.",
                artifact_paths=[],
            )
        return RunResult(
            exit_code=0,
            stdout=response_text,
            stderr="",
            artifact_paths=[],
        )
