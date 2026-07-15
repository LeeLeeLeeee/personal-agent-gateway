import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from personal_agent_gateway.app import create_app
from personal_agent_gateway.backup import BackupError, BackupService
from personal_agent_gateway.config import AppConfig
from personal_agent_gateway.db import Database
from personal_agent_gateway.intake import IntakeGate


def make_config(tmp_path: Path) -> AppConfig:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return AppConfig(
        web_token="secret-token",
        workspace_root=workspace,
        session_dir=tmp_path / "data" / "sessions",
        openai_api_key="test-key",
    )


def make_service(tmp_path: Path) -> tuple[BackupService, Database, IntakeGate]:
    data = tmp_path / "data"
    db = Database(data / "app.sqlite")
    db.initialize()
    gate = IntakeGate()
    service = BackupService(
        database=db,
        backup_root=data / "backups",
        auth_dir=data / "auth",
        session_dir=data / "sessions",
        artifact_root=data / "artifacts",
        workspace_root=tmp_path / "workspace",
        intake_gate=gate,
    )
    return service, db, gate


def test_backup_round_trip_uses_manifest_and_separate_restore_target(tmp_path: Path) -> None:
    service, db, gate = make_service(tmp_path)
    db.execute(
        "insert into runtime_settings (key, value, updated_at) values (?, ?, ?)",
        ("round-trip", "present", "2026-07-15T00:00:00+00:00"),
    )
    auth_dir = tmp_path / "data" / "auth"
    auth_dir.mkdir(parents=True)
    (auth_dir / "secret.txt").write_text("must-not-enter-manifest", encoding="utf-8")

    backup = service.create_backup()
    manifest_text = backup.manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)

    assert manifest["manifest_version"] == 1
    assert manifest["file_bodies_included"] is False
    assert manifest["database"]["sha256"]
    assert "must-not-enter-manifest" not in manifest_text
    assert service.dry_run(backup.id).valid is True

    target = tmp_path / "restore" / "restored.sqlite"
    gate.close()
    restored = service.restore_to(backup.id, target)

    assert restored == target.resolve()
    row = Database(target).fetchone(
        "select value from runtime_settings where key = ?",
        ("round-trip",),
    )
    assert row is not None and row["value"] == "present"


def test_backup_dry_run_rejects_checksum_tamper_and_target_conflict(tmp_path: Path) -> None:
    service, _db, gate = make_service(tmp_path)
    backup = service.create_backup()
    backup.database_path.write_bytes(backup.database_path.read_bytes() + b"tamper")

    with pytest.raises(BackupError, match="checksum"):
        service.dry_run(backup.id)

    clean = service.create_backup()
    target = tmp_path / "restore.sqlite"
    target.write_text("occupied", encoding="utf-8")
    gate.close()
    with pytest.raises(BackupError, match="already exists"):
        service.restore_to(clean.id, target)


def test_restore_requires_closed_intake(tmp_path: Path) -> None:
    service, _db, _gate = make_service(tmp_path)
    backup = service.create_backup()

    with pytest.raises(BackupError, match="intake must be stopped"):
        service.restore_to(backup.id, tmp_path / "restore.sqlite")


def test_backup_api_creates_lists_and_verifies_backup(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))
    client.cookies.set(
        "agent_session",
        client.app.state.auth_session_service.issue().token,
    )

    created = client.post("/api/operations/backups")
    listed = client.get("/api/operations/backups")
    verified = client.post(
        f"/api/operations/backups/{created.json()['backup']['id']}/dry-run"
    )

    assert created.status_code == 200
    assert listed.json()["backups"][0]["id"] == created.json()["backup"]["id"]
    assert verified.json()["valid"] is True
