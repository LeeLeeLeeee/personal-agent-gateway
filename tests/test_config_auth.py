from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from personal_agent_gateway.auth import require_token
from personal_agent_gateway.config import ConfigError, load_config


def test_load_config_requires_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_WEB_TOKEN", "")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("AGENT_SESSION_DIR", str(tmp_path / "sessions"))

    with pytest.raises(ConfigError, match="AGENT_WEB_TOKEN"):
        load_config()


def test_load_config_rejects_non_loopback_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AGENT_WEB_TOKEN", "secret-token")
    monkeypatch.setenv("AGENT_WEB_HOST", "0.0.0.0")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("AGENT_SESSION_DIR", str(tmp_path / "sessions"))

    with pytest.raises(ConfigError, match="127.0.0.1"):
        load_config()


def test_require_token_accepts_query_token_and_sets_cookie() -> None:
    app = FastAPI()

    @app.get("/")
    def route(_: None = require_token("secret-token")) -> dict[str, str]:
        return {"status": "ok"}

    client = TestClient(app)
    response = client.get("/?token=secret-token")

    assert response.status_code == 200
    assert response.cookies.get("agent_web_token") == "secret-token"


def test_require_token_rejects_missing_or_invalid_token() -> None:
    app = FastAPI()

    @app.get("/")
    def route(_: None = require_token("secret-token")) -> dict[str, str]:
        return {"status": "ok"}

    client = TestClient(app)

    assert client.get("/").status_code == 401
    assert client.get("/?token=wrong").status_code == 401
