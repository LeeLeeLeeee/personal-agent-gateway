from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    artifact_paths: list[Path]


class Runner(Protocol):
    def preview_command(self, capability_id: str, input_json: dict[str, object]) -> list[str]:
        pass

    async def run(self, capability_id: str, input_json: dict[str, object]) -> RunResult:
        pass
