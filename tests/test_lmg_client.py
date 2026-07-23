import httpx
from personal_agent_gateway.config import AppConfig
from personal_agent_gateway.lmg_client import fetch_capabilities


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
