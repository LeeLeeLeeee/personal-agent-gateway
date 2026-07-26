from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from personal_agent_gateway.agent_session_link import (
    AgentSessionContext,
    AgentSessionLinkService,
)
from personal_agent_gateway.app import create_app
from personal_agent_gateway.config import AppConfig
from personal_agent_gateway.lmg_client import LMGQueryError, fetch_sessions_strict


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


def _lmg_session_row(**overrides):
    row = {
        "provider": "codex",
        "upstream_id": "upstream-1",
        "model": "",
        "workspace_root": "",
        "created_at": "2026-07-27T00:00:00Z",
        "last_run_at": "2026-07-27T00:00:00Z",
        "size_bytes": 0,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    "rows",
    [
        [{"provider": "codex", "upstream_id": "incomplete"}],
        [_lmg_session_row(size_bytes=-1)],
        [_lmg_session_row(), _lmg_session_row(provider="claude")],
    ],
)
def test_session_consistency_returns_503_for_invalid_lmg_inventory(
    tmp_path: Path,
    monkeypatch,
    rows,
) -> None:
    client = _authenticated_client(tmp_path)

    def fetch_invalid(config):
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(200, json=rows)
        )
        return fetch_sessions_strict(config, transport=transport)

    monkeypatch.setattr(
        "personal_agent_gateway.api.audit.fetch_sessions_strict",
        fetch_invalid,
    )

    response = client.get("/api/audit/session-consistency")

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Local model gateway consistency check unavailable"
    )
