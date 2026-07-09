from dataclasses import dataclass
from typing import Literal, Self


RiskLevel = Literal["low", "medium", "high"]


class CapabilityValidationError(Exception):
    pass


@dataclass(frozen=True)
class Capability:
    id: str
    title: str
    description: str
    category: str
    risk_level: RiskLevel
    required_inputs: tuple[str, ...]
    output_types: tuple[str, ...]
    requires_approval: bool
    runner_type: str
    enabled: bool = True


class CapabilityRegistry:
    def __init__(self, capabilities: list[Capability]) -> None:
        self._capabilities = {capability.id: capability for capability in capabilities}

    @classmethod
    def default(cls) -> Self:
        return cls(
            [
                Capability(
                    id="shell.run",
                    title="Run Shell Command",
                    description="Run an approved shell command in the configured workspace.",
                    category="System",
                    risk_level="high",
                    required_inputs=("command",),
                    output_types=("log",),
                    requires_approval=True,
                    runner_type="shell",
                ),
                Capability(
                    id="ffmpeg.inspect",
                    title="Inspect Media",
                    description="Read local media metadata with ffprobe.",
                    category="Media",
                    risk_level="low",
                    required_inputs=("source_file",),
                    output_types=("json", "log"),
                    requires_approval=False,
                    runner_type="ffmpeg",
                ),
                Capability(
                    id="ffmpeg.extract-audio",
                    title="Extract Audio",
                    description="Extract an audio artifact from a local video or media file.",
                    category="Media",
                    risk_level="medium",
                    required_inputs=("source_file", "format"),
                    output_types=("audio",),
                    requires_approval=True,
                    runner_type="ffmpeg",
                ),
                Capability(
                    id="ffmpeg.thumbnail",
                    title="Create Thumbnail",
                    description="Create an image thumbnail from a local video file.",
                    category="Media",
                    risk_level="medium",
                    required_inputs=("source_file",),
                    output_types=("image",),
                    requires_approval=True,
                    runner_type="ffmpeg",
                ),
                Capability(
                    id="capture.screen",
                    title="Capture Screen",
                    description="Capture the local screen and save it as an image artifact.",
                    category="Capture",
                    risk_level="medium",
                    required_inputs=(),
                    output_types=("image",),
                    requires_approval=True,
                    runner_type="capture",
                ),
                Capability(
                    id="agent.instruct",
                    title="Instruct Agent",
                    description="Send a saved instruction to the local agent on a schedule.",
                    category="Agent",
                    risk_level="medium",
                    required_inputs=("prompt",),
                    output_types=("log",),
                    requires_approval=False,
                    runner_type="agent",
                ),
            ]
        )

    def list(self) -> list[Capability]:
        return sorted(self._capabilities.values(), key=lambda capability: capability.id)

    def get(self, capability_id: str) -> Capability:
        capability = self._capabilities.get(capability_id)
        if capability is None:
            raise CapabilityValidationError(f"Unknown capability: {capability_id}")
        return capability

    def validate_input(self, capability_id: str, payload: dict[str, object]) -> None:
        capability = self.get(capability_id)
        for input_name in capability.required_inputs:
            value = payload.get(input_name)
            if value is None:
                raise CapabilityValidationError(f"Missing required input: {input_name}")
            if isinstance(value, str) and not value.strip():
                raise CapabilityValidationError(f"Missing required input: {input_name}")
