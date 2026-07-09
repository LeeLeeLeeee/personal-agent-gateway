from pathlib import Path

import pytest

from personal_agent_gateway.runners.agent import AgentRunner
from personal_agent_gateway.runners.capture import CaptureRunner, _capture_command
from personal_agent_gateway.runners.ffmpeg import FfmpegRunner
from personal_agent_gateway.runtime import RuntimeResult


def test_ffmpeg_extract_audio_builds_safe_command(tmp_path: Path) -> None:
    runner = FfmpegRunner(
        ffmpeg_binary="ffmpeg",
        ffprobe_binary="ffprobe",
        workspace_root=tmp_path,
        temp_dir=tmp_path / "temp",
    )

    command = runner.preview_command(
        "ffmpeg.extract-audio",
        {"source_file": "input.mov", "format": "m4a"},
    )

    assert command[:4] == ["ffmpeg", "-y", "-i", str(tmp_path / "input.mov")]
    assert command[-1].endswith(".m4a")


def test_ffmpeg_rejects_workspace_escape(tmp_path: Path) -> None:
    runner = FfmpegRunner(
        ffmpeg_binary="ffmpeg",
        ffprobe_binary="ffprobe",
        workspace_root=tmp_path / "workspace",
        temp_dir=tmp_path / "temp",
    )

    with pytest.raises(ValueError, match="outside workspace"):
        runner.preview_command(
            "ffmpeg.inspect",
            {"source_file": "../outside.mov"},
        )


def test_capture_screen_builds_command(tmp_path: Path) -> None:
    runner = CaptureRunner(
        capture_binary="screencapture",
        temp_dir=tmp_path / "temp",
    )

    command = runner.preview_command("capture.screen", {})

    assert command[0] == "screencapture"
    assert ".png" in " ".join(command)


def test_capture_command_uses_windows_powershell(tmp_path: Path) -> None:
    output = tmp_path / "screen.png"

    command = _capture_command("powershell", output, "win32")

    assert command[:4] == ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass"]
    assert str(output) in command[-1]


def test_capture_command_uses_macos_screencapture(tmp_path: Path) -> None:
    output = tmp_path / "screen.png"

    command = _capture_command("screencapture", output, "darwin")

    assert command == ["screencapture", "-x", str(output)]


class FakeAgentRuntime:
    def __init__(self, response_text: str) -> None:
        self._response_text = response_text
        self.received_prompt: str | None = None

    async def handle_user_message(self, content: str) -> RuntimeResult:
        self.received_prompt = content
        return RuntimeResult(
            messages=[{"role": "assistant", "content": self._response_text}],
            pending_approval=None,
        )


class FakeAgentRuntimeFactory:
    def __init__(self, response_text: str) -> None:
        self.runtime = FakeAgentRuntime(response_text)

    def create_default_runtime(self) -> FakeAgentRuntime:
        return self.runtime


async def test_agent_runner_runs_one_turn_and_returns_response_as_log() -> None:
    factory = FakeAgentRuntimeFactory("known agent response")
    runner = AgentRunner(factory)

    result = await runner.run("agent.instruct", {"prompt": "hi there"})

    assert factory.runtime.received_prompt == "hi there"
    assert result.exit_code == 0
    assert "known agent response" in result.stdout
    assert result.artifact_paths == []


async def test_agent_runner_requires_prompt() -> None:
    runner = AgentRunner(FakeAgentRuntimeFactory("unused"))

    with pytest.raises(ValueError, match="prompt"):
        await runner.run("agent.instruct", {})
