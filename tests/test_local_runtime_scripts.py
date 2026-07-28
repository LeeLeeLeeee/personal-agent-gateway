from __future__ import annotations

import json
import os
import socket
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
COMMON = ROOT / "scripts" / "local_runtime_common.ps1"
START = ROOT / "scripts" / "start_local_runtime.ps1"
STOP = ROOT / "scripts" / "stop_local_runtime.ps1"
STATE = ROOT / "data" / "local-runtime-state.json"

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


def run_ps_file(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(path),
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


def test_start_refuses_unknown_runtime_listener() -> None:
    if STATE.exists():
        pytest.skip("managed local runtime is active")

    listener: socket.socket | None = None
    try:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 8787))
        listener.listen()
    except OSError:
        if listener is not None:
            listener.close()
        listener = None

    try:
        before = run_ps(
            "(Get-NetTCPConnection -State Listen -LocalPort 8787 | "
            "Select-Object -First 1 -ExpandProperty OwningProcess)"
        )
        assert before.returncode == 0, before.stderr
        owner_pid = int(before.stdout.strip())

        result = run_ps_file(START)

        assert result.returncode != 0
        assert "port_conflict" in result.stdout + result.stderr
        after = run_ps(
            "(Get-NetTCPConnection -State Listen -LocalPort 8787 | "
            "Select-Object -First 1 -ExpandProperty OwningProcess)"
        )
        assert after.returncode == 0, after.stderr
        assert int(after.stdout.strip()) == owner_pid
    finally:
        if listener is not None:
            listener.close()


def test_stop_reports_not_running_without_state() -> None:
    if STATE.exists():
        pytest.skip("managed local runtime is active")

    result = run_ps_file(STOP)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "not_running"
    assert payload["identity"].endswith(r"\Administrator")
