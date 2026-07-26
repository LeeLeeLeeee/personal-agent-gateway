import httpx
import pytest

from personal_agent_gateway.config import AppConfig
from personal_agent_gateway.lmg_client import (
    LMGQueryError,
    delete_session,
    fetch_capabilities,
    fetch_sessions,
    fetch_sessions_strict,
)


def _cfg(base="http://lmg", token="local-secret"):
    return AppConfig.from_env(
        {
            "AGENT_WORKSPACE_ROOT": "/ws",
            "AGENT_SESSION_DIR": "/ws/data/sessions",
            "LMG_BASE_URL": base,
            "LMG_LOCAL_TOKEN": token,
        }
    )


def _session_row(**overrides):
    row = {
        "provider": "codex",
        "upstream_id": "session-1",
        "model": "",
        "workspace_root": "",
        "created_at": "2026-07-27T00:00:00Z",
        "last_run_at": "2026-07-27T00:00:00Z",
        "size_bytes": 0,
    }
    row.update(overrides)
    return row


def test_fetch_capabilities_returns_payload():
    payload = {"schema_version": 1, "providers": {"codex": {"available": True, "models": [{"id": "x"}]}}}
    def handler(request):
        assert request.headers["authorization"] == "Bearer local-secret"
        return httpx.Response(200, json=payload)
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
    def handler(request):
        assert request.headers["authorization"] == "Bearer local-secret"
        return httpx.Response(200, json=rows)
    assert fetch_sessions(_cfg(), transport=httpx.MockTransport(handler)) == rows


def test_fetch_sessions_empty_on_http_error():
    def handler(request): return httpx.Response(500)
    assert fetch_sessions(_cfg(), transport=httpx.MockTransport(handler)) == []


def test_fetch_sessions_empty_on_non_list():
    def handler(request): return httpx.Response(200, json={"oops": 1})
    assert fetch_sessions(_cfg(), transport=httpx.MockTransport(handler)) == []


def test_fetch_sessions_strict_preserves_valid_empty_list():
    def handler(request): return httpx.Response(200, json=[])

    assert fetch_sessions_strict(
        _cfg(),
        transport=httpx.MockTransport(handler),
    ) == []


def test_fetch_sessions_strict_accepts_typed_session_rows():
    rows = [
        _session_row(
            consumer=None,
            consumer_session_id="chat-1",
        )
    ]

    def handler(request):
        return httpx.Response(200, json=rows)

    assert fetch_sessions_strict(
        _cfg(),
        transport=httpx.MockTransport(handler),
    ) == rows


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401),
        httpx.Response(503),
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json={"sessions": []}),
        httpx.Response(200, json=[None]),
    ],
)
def test_fetch_sessions_strict_raises_typed_error(response):
    def handler(request): return response

    with pytest.raises(LMGQueryError):
        fetch_sessions_strict(_cfg(), transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    "missing_field",
    [
        "provider",
        "upstream_id",
        "model",
        "workspace_root",
        "created_at",
        "last_run_at",
        "size_bytes",
    ],
)
def test_fetch_sessions_strict_rejects_missing_required_fields(missing_field):
    row = _session_row()
    row.pop(missing_field)

    def handler(request):
        return httpx.Response(200, json=[row])

    with pytest.raises(LMGQueryError):
        fetch_sessions_strict(_cfg(), transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", ""),
        ("provider", " "),
        ("provider", 1),
        ("upstream_id", ""),
        ("upstream_id", None),
        ("model", None),
        ("workspace_root", []),
        ("created_at", 1),
        ("last_run_at", {}),
        ("consumer", 1),
        ("consumer_session_id", []),
        ("storage_path", 1),
        ("size_bytes", "large"),
        ("size_bytes", True),
        ("size_bytes", None),
        ("size_bytes", -1),
    ],
)
def test_fetch_sessions_strict_rejects_invalid_session_field_types(field, value):
    row = _session_row(**{field: value})

    def handler(request):
        return httpx.Response(200, json=[row])

    with pytest.raises(LMGQueryError):
        fetch_sessions_strict(_cfg(), transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("second_provider", ["codex", "claude"])
def test_fetch_sessions_strict_rejects_duplicate_global_upstream_ids(
    second_provider,
):
    rows = [
        _session_row(),
        _session_row(provider=second_provider),
    ]

    def handler(request):
        return httpx.Response(200, json=rows)

    with pytest.raises(LMGQueryError):
        fetch_sessions_strict(_cfg(), transport=httpx.MockTransport(handler))


def test_fetch_sessions_strict_raises_on_network_error():
    def handler(request):
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(LMGQueryError):
        fetch_sessions_strict(_cfg(), transport=httpx.MockTransport(handler))


def test_delete_session_calls_encoded_lmg_session_url():
    captured = {}

    def handler(request):
        captured["method"] = request.method
        captured["path"] = request.url.raw_path
        captured["authorization"] = request.headers["authorization"]
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
        "authorization": "Bearer local-secret",
    }


def test_lmg_requests_omit_authorization_when_direct_config_has_no_token():
    config = AppConfig(
        workspace_root="/ws",
        session_dir="/ws/data/sessions",
        lmg_local_token=None,
    )

    def handler(request):
        assert "authorization" not in request.headers
        return httpx.Response(
            200,
            json={"schema_version": 1, "providers": {}},
        )

    assert fetch_capabilities(
        config,
        transport=httpx.MockTransport(handler),
    ) == {"schema_version": 1, "providers": {}}


def test_delete_session_returns_false_on_http_error():
    def handler(request): return httpx.Response(500)

    assert delete_session(
        _cfg(),
        "s1",
        transport=httpx.MockTransport(handler),
    ) is False


def test_delete_session_accepts_legacy_not_found_as_success():
    def handler(request): return httpx.Response(404)

    assert delete_session(
        _cfg(),
        "already-gone",
        transport=httpx.MockTransport(handler),
    ) is True
