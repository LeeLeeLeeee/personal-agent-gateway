import httpx
from personal_agent_gateway.config import AppConfig
from personal_agent_gateway.lmg_client import delete_session, fetch_capabilities, fetch_sessions


def _cfg(base="http://lmg"):
    return AppConfig.from_env({"AGENT_WORKSPACE_ROOT": "/ws", "AGENT_SESSION_DIR": "/ws/data/sessions", "LMG_BASE_URL": base})


def test_fetch_capabilities_returns_payload():
    payload = {"schema_version": 1, "providers": {"codex": {"available": True, "models": [{"id": "x"}]}}}
    def handler(request): return httpx.Response(200, json=payload)
    got = fetch_capabilities(_cfg(), transport=httpx.MockTransport(handler))
    assert got == payload


def test_fetch_capabilities_none_on_bad_schema():
    def handler(request): return httpx.Response(200, json={"schema_version": 2, "providers": {}})
    assert fetch_capabilities(_cfg(), transport=httpx.MockTransport(handler)) is None


def test_fetch_capabilities_none_on_http_error():
    def handler(request): return httpx.Response(500)
    assert fetch_capabilities(_cfg(), transport=httpx.MockTransport(handler)) is None


def test_fetch_sessions_returns_list():
    rows = [{"upstream_id": "s1", "provider": "codex", "model": "default",
             "size_bytes": 100, "created_at": "t", "last_run_at": "t", "storage_path": "/p"}]
    def handler(request): return httpx.Response(200, json=rows)
    assert fetch_sessions(_cfg(), transport=httpx.MockTransport(handler)) == rows


def test_fetch_sessions_empty_on_http_error():
    def handler(request): return httpx.Response(500)
    assert fetch_sessions(_cfg(), transport=httpx.MockTransport(handler)) == []


def test_fetch_sessions_empty_on_non_list():
    def handler(request): return httpx.Response(200, json={"oops": 1})
    assert fetch_sessions(_cfg(), transport=httpx.MockTransport(handler)) == []


def test_delete_session_calls_encoded_lmg_session_url():
    captured = {}

    def handler(request):
        captured["method"] = request.method
        captured["path"] = request.url.raw_path
        return httpx.Response(204)

    deleted = delete_session(
        _cfg(),
        "claude/session id",
        transport=httpx.MockTransport(handler),
    )

    assert deleted is True
    assert captured == {
        "method": "DELETE",
        "path": b"/v1/sessions/claude%2Fsession%20id",
    }


def test_delete_session_returns_false_on_http_error():
    def handler(request): return httpx.Response(500)

    assert delete_session(
        _cfg(),
        "s1",
        transport=httpx.MockTransport(handler),
    ) is False
