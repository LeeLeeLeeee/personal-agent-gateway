from pathlib import Path

import pytest

from personal_agent_gateway.approval import ApprovalStore
from personal_agent_gateway.model_client import ModelResponse, ToolCall
from personal_agent_gateway.runtime import AgentRuntime
from personal_agent_gateway.tools import WorkspaceTools
from personal_agent_gateway.transcript import TranscriptStore


class FakeModelClient:
    def __init__(self, responses: list[ModelResponse], error: Exception | None = None) -> None:
        self.responses = responses
        self.error = error
        self.calls: list[list[dict[str, object]]] = []

    async def complete(self, messages: list[dict[str, object]]) -> ModelResponse:
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return self.responses.pop(0)


def make_runtime(
    tmp_path: Path,
    responses: list[ModelResponse],
    error: Exception | None = None,
) -> tuple[AgentRuntime, TranscriptStore, WorkspaceTools, FakeModelClient, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transcript = TranscriptStore(tmp_path / "sessions")
    tools = WorkspaceTools(root=workspace, approvals=ApprovalStore())
    model = FakeModelClient(responses, error=error)
    return AgentRuntime(transcript, tools, model), transcript, tools, model, workspace


def event_payloads(transcript: TranscriptStore) -> list[tuple[str, dict[str, object]]]:
    return [(event.kind, event.payload) for event in transcript.load_active()]


@pytest.mark.asyncio
async def test_user_input_is_appended_as_user_event(tmp_path: Path) -> None:
    runtime, transcript, _tools, _model, _workspace = make_runtime(
        tmp_path,
        [ModelResponse(content="hello", tool_calls=[])],
    )

    await runtime.handle_user_message("hi")

    assert event_payloads(transcript)[0] == ("user", {"content": "hi"})


@pytest.mark.asyncio
async def test_plain_assistant_output_is_appended_as_assistant_event(tmp_path: Path) -> None:
    runtime, transcript, _tools, _model, _workspace = make_runtime(
        tmp_path,
        [ModelResponse(content="plain answer", tool_calls=[])],
    )

    result = await runtime.handle_user_message("question")

    assert result.messages == [{"role": "assistant", "content": "plain answer"}]
    assert event_payloads(transcript)[-1] == ("assistant", {"content": "plain answer"})


@pytest.mark.asyncio
async def test_fs_tools_execute_and_append_request_and_result_events(tmp_path: Path) -> None:
    runtime, transcript, _tools, model, workspace = make_runtime(
        tmp_path,
        [
            ModelResponse(
                content="",
                tool_calls=[
                    ToolCall(id="list-call", name="fs.list", arguments={"path": "."}),
                    ToolCall(id="read-call", name="fs.read", arguments={"path": "note.txt"}),
                ],
            ),
            ModelResponse(content="read complete", tool_calls=[]),
        ],
    )
    (workspace / "note.txt").write_text("note body", encoding="utf-8")

    result = await runtime.handle_user_message("inspect files")

    assert result.pending_approval is None
    assert result.messages == [{"role": "assistant", "content": "read complete"}]
    assert event_payloads(transcript) == [
        ("user", {"content": "inspect files"}),
        (
            "tool_request",
            {"id": "list-call", "name": "fs.list", "arguments": {"path": "."}},
        ),
        (
            "tool_result",
            {"id": "list-call", "name": "fs.list", "result": ["note.txt"]},
        ),
        (
            "tool_request",
            {"id": "read-call", "name": "fs.read", "arguments": {"path": "note.txt"}},
        ),
        (
            "tool_result",
            {"id": "read-call", "name": "fs.read", "result": "note body"},
        ),
        ("assistant", {"content": "read complete"}),
    ]
    assert model.calls[1] == [
        {"role": "user", "content": "inspect files"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "list-call",
                    "type": "function",
                    "function": {"name": "fs.list", "arguments": '{"path": "."}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "list-call",
            "content": '["note.txt"]',
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "read-call",
                    "type": "function",
                    "function": {"name": "fs.read", "arguments": '{"path": "note.txt"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "read-call",
            "content": '"note body"',
        },
    ]


@pytest.mark.asyncio
async def test_shell_run_requests_approval_without_executing(tmp_path: Path) -> None:
    runtime, transcript, _tools, _model, workspace = make_runtime(
        tmp_path,
        [
            ModelResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="shell-call",
                        name="shell.run",
                        arguments={"command": "printf ran > ran.txt"},
                    )
                ],
            )
        ],
    )

    result = await runtime.handle_user_message("run command")

    assert result.pending_approval is not None
    assert result.pending_approval["command"] == "printf ran > ran.txt"
    assert event_payloads(transcript) == [
        ("user", {"content": "run command"}),
        (
            "tool_request",
            {
                "id": "shell-call",
                "name": "shell.run",
                "arguments": {"command": "printf ran > ran.txt"},
                "approval_id": result.pending_approval["id"],
            },
        ),
    ]
    assert not (workspace / "ran.txt").exists()


