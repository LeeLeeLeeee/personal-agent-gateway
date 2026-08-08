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
