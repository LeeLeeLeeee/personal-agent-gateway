from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_root_npm_scripts_build_frontend_before_default_start() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert package["scripts"] == {
        "build:frontend": "npm --prefix frontend run build",
        "start": "npm run build:frontend && npm run start:no-build",
        "start:no-build": "node ./scripts/local_runtime_launcher.js start",
        "stop": "node ./scripts/local_runtime_launcher.js stop",
    }


def test_runtime_launcher_preserves_windows_powershell_commands() -> None:
    launcher = (ROOT / "scripts" / "local_runtime_launcher.js").read_text(
        encoding="utf-8"
    )

    assert 'path.join(__dirname, action + "_local_runtime.ps1")' in launcher
    assert '"powershell.exe"' in launcher
    assert '"-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script' in launcher


def test_macos_launcher_accepts_pag_local_token_as_lmg_local_token() -> None:
    launcher = (ROOT / "scripts" / "start_local_runtime.sh").read_text(encoding="utf-8")

    assert 'export LMG_LOCAL_TOKEN="${PAG_LOCAL_TOKEN:-}"' in launcher


def test_macos_stop_waits_for_exit_before_removing_runtime_state() -> None:
    stop_script = (ROOT / "scripts" / "stop_local_runtime.sh").read_text(
        encoding="utf-8"
    )

    term_index = stop_script.index('kill "$pag_pid" "$lmg_pid"')
    wait_index = stop_script.index('wait_for_processes 10 "$pag_pid" "$lmg_pid"')
    force_index = stop_script.index('force_stop "$pag_pid"')
    final_wait_index = stop_script.index(
        'wait_for_processes 5 "$pag_pid" "$lmg_pid"'
    )
    remove_index = stop_script.index('rm "$state_path"')

    assert 'kill -KILL "$pid"' in stop_script
    assert term_index < wait_index < force_index < final_wait_index < remove_index


def test_launcher_bounds_uvicorn_graceful_shutdown() -> None:
    """uvicorn defaults timeout_graceful_shutdown to None, which means wait
    forever: one browser tab holding GET /api/events made the server ignore the
    first Ctrl+C entirely."""
    script = (ROOT / "scripts" / "start_local_runtime.ps1").read_text(encoding="utf-8")
    assert "--timeout-graceful-shutdown" in script
    local = (ROOT / "scripts" / "run_local.ps1").read_text(encoding="utf-8")
    assert "--timeout-graceful-shutdown" in local