@pytest.mark.asyncio
async def test_new_user_message_does_not_call_model_while_shell_approval_is_pending(
    tmp_path: Path,
) -> None:
    runtime, transcript, _tools, model, _workspace = make_runtime(
        tmp_path,
        [
            ModelResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="shell-call",
                        name="shell.run",
                        arguments={"command": "printf pending"},
                    )
                ],
            ),
        ],
    )
    pending = await runtime.handle_user_message("run command")

    result = await runtime.handle_user_message("new input")

    assert result.messages == []
    assert result.pending_approval == pending.pending_approval
    assert len(model.calls) == 1
    assert event_payloads(transcript) == [
        ("user", {"content": "run command"}),
        (
            "tool_request",
            {
                "id": "shell-call",
                "name": "shell.run",
                "arguments": {"command": "printf pending"},
                "approval_id": pending.pending_approval["id"],
            },
        ),
    ]


@pytest.mark.asyncio
async def test_restarted_runtime_can_approve_pending_shell_request(tmp_path: Path) -> None:
    runtime, transcript, _tools, _model, workspace = make_runtime(
        tmp_path,
        [
            ModelResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="shell-call",
                        name="shell.run",
                        arguments={"command": "printf restored > restored.txt"},
                    )
                ],
            )
        ],
    )
    pending = await runtime.handle_user_message("run command")
    restarted_tools = WorkspaceTools(root=workspace, approvals=ApprovalStore())
    restarted_model = FakeModelClient([ModelResponse(content="restored done", tool_calls=[])])
    restarted_runtime = AgentRuntime(transcript, restarted_tools, restarted_model)

    result = await restarted_runtime.approve(str(pending.pending_approval["id"]))

    assert (workspace / "restored.txt").read_text(encoding="utf-8") == "restored"
    assert result.messages == [{"role": "assistant", "content": "restored done"}]
    assert restarted_model.calls[0][1]["tool_calls"] == [
        {
            "id": "shell-call",
            "type": "function",
            "function": {
                "name": "shell.run",
                "arguments": '{"command": "printf restored > restored.txt"}',
            },
        }
    ]
    assert restarted_model.calls[0][2]["tool_call_id"] == "shell-call"


@pytest.mark.asyncio
async def test_approval_without_active_pending_request_is_rejected_without_execution(
    tmp_path: Path,
) -> None:
    runtime, transcript, _tools, _model, workspace = make_runtime(
        tmp_path,
        [
            ModelResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="shell-call",
                        name="shell.run",
                        arguments={"command": "printf stale > stale.txt"},
                    )
                ],
            )
        ],
    )
    pending = await runtime.handle_user_message("run command")
    transcript.reset()

    result = await runtime.approve(str(pending.pending_approval["id"]))

    assert not (workspace / "stale.txt").exists()
    assert result.messages == [
        {
            "role": "assistant",
            "content": f"Error: No pending approval: {pending.pending_approval['id']}",
        }
    ]


