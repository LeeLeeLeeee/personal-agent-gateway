import json
import subprocess

from personal_agent_gateway.config import AppConfig
from personal_agent_gateway.local_capabilities import detect_local_agent_capabilities


def make_config(tmp_path):
    return AppConfig(
        workspace_root=tmp_path,
        session_dir=tmp_path / "sessions",
        codex_binary="codex-test",
        claude_binary="claude-test",
    )


def test_detect_local_agent_capabilities_runs_node_script_and_parses_payload(tmp_path):
    payload = {
        "schema_version": 1,
        "detected_at": "2026-07-14T00:00:00Z",
        "providers": {"codex": {"models": []}, "claude": {"models": []}},
    }
    captured = {}

    def runner(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    result = detect_local_agent_capabilities(make_config(tmp_path), runner=runner)

    assert result == payload
    assert captured["command"][0] == "node"
    assert captured["command"][1].endswith("scripts\\detect_local_agent_capabilities.mjs")
    assert captured["command"][-4:] == [
        "--claude-bin",
        "claude-test",
        "--cwd",
        str(tmp_path.resolve()),
    ]
    assert captured["kwargs"]["timeout"] == 15


def test_detect_local_agent_capabilities_returns_none_for_invalid_output(tmp_path):
    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="not-json", stderr="")

    assert detect_local_agent_capabilities(make_config(tmp_path), runner=runner) is None
