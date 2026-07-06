from pathlib import Path

import pytest

from personal_agent_gateway.runners.capture import CaptureRunner
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

    assert command[0:2] == ["screencapture", "-x"]
    assert command[-1].endswith(".png")
