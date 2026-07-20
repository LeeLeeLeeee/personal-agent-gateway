from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from personal_agent_gateway.auth import require_token
from personal_agent_gateway.config import (
    AppConfig,
    ConfigError,
    _default_capture_binary,
    _default_codex_binary,
    load_config,
)


@pytest.fixture(autouse=True)
def disable_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("personal_agent_gateway.config.load_dotenv", lambda: None)


def test_load_config_does_not_require_web_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("AGENT_WEB_TOKEN", raising=False)
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("AGENT_SESSION_DIR", str(tmp_path / "sessions"))

    config = load_config()

    assert config.web_token is None


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


def test_load_config_reads_cookie_secure_flag() -> None:
    config = AppConfig.from_env(
        {
            "AGENT_WEB_TOKEN": "secret-token",
            "AGENT_WORKSPACE_ROOT": ".",
            "AGENT_SESSION_DIR": "./data/sessions",
            "AGENT_COOKIE_SECURE": "true",
        }
    )

    assert config.cookie_secure is True


def test_load_config_derives_local_data_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("AGENT_WEB_TOKEN", raising=False)
    for name in (
        "AGENT_APP_DB_PATH",
        "AGENT_ARTIFACT_ROOT",
        "AGENT_TEMP_DIR",
        "AGENT_AUTH_DIR",
        "AGENT_FFMPEG_BIN",
        "AGENT_FFPROBE_BIN",
        "AGENT_CAPTURE_BIN",
        "AGENT_JOB_WORKER_CONCURRENCY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setenv("AGENT_SESSION_DIR", str(tmp_path / "data" / "sessions"))

    config = load_config()

    assert config.app_db_path == tmp_path / "data" / "app.sqlite"
    assert config.artifact_root == tmp_path / "data" / "artifacts"
    assert config.temp_dir == tmp_path / "data" / "temp"
    assert config.auth_dir == tmp_path / "data" / "auth"
    assert config.ffmpeg_binary == "ffmpeg"
    assert config.ffprobe_binary == "ffprobe"
    assert config.capture_binary == _default_capture_binary()
    assert config.job_worker_concurrency == 1


def test_load_config_accepts_local_tool_and_auth_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AGENT_WEB_TOKEN", "api-token")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setenv("AGENT_SESSION_DIR", str(tmp_path / "data" / "sessions"))
    monkeypatch.setenv("AGENT_APP_DB_PATH", str(tmp_path / "custom.sqlite"))
    monkeypatch.setenv("AGENT_ARTIFACT_ROOT", str(tmp_path / "store"))
    monkeypatch.setenv("AGENT_TEMP_DIR", str(tmp_path / "scratch"))
    monkeypatch.setenv("AGENT_AUTH_DIR", str(tmp_path / "auth-store"))
    monkeypatch.setenv("AGENT_AUTH_SETUP_TOKEN", "setup-token")
    monkeypatch.setenv("AGENT_AUTH_REQUIRE_TOKEN_AND_OTP", "true")
    monkeypatch.setenv("AGENT_FFMPEG_BIN", "/opt/homebrew/bin/ffmpeg")
    monkeypatch.setenv("AGENT_FFPROBE_BIN", "/opt/homebrew/bin/ffprobe")
    monkeypatch.setenv("AGENT_CAPTURE_BIN", "screencapture-custom")
    monkeypatch.setenv("AGENT_JOB_WORKER_CONCURRENCY", "1")

    config = load_config()

    assert config.web_token == "api-token"
    assert config.app_db_path == tmp_path / "custom.sqlite"
    assert config.artifact_root == tmp_path / "store"
    assert config.temp_dir == tmp_path / "scratch"
    assert config.auth_dir == tmp_path / "auth-store"
    assert config.auth_setup_token == "setup-token"
    assert config.auth_require_token_and_otp is True
    assert config.ffmpeg_binary == "/opt/homebrew/bin/ffmpeg"
    assert config.ffprobe_binary == "/opt/homebrew/bin/ffprobe"
    assert config.capture_binary == "screencapture-custom"
    assert config.job_worker_concurrency == 1


def test_default_codex_binary_uses_windows_cmd_shim() -> None:
    assert _default_codex_binary("nt") == "codex.cmd"
    assert _default_codex_binary("posix") == "codex"


def test_default_capture_binary_matches_platform() -> None:
    assert _default_capture_binary("darwin") == "screencapture"
    assert _default_capture_binary("win32") == "powershell"
    assert _default_capture_binary("linux") == "unsupported-capture"


def test_require_token_accepts_query_token_and_sets_cookie() -> None:
    app = FastAPI()

    @app.get("/")
    def route(_: None = require_token("secret-token")) -> dict[str, str]:
        return {"status": "ok"}

    client = TestClient(app)
    response = client.get("/?token=secret-token")

    assert response.status_code == 200
    assert response.cookies.get("agent_web_token") == "secret-token"


def test_require_token_can_set_secure_cookie() -> None:
    app = FastAPI()

    @app.get("/")
    def route(_: None = require_token("secret-token", secure_cookie=True)) -> dict[str, str]:
        return {"status": "ok"}

    client = TestClient(app)
    response = client.get("/?token=secret-token")

    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]


def test_require_token_rejects_missing_or_invalid_token() -> None:
    app = FastAPI()

    @app.get("/")
    def route(_: None = require_token("secret-token")) -> dict[str, str]:
        return {"status": "ok"}

    client = TestClient(app)

    assert client.get("/").status_code == 401
    assert client.get("/?token=wrong").status_code == 401
