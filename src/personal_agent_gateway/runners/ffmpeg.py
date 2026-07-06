import asyncio
from pathlib import Path

from personal_agent_gateway.runners.base import RunResult


class FfmpegRunner:
    def __init__(
        self,
        ffmpeg_binary: str,
        ffprobe_binary: str,
        workspace_root: Path,
        temp_dir: Path,
    ) -> None:
        self._ffmpeg_binary = ffmpeg_binary
        self._ffprobe_binary = ffprobe_binary
        self._workspace_root = workspace_root.resolve()
        self._temp_dir = temp_dir.resolve()

    def preview_command(self, capability_id: str, input_json: dict[str, object]) -> list[str]:
        if capability_id == "ffmpeg.inspect":
            source = self._source_path(input_json)
            return [
                self._ffprobe_binary,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(source),
            ]
        if capability_id == "ffmpeg.extract-audio":
            source = self._source_path(input_json)
            output = self._output_path(source, str(input_json.get("format") or "m4a"))
            return [self._ffmpeg_binary, "-y", "-i", str(source), str(output)]
        if capability_id == "ffmpeg.thumbnail":
            source = self._source_path(input_json)
            output = self._output_path(source, "png")
            return [
                self._ffmpeg_binary,
                "-y",
                "-i",
                str(source),
                "-frames:v",
                "1",
                str(output),
            ]
        raise ValueError(f"Unsupported ffmpeg capability: {capability_id}")

    async def run(self, capability_id: str, input_json: dict[str, object]) -> RunResult:
        command = self.preview_command(capability_id, input_json)
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        artifact_paths = _artifact_outputs(command, capability_id)
        return RunResult(
            exit_code=process.returncode or 0,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
            artifact_paths=artifact_paths,
        )

    def _source_path(self, input_json: dict[str, object]) -> Path:
        source_file = input_json.get("source_file")
        if not isinstance(source_file, str) or not source_file.strip():
            raise ValueError("source_file is required")
        source = (self._workspace_root / source_file).resolve()
        try:
            source.relative_to(self._workspace_root)
        except ValueError as exc:
            raise ValueError("source_file is outside workspace") from exc
        return source

    def _output_path(self, source: Path, suffix: str) -> Path:
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        return self._temp_dir / f"{source.stem}.{suffix.lstrip('.')}"


def _artifact_outputs(command: list[str], capability_id: str) -> list[Path]:
    if capability_id in {"ffmpeg.extract-audio", "ffmpeg.thumbnail"}:
        return [Path(command[-1])]
    return []
