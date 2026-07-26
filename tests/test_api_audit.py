from pathlib import Path

from fastapi.testclient import TestClient

from personal_agent_gateway.agent_session_link import (
    AgentSessionContext,
    AgentSessionLinkService,
)
from personal_agent_gateway.app import create_app
from personal_agent_gateway.config import AppConfig
from personal_agent_gateway.lmg_client import LMGQueryError


def _config(tmp_path: Path) -> AppConfig:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return AppConfig(
        web_token="secret-token",
        workspace_root=workspace,
        session_dir=tmp_path / "sessions",
        lmg_local_token="local-secret",
    )


def _authenticated_client(tmp_path: Path) -> TestClient:
    client = TestClient(create_app(_config(tmp_path)))
    client.cookies.set(
        "agent_session",
        client.app.state.auth_session_service.issue().token,
    )
    return client


def test_session_consistency_requires_authentication(tmp_path: Path) -> None:
    client = TestClient(create_app(_config(tmp_path)))

    assert client.get("/api/audit/session-consistency").status_code == 401


def test_session_consistency_returns_report_counts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _authenticated_client(tmp_path)
    transcript = client.app.state.transcript_store
    session_id = transcript.start_new()
    context = AgentSessionContext("codex", "default", {}, None, None, None)
    AgentSessionLinkService(transcript).record(session_id, context, "missing")
    monkeypatch.setattr(
        "personal_agent_gateway.api.audit.fetch_sessions_strict",
        lambda _config: [],
    )

    response = client.get("/api/audit/session-consistency")

    assert response.status_code == 200
    assert response.json() == {
        "missing_in_lmg": [
            {
                "provider": "codex",
                "upstream_session_id": "missing",
                "consumer_session_id": session_id,
                "context_fingerprint": context.fingerprint(),
            }
        ],
        "unlinked_in_pag": [],
        "context_mismatch": [],
        "counts": {
            "missing_in_lmg": 1,
            "unlinked_in_pag": 0,
            "context_mismatch": 0,
        },
    }


def test_session_consistency_redacts_lmg_query_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _authenticated_client(tmp_path)

    def unavailable(_config):
        raise LMGQueryError("Bearer secret internal upstream details")

    monkeypatch.setattr(
        "personal_agent_gateway.api.audit.fetch_sessions_strict",
        unavailable,
    )

    response = client.get("/api/audit/session-consistency")

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Local model gateway consistency check unavailable"
    )
    assert "secret" not in response.text