@pytest.mark.asyncio
async def test_approving_shell_request_appends_result_and_resumes_model(
    tmp_path: Path,
) -> None:
    runtime, transcript, _tools, model, workspace = make_runtime(
        tmp_path,
        [
            ModelResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="shell-call",
                        name="shell.run",
                        arguments={"command": "printf approved > approved.txt"},
                    )
                ],
            ),
            ModelResponse(content="command finished", tool_calls=[]),
        ],
    )
    pending = await runtime.handle_user_message("run command")

    result = await runtime.approve(str(pending.pending_approval["id"]))

    assert (workspace / "approved.txt").read_text(encoding="utf-8") == "approved"
    assert result.messages == [{"role": "assistant", "content": "command finished"}]
    assert event_payloads(transcript)[-3:] == [
        (
            "approval",
            {
                "id": pending.pending_approval["id"],
                "command": "printf approved > approved.txt",
                "status": "approved",
            },
        ),
        (
            "tool_result",
            {
                "id": "shell-call",
                "name": "shell.run",
                "command": "printf approved > approved.txt",
                "exit_code": 0,
                "stdout": "",
                "stderr": "",
            },
        ),
        ("assistant", {"content": "command finished"}),
    ]
    assert model.calls[1] == [
        {"role": "user", "content": "run command"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "shell-call",
                    "type": "function",
                    "function": {
                        "name": "shell.run",
                        "arguments": '{"command": "printf approved > approved.txt"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "shell-call",
            "content": (
                '{"command": "printf approved > approved.txt", "exit_code": 0, '
                '"id": "shell-call", "name": "shell.run", "stderr": "", "stdout": ""}'
            ),
        },
    ]


@pytest.mark.asyncio
async def test_denying_shell_request_appends_tool_denial(tmp_path: Path) -> None:
    runtime, transcript, _tools, model, workspace = make_runtime(
        tmp_path,
        [
            ModelResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="shell-call",
                        name="shell.run",
                        arguments={"command": "printf denied > denied.txt"},
                    )
                ],
            )
        ],
    )
    pending = await runtime.handle_user_message("run command")

    result = await runtime.deny(str(pending.pending_approval["id"]))

    assert result.messages == [{"role": "assistant", "content": "Command denied."}]
    assert result.pending_approval is None
    assert len(model.calls) == 1
    assert event_payloads(transcript)[-1] == (
        "tool_denial",
        {
            "id": "shell-call",
            "command": "printf denied > denied.txt",
            "status": "denied",
        },
    )
    assert not (workspace / "denied.txt").exists()


@pytest.mark.asyncio
async def test_denied_shell_request_replays_with_original_tool_call_id(tmp_path: Path) -> None:
    runtime, _transcript, _tools, model, _workspace = make_runtime(
        tmp_path,
        [
            ModelResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="shell-call",
                        name="shell.run",
                        arguments={"command": "printf denied"},
                    )
                ],
            ),
            ModelResponse(content="next answer", tool_calls=[]),
        ],
    )
    pending = await runtime.handle_user_message("run command")
    await runtime.deny(str(pending.pending_approval["id"]))

    await runtime.handle_user_message("next")

    assert model.calls[1] == [
        {"role": "user", "content": "run command"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "shell-call",
                    "type": "function",
                    "function": {
                        "name": "shell.run",
                        "arguments": '{"command": "printf denied"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "shell-call",
            "content": "denied",
        },
        {"role": "user", "content": "next"},
    ]


@pytest.mark.asyncio
async def test_runtime_errors_append_runtime_error_and_return_message(tmp_path: Path) -> None:
    runtime, transcript, _tools, _model, _workspace = make_runtime(
        tmp_path,
        [],
        error=RuntimeError("model unavailable"),
    )

    result = await runtime.handle_user_message("hello")

    assert result.messages == [{"role": "assistant", "content": "Error: model unavailable"}]
    assert result.pending_approval is None
    assert event_payloads(transcript)[-1] == (
        "runtime_error",
        {"message": "model unavailable"},
    )
