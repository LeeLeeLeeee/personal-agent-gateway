import asyncio
import sys
from pathlib import Path
from uuid import uuid4

from personal_agent_gateway.runners.base import RunResult


class CaptureRunner:
    def __init__(self, capture_binary: str, temp_dir: Path) -> None:
        self._capture_binary = capture_binary
        self._temp_dir = temp_dir.resolve()

    def preview_command(self, capability_id: str, input_json: dict[str, object]) -> list[str]:
        if capability_id != "capture.screen":
            raise ValueError(f"Unsupported capture capability: {capability_id}")
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        return [self._capture_binary, "-x", str(self._temp_dir / f"screen-{uuid4().hex}.png")]

    async def run(self, capability_id: str, input_json: dict[str, object]) -> RunResult:
        command = self.preview_command(capability_id, input_json)
        if sys.platform != "darwin":
            return RunResult(
                exit_code=1,
                stdout="",
                stderr="capture.screen is only implemented for macOS screencapture",
                artifact_paths=[],
            )
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        output = Path(command[-1])
        return RunResult(
            exit_code=process.returncode or 0,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
            artifact_paths=[output],
        )
