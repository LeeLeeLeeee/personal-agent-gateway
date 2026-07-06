import asyncio
from pathlib import Path

from personal_agent_gateway.runners.base import RunResult


class ShellRunner:
    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = workspace_root.resolve()

    def preview_command(self, capability_id: str, input_json: dict[str, object]) -> list[str]:
        if capability_id != "shell.run":
            raise ValueError(f"Unsupported shell capability: {capability_id}")
        command = input_json.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command is required")
        return [command]

    async def run(self, capability_id: str, input_json: dict[str, object]) -> RunResult:
        command = self.preview_command(capability_id, input_json)[0]
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=self._workspace_root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        return RunResult(
            exit_code=process.returncode or 0,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
            artifact_paths=[],
        )
