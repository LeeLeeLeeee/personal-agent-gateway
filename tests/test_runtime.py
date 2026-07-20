import json
import sys
from pathlib import Path

import pytest

from personal_agent_gateway.approval import ApprovalStore
from personal_agent_gateway.capabilities import CapabilityRegistry
from personal_agent_gateway.db import Database
from personal_agent_gateway.jobs import JobService
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


class CapturingModel:
    def __init__(self, response: ModelResponse) -> None:
        self.response = response
        self.calls: list[list[dict[str, object]]] = []

    async def complete(self, messages: list[dict[str, object]]) -> ModelResponse:
        self.calls.append(messages)
        return self.response


class SwitchingActiveSessionModel:
    def __init__(self, transcript: TranscriptStore, response: ModelResponse) -> None:
        self.transcript = transcript
        self.response = response

    async def complete(self, _messages: list[dict[str, object]]) -> ModelResponse:
        self.transcript.reset()
        return self.response


def write_file_command(filename: str, content: str) -> str:
    code = (
        "from pathlib import Path; "
        f"Path({filename!r}).write_text({content!r}, encoding='utf-8')"
    )
    return f'"{sys.executable}" -c "{code}"'


def make_runtime(
    tmp_path: Path,
    responses: list[ModelResponse],
    error: Exception | None = None,
    job_service: JobService | None = None,
) -> tuple[AgentRuntime, TranscriptStore, WorkspaceTools, FakeModelClient, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transcript = TranscriptStore(tmp_path / "sessions")
    tools = WorkspaceTools(root=workspace, approvals=ApprovalStore())
    model = FakeModelClient(responses, error=error)
    return AgentRuntime(transcript, tools, model, job_service=job_service), transcript, tools, model, workspace


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
async def test_runtime_prepends_persona_system_prompt_to_every_model_call(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transcript = TranscriptStore(tmp_path / "sessions")
    tools = WorkspaceTools(root=workspace, approvals=ApprovalStore())
    model = FakeModelClient([ModelResponse(content="answer", tool_calls=[])])
    runtime = AgentRuntime(
        transcript,
        tools,
        model,
        history_mode="latest_user",
        system_prompt="Persona: Mail Manager",
    )

    await runtime.handle_user_message("classify this")

    assert model.calls[0][0] == {
        "role": "system",
        "content": "Persona: Mail Manager",
    }


@pytest.mark.asyncio
async def test_runtime_writes_entire_turn_to_starting_session_when_active_changes(tmp_path: Path) -> None:
    transcript = TranscriptStore(tmp_path / "sessions")
    session_id = transcript.start_new()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = AgentRuntime(
        transcript,
        WorkspaceTools(root=workspace, approvals=ApprovalStore()),
        SwitchingActiveSessionModel(transcript, ModelResponse(content="answer for original", tool_calls=[])),
        session_id=session_id,
    )

    await runtime.handle_user_message("question")

    assert [(event.kind, event.payload) for event in transcript.load(session_id)] == [
        ("user", {"content": "question"}),
        ("assistant", {"content": "answer for original"}),
    ]
    active_id = transcript.active_id()
    assert active_id != session_id
    assert transcript.load(active_id or "") == []


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
async def test_shell_run_creates_visible_job_when_job_service_is_configured(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    job_service = JobService(db, CapabilityRegistry.default())
    runtime, _transcript, _tools, _model, _workspace = make_runtime(
        tmp_path,
        [
            ModelResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="shell-call",
                        name="shell.run",
                        arguments={"command": "printf visible"},
                    )
                ],
            )
        ],
        job_service=job_service,
    )

    await runtime.handle_user_message("run command")

    jobs = job_service.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].capability_id == "shell.run"
    assert jobs[0].status == "waiting_approval"
    assert jobs[0].command_preview == "printf visible"


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
    command = write_file_command("restored.txt", "restored")
    runtime, transcript, _tools, _model, workspace = make_runtime(
        tmp_path,
        [
            ModelResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="shell-call",
                        name="shell.run",
                        arguments={"command": command},
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
                "arguments": json.dumps({"command": command}, sort_keys=True),
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
    command = write_file_command("approved.txt", "approved")
    runtime, transcript, _tools, model, workspace = make_runtime(
        tmp_path,
        [
            ModelResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="shell-call",
                        name="shell.run",
                        arguments={"command": command},
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
                "command": command,
                "status": "approved",
            },
        ),
        (
            "tool_result",
            {
                "id": "shell-call",
                "name": "shell.run",
                "command": command,
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
                        "arguments": json.dumps({"command": command}, sort_keys=True),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "shell-call",
            "content": json.dumps(
                {
                    "command": command,
                    "exit_code": 0,
                    "id": "shell-call",
                    "name": "shell.run",
                    "stderr": "",
                    "stdout": "",
                },
                sort_keys=True,
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


@pytest.mark.asyncio
async def test_runtime_records_upstream_session_id_after_model_response(tmp_path: Path) -> None:
    transcript = TranscriptStore(tmp_path / "sessions")
    recorded: list[str] = []
    model = CapturingModel(ModelResponse("hello", [], upstream_session_id="native-1"))
    runtime = AgentRuntime(
        transcript=transcript,
        tools=WorkspaceTools(tmp_path, ApprovalStore()),
        model=model,
        on_upstream_session_id=recorded.append,
    )

    await runtime.handle_user_message("hello")

    assert recorded == ["native-1"]


@pytest.mark.asyncio
async def test_runtime_latest_user_history_mode_preserves_local_tool_loop_context(tmp_path: Path) -> None:
    transcript = TranscriptStore(tmp_path / "sessions")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transcript.start_new()
    transcript.append("user", {"content": "first"})
    transcript.append("assistant", {"content": "first answer"})
    transcript.append("agent_session_link", {"upstream_session_id": "native-old"})
    transcript.append("runtime_error", {"message": "old error"})
    model = FakeModelClient(
        [
            ModelResponse(
                content="",
                tool_calls=[ToolCall(id="list-call", name="fs.list", arguments={"path": "."})],
                upstream_session_id="native-1",
            ),
            ModelResponse(content="second answer", tool_calls=[], upstream_session_id="native-1"),
        ]
    )
    runtime = AgentRuntime(
        transcript=transcript,
        tools=WorkspaceTools(workspace, ApprovalStore()),
        model=model,
        history_mode="latest_user",
    )

    await runtime.handle_user_message("second")

    assert model.calls[0] == [{"role": "user", "content": "second"}]
    assert model.calls[1] == [
        {"role": "user", "content": "second"},
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
            "content": "[]",
        },
    ]
