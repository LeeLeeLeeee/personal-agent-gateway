from pathlib import Path

import pytest

from personal_agent_gateway.runners.capture import CaptureRunner, _capture_command
from personal_agent_gateway.runners.ffmpeg import FfmpegRunner


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
