import json
import subprocess
from collections.abc import Callable
from pathlib import Path

from personal_agent_gateway.config import AppConfig


Runner = Callable[..., subprocess.CompletedProcess[str]]


def detect_local_agent_capabilities(
    config: AppConfig,
    runner: Runner = subprocess.run,
) -> dict[str, object] | None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "detect_local_agent_capabilities.mjs"
    try:
        completed = runner(
            [
                "node",
                str(script),
                "--codex-bin",
                config.codex_binary,
                "--claude-bin",
                config.claude_binary,
                "--cwd",
                str(config.workspace_root),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return None
    if not isinstance(payload.get("providers"), dict):
        return None
    return {str(key): value for key, value in payload.items()}
