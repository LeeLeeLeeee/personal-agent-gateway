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
        return _capture_command(
            self._capture_binary,
            self._temp_dir / f"screen-{uuid4().hex}.png",
            sys.platform,
        )

    async def run(self, capability_id: str, input_json: dict[str, object]) -> RunResult:
        command = self.preview_command(capability_id, input_json)
        if sys.platform not in {"darwin", "win32"}:
            return RunResult(
                exit_code=1,
                stdout="",
                stderr="capture.screen is only implemented for macOS and Windows",
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


def _capture_command(capture_binary: str, output: Path, platform_name: str) -> list[str]:
    if platform_name == "darwin":
        return [capture_binary, "-x", str(output)]
    if platform_name == "win32":
        return [
            capture_binary,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            _windows_capture_script(output),
        ]
    return [capture_binary, str(output)]


def _windows_capture_script(output: Path) -> str:
    escaped_output = str(output).replace("'", "''")
    return "; ".join(
        [
            "Add-Type -AssemblyName System.Windows.Forms",
            "Add-Type -AssemblyName System.Drawing",
            "$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds",
            "$bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height",
            "$graphics = [System.Drawing.Graphics]::FromImage($bitmap)",
            "$graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)",
            f"$bitmap.Save('{escaped_output}', [System.Drawing.Imaging.ImageFormat]::Png)",
            "$graphics.Dispose()",
            "$bitmap.Dispose()",
        ]
    )
