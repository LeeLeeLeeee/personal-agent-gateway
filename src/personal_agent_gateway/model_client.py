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
    #: 프로바이더가 보고한 토큰 사용량. None 은 "보고하지 않음" 이고 0 과
    #: 다르다 -- 합계를 내는 쪽이 None 을 0 으로 다루면 보고하지 않는
    #: 프로바이더가 섞일 때마다 총합이 조용히 낮아진다.
    usage: dict[str, int] | None = None


class ModelClient(Protocol):
    async def complete(self, messages: list[dict[str, object]]) -> ModelResponse:
        pass
