from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
COMMON = ROOT / "scripts" / "local_runtime_common.ps1"

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows runtime launcher")


def run_ps(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            f"$ErrorActionPreference = 'Stop'; {command}",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (r"DESKTOP\Administrator", "False"),
        (r"DESKTOP\CodexSandboxOffline", "True"),
        (r"desktop\codexsandboxonline", "True"),
    ],
)
def test_codex_sandbox_identity_detection(name: str, expected: str) -> None:
    result = run_ps(
        f". '{COMMON}'; "
        f"(Test-CodexSandboxIdentity -Name '{name}').ToString()"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@pytest.mark.parametrize(
    "process_fields",
    [
        "Id=41; StartTime=[datetime]'2026-07-28T01:02:03Z'; "
        "Path='C:\\runtime\\service.exe'",
        "Id=42; StartTime=[datetime]'2026-07-28T01:02:05Z'; "
        "Path='C:\\runtime\\service.exe'",
        "Id=42; StartTime=[datetime]'2026-07-28T01:02:03Z'; "
        "Path='C:\\runtime\\other.exe'",
    ],
)
def test_runtime_process_match_rejects_each_mismatched_field(
    process_fields: str,
) -> None:
    state = {
        "pid": 42,
        "started_at": "2026-07-28T01:02:03.0000000Z",
        "path": r"C:\runtime\service.exe",
    }
    encoded = json.dumps(state).replace("'", "''")
    result = run_ps(
        f". '{COMMON}'; "
        f"$entry = '{encoded}' | ConvertFrom-Json; "
        f"$process = [pscustomobject]@{{{process_fields}}}; "
        "(Test-RuntimeProcessMatches -Entry $entry -Process $process).ToString()"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


def test_current_process_owner_matches_runtime_identity() -> None:
    result = run_ps(
        f". '{COMMON}'; "
        "$identity = Get-HostRuntimeIdentity; "
        "$ownerSid = Get-ProcessOwnerSid -ProcessId $PID; "
        "[string]::Equals("
        "$identity.sid, $ownerSid, [StringComparison]::OrdinalIgnoreCase)"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"


def test_runtime_state_round_trip(tmp_path: Path) -> None:
    state_path = str(tmp_path / "runtime-state.json").replace("'", "''")
    result = run_ps(
        f". '{COMMON}'; "
        "$state = [ordered]@{"
        "schema_version=1; "
        "identity=[ordered]@{name='DESKTOP\\Administrator'; sid='S-1-5-21'}"
        "}; "
        f"Write-LocalRuntimeState -Path '{state_path}' -State $state; "
        f"(Read-LocalRuntimeState -Path '{state_path}') | ConvertTo-Json -Compress"
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "schema_version": 1,
        "identity": {
            "name": r"DESKTOP\Administrator",
            "sid": "S-1-5-21",
        },
    }
