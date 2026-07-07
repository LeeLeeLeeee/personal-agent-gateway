import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import personal_agent_gateway.app as app_module
from personal_agent_gateway.app import _sse_events, create_app, main
from personal_agent_gateway.approval import ApprovalStore
from personal_agent_gateway.auth_store import AuthStore
from personal_agent_gateway.config import AppConfig
from personal_agent_gateway.events import EventBus
from personal_agent_gateway.model_client import ModelResponse, ToolCall
from personal_agent_gateway.runtime import AgentRuntime, RuntimeResult
from personal_agent_gateway.tools import WorkspaceTools
from personal_agent_gateway.transcript import TranscriptStore


class FakeRuntime:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.approved: list[str] = []
        self.denied: list[str] = []

    async def handle_user_message(self, content: str) -> RuntimeResult:
        self.messages.append(content)
        return RuntimeResult(
            messages=[{"role": "assistant", "content": f"reply: {content}"}],
            pending_approval={"id": "approval-1", "command": "printf ok"},
        )

    async def approve(self, approval_id: str) -> RuntimeResult:
        self.approved.append(approval_id)
        return RuntimeResult(
            messages=[{"role": "assistant", "content": f"approved {approval_id}"}],
            pending_approval=None,
        )

    async def deny(self, approval_id: str) -> RuntimeResult:
        self.denied.append(approval_id)
        return RuntimeResult(
            messages=[{"role": "assistant", "content": f"denied {approval_id}"}],
            pending_approval=None,
        )


class BrokenRuntime:
    async def handle_user_message(self, _content: str) -> RuntimeResult:
        raise RuntimeError("stack trace details")

    async def approve(self, _approval_id: str) -> RuntimeResult:
        raise RuntimeError("stack trace details")

    async def deny(self, _approval_id: str) -> RuntimeResult:
        raise RuntimeError("stack trace details")


class FakeModelClient:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = responses

    async def complete(self, messages: list[dict[str, object]]) -> ModelResponse:
        return self.responses.pop(0)


def write_file_command(filename: str, content: str) -> str:
    code = (
        "from pathlib import Path; "
        f"Path({filename!r}).write_text({content!r}, encoding='utf-8')"
    )
    return f'"{sys.executable}" -c "{code}"'


def make_config(tmp_path: Path) -> AppConfig:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return AppConfig(
        web_token="secret-token",
        workspace_root=workspace,
        session_dir=tmp_path / "sessions",
        openai_api_key="test-key",
    )


def auth_client(config: AppConfig, runtime: AgentRuntime | FakeRuntime) -> TestClient:
    client = TestClient(create_app(config=config, runtime=runtime))
    client.cookies.set("agent_session", "test-session")
    return client


