from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_root_npm_scripts_build_frontend_before_default_start() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert package["scripts"] == {
        "build:frontend": "npm --prefix frontend run build",
        "start": "npm run build:frontend && npm run start:no-build",
        "start:no-build": (
            "powershell.exe -NoProfile -ExecutionPolicy Bypass "
            "-File ./scripts/start_local_runtime.ps1"
        ),
        "stop": (
            "powershell.exe -NoProfile -ExecutionPolicy Bypass "
            "-File ./scripts/stop_local_runtime.ps1"
        ),
    }
