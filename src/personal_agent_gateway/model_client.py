from dataclasses import dataclass
from typing import Literal, Protocol

ToolName = Literal["fs.list", "fs.read", "shell.run"]
WIRE_TOOL_NAMES: dict[ToolName, str] = {
    "fs.list": "fs_list",
    "fs.read": "fs_read",
    "shell.run": "shell_run",
}
INTERNAL_TOOL_NAMES = {wire_name: tool_name for tool_name, wire_name in WIRE_TOOL_NAMES.items()}


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: ToolName
    arguments: dict[str, object]


@dataclass(frozen=True)
class ModelResponse:
    content: str
    tool_calls: list[ToolCall]
    upstream_session_id: str | None = None


class ModelClient(Protocol):
    async def complete(self, messages: list[dict[str, object]]) -> ModelResponse:
        pass