def test_browser_shell_is_public_but_data_routes_require_session(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    client = TestClient(create_app(config=config, runtime=FakeRuntime()))

    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/api/history").status_code == 401
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/sessions").status_code == 401
    assert client.get("/api/sessions/search?q=hello").status_code == 401
    assert client.post("/api/sessions/session-1/activate").status_code == 401
    assert client.delete("/api/sessions/session-1").status_code == 401
    assert client.post("/api/chat", json={"message": "hello"}).status_code == 401
    assert client.post("/api/reset").status_code == 401
    assert client.post("/api/approvals/approval-1/approve").status_code == 401
    assert client.post("/api/approvals/approval-1/deny").status_code == 401
    assert client.get("/api/events").status_code == 401


class DisconnectAfterEventRequest:
    def __init__(self) -> None:
        self.calls = 0

    async def is_disconnected(self) -> bool:
        self.calls += 1
        return self.calls > 1


async def test_sse_events_formats_published_events() -> None:
    bus = EventBus()
    await bus.publish({"type": "runtime.started"})
    subscriber = bus.subscribe()
    chunks: list[str] = []

    async for chunk in _sse_events(DisconnectAfterEventRequest(), bus, subscriber):
        chunks.append(chunk)

    assert chunks == [
        ": connected\n\n",
        'id: 1\ndata: {"id": 1, "type": "runtime.started"}\n\n',
    ]


def test_query_token_is_not_required_to_load_browser_shell(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    client = TestClient(create_app(config=config, runtime=FakeRuntime()))

    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert response.cookies.get("agent_web_token") is None
    assert client.get("/api/history").status_code == 401
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200


def test_ui_assets_smoke(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    client = auth_client(config, FakeRuntime())

    page = client.get("/")
    script = client.get("/static/app.js")

    # UI is JS-rendered (frontend redesign): served HTML is a #app skeleton;
    # behavior lives in app.js. Frontend-owned smoke assertions.
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert 'id="app"' in page.text
    assert script.status_code == 200
    assert "text/javascript" in script.headers["content-type"]
    assert "renderShell" in script.text
    assert "/api/status" in script.text
    assert "EventSource" in script.text
    assert "/api/events" in script.text
    assert "state.timeline" in script.text
    assert "renderTimeline" in script.text


def test_rename_session_sets_custom_title(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = TranscriptStore(config.session_dir)
    transcript_id = store.start_new()
    store.append("user", {"content": "first message here"})
    client = auth_client(config, FakeRuntime())

    response = client.post(f"/api/sessions/{transcript_id}/title", json={"title": "My renamed chat"})

    assert response.status_code == 200
    assert response.json() == {"session_id": transcript_id, "title": "My renamed chat"}
    sessions = client.get("/api/sessions").json()["sessions"]
    assert any(s["id"] == transcript_id and s["title"] == "My renamed chat" for s in sessions)


def test_rename_session_rejects_empty_and_unknown(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    client = auth_client(config, FakeRuntime())

    assert client.post("/api/sessions/whatever/title", json={"title": "  "}).status_code == 400
    assert client.post("/api/sessions/missing/title", json={"title": "x"}).status_code == 404


def test_status_returns_safe_runtime_metadata(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    client = auth_client(config, FakeRuntime())

    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json() == {
        "provider": "codex",
        "model": "default",
        "workspace_root": str(config.workspace_root),
        "session_id": None,
        "message_count": 0,
        "pending_approval": False,
        "session_status": "idle",
        "cookie_secure": False,
    }
    assert "secret-token" not in response.text


def test_status_reports_active_session_after_real_runtime_message(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    runtime = AgentRuntime(
        TranscriptStore(config.session_dir),
        WorkspaceTools(config.workspace_root, ApprovalStore()),
        FakeModelClient([ModelResponse(content="stored answer", tool_calls=[])]),
    )
    client = auth_client(config, runtime)

    client.post("/api/chat", json={"message": "remember this"})
    response = client.get("/api/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"]
    assert payload["message_count"] == 2
    assert payload["session_status"] == "idle"


def test_chat_records_runtime_events_for_sse_subscribers(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    runtime = AgentRuntime(
        TranscriptStore(config.session_dir),
        WorkspaceTools(config.workspace_root, ApprovalStore()),
        FakeModelClient([ModelResponse(content="stored answer", tool_calls=[])]),
    )
    client = auth_client(config, runtime)

    response = client.post("/api/chat", json={"message": "remember this"})

    assert response.status_code == 200
    recent = client.app.state.event_bus.recent()
    assert [event["type"] for event in recent] == [
        "runtime.user_message.started",
        "runtime.completed",
    ]
    assert recent[0]["message"] == "remember this"
    assert recent[1]["pending_approval"] is None


def test_sessions_api_lists_activate_delete_and_searches_sessions(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = TranscriptStore(config.session_dir)
    first_id = store.start_new()
    store.append("user", {"content": "billing regression"})
    second_id = store.reset()
    store.append("user", {"content": "frontend polish"})
    client = auth_client(config, FakeRuntime())

    list_response = client.get("/api/sessions")
    sessions = list_response.json()["sessions"]

    assert list_response.status_code == 200
    assert [session["id"] for session in sessions] == [second_id, first_id]
    assert sessions[0]["title"] == "frontend polish"
    assert sessions[0]["is_active"] is True
    assert sessions[0]["status"] == "idle"

    activate_response = client.post(f"/api/sessions/{first_id}/activate")

    assert activate_response.status_code == 200
    assert activate_response.json()["session_id"] == first_id
    assert client.get("/api/history").json()["events"][0]["payload"] == {
        "content": "billing regression"
    }

    search_response = client.get("/api/sessions/search?q=billing")

    assert search_response.status_code == 200
    assert [session["id"] for session in search_response.json()["sessions"]] == [first_id]

    delete_response = client.delete(f"/api/sessions/{first_id}")

    assert delete_response.status_code == 200
    assert delete_response.json() == {"deleted": True, "active_session_id": None}
    assert client.get("/api/sessions").json()["sessions"][0]["id"] == second_id


def test_activate_missing_session_returns_404(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    client = auth_client(config, FakeRuntime())

    response = client.post("/api/sessions/missing/activate")

    assert response.status_code == 404
    assert response.json() == {"detail": "Session not found"}


def test_chat_returns_runtime_output(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    runtime = FakeRuntime()
    client = auth_client(config, runtime)

    response = client.post("/api/chat", json={"message": "hello"})

    assert response.status_code == 200
    assert response.json() == {
        "messages": [{"role": "assistant", "content": "reply: hello"}],
        "pending_approval": {"id": "approval-1", "command": "printf ok"},
    }
    assert runtime.messages == ["hello"]


def test_chat_requires_otp_session_after_totp_is_configured(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    auth_store = AuthStore(config.auth_dir)
    setup = auth_store.start_totp_setup("local-owner")
    auth_store.verify_totp_setup(auth_store.current_code_for_test(setup.secret))
    client = TestClient(create_app(config=config, runtime=FakeRuntime()))

    response = client.post("/api/chat", json={"message": "hello"})

    assert response.status_code == 401


def test_app_reuses_one_runtime_instance(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    runtime = FakeRuntime()
    client = auth_client(config, runtime)

    assert client.post("/api/chat", json={"message": "one"}).status_code == 200
    assert client.post("/api/chat", json={"message": "two"}).status_code == 200

    assert runtime.messages == ["one", "two"]


def test_history_returns_restored_transcript_after_app_recreation(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    first_runtime = AgentRuntime(
        TranscriptStore(config.session_dir),
        WorkspaceTools(config.workspace_root, ApprovalStore()),
        FakeModelClient([ModelResponse(content="stored answer", tool_calls=[])]),
    )
    first_client = auth_client(config, first_runtime)
    first_client.post("/api/chat", json={"message": "remember this"})
    second_runtime = AgentRuntime(
        TranscriptStore(config.session_dir),
        WorkspaceTools(config.workspace_root, ApprovalStore()),
        FakeModelClient([]),
    )

    second_client = auth_client(config, second_runtime)
    response = second_client.get("/api/history")

    assert response.status_code == 200
    events = response.json()["events"]
    assert [(event["kind"], event["payload"]) for event in events] == [
        ("user", {"content": "remember this"}),
        ("assistant", {"content": "stored answer"}),
    ]


def test_reset_returns_empty_events_and_resets_history(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    TranscriptStore(config.session_dir).append("user", {"content": "old"})
    client = auth_client(config, FakeRuntime())

    assert client.get("/api/history").json()["events"]

    response = client.post("/api/reset")

    assert response.status_code == 200
    assert response.json()["events"] == []
    assert response.json()["session_id"]
    assert client.get("/api/history").json() == {"events": []}


def test_reset_invalidates_real_runtime_pending_approval(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    runtime = AgentRuntime(
        TranscriptStore(config.session_dir),
        WorkspaceTools(config.workspace_root, ApprovalStore()),
        FakeModelClient(
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
            ]
        ),
    )
    client = auth_client(config, runtime)
    pending = client.post("/api/chat", json={"message": "run it"}).json()["pending_approval"]

    reset_payload = client.post("/api/reset").json()
    assert reset_payload["events"] == []
    assert reset_payload["session_id"]
    response = client.post(f"/api/approvals/{pending['id']}/approve")

    assert response.status_code == 200
    assert response.json() == {
        "messages": [
            {
                "role": "assistant",
                "content": f"Error: No pending approval: {pending['id']}",
            }
        ],
        "pending_approval": None,
    }
    assert not (config.workspace_root / "stale.txt").exists()


def test_approve_resumes_execution(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    runtime = FakeRuntime()
    client = auth_client(config, runtime)

    response = client.post("/api/approvals/approval-1/approve")

    assert response.status_code == 200
    assert response.json() == {
        "messages": [{"role": "assistant", "content": "approved approval-1"}],
        "pending_approval": None,
    }
    assert runtime.approved == ["approval-1"]


def test_deny_records_denial(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    runtime = FakeRuntime()
    client = auth_client(config, runtime)

    response = client.post("/api/approvals/approval-1/deny")

    assert response.status_code == 200
    assert response.json() == {
        "messages": [{"role": "assistant", "content": "denied approval-1"}],
        "pending_approval": None,
    }
    assert runtime.denied == ["approval-1"]


def test_real_runtime_approve_resumes_execution(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    command = write_file_command("ran.txt", "ran")
    runtime = AgentRuntime(
        TranscriptStore(config.session_dir),
        WorkspaceTools(config.workspace_root, ApprovalStore()),
        FakeModelClient(
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
                ModelResponse(content="done", tool_calls=[]),
            ]
        ),
    )
    client = auth_client(config, runtime)
    pending = client.post("/api/chat", json={"message": "run it"}).json()["pending_approval"]

    response = client.post(f"/api/approvals/{pending['id']}/approve")

    assert response.status_code == 200
    assert response.json() == {
        "messages": [{"role": "assistant", "content": "done"}],
        "pending_approval": None,
    }
    assert (config.workspace_root / "ran.txt").read_text(encoding="utf-8") == "ran"


def test_real_runtime_deny_records_denial(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    runtime = AgentRuntime(
        TranscriptStore(config.session_dir),
        WorkspaceTools(config.workspace_root, ApprovalStore()),
        FakeModelClient(
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
            ]
        ),
    )
    client = auth_client(config, runtime)
    pending = client.post("/api/chat", json={"message": "run it"}).json()["pending_approval"]

    response = client.post(f"/api/approvals/{pending['id']}/deny")

    assert response.status_code == 200
    assert response.json() == {
        "messages": [{"role": "assistant", "content": "Command denied."}],
        "pending_approval": None,
    }
    assert not (config.workspace_root / "denied.txt").exists()
    events = client.get("/api/history").json()["events"]
    assert events[-1]["kind"] == "tool_denial"
    assert events[-1]["payload"] == {
        "id": "shell-call",
        "command": "printf denied > denied.txt",
        "status": "denied",
    }


def test_runtime_errors_return_json_without_stack_trace(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    client = TestClient(
        create_app(config=config, runtime=BrokenRuntime()),
        raise_server_exceptions=False,
    )
    client.cookies.set("agent_session", "test-session")

    response = client.post("/api/chat", json={"message": "boom"})

    assert response.status_code == 500
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"detail": "Internal Server Error"}
    assert "stack trace details" not in response.text


def test_main_loads_config_and_runs_uvicorn_with_configured_host_port(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = make_config(tmp_path)
    calls: list[dict[str, object]] = []

    def fake_run(application: object, host: str, port: int) -> None:
        calls.append({"application": application, "host": host, "port": port})

    monkeypatch.setattr(app_module, "load_config", lambda: config)
    monkeypatch.setattr(app_module.uvicorn, "run", fake_run)

    main()

    assert len(calls) == 1
    assert isinstance(calls[0]["application"], FastAPI)
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 8787
