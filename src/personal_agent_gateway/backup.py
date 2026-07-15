import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from personal_agent_gateway.db import Database
from personal_agent_gateway.intake import IntakeGate


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupRecord:
    id: str
    created_at: str
    schema_version: int
    database_sha256: str
    database_size_bytes: int
    directory: Path

    @property
    def database_path(self) -> Path:
        return self.directory / "gateway.sqlite"

    @property
    def manifest_path(self) -> Path:
        return self.directory / "manifest.json"


@dataclass(frozen=True)
class BackupValidation:
    backup_id: str
    valid: bool
    schema_version: int
    database_sha256: str
    target_path: Path | None


class BackupService:
    def __init__(
        self,
        *,
        database: Database,
        backup_root: Path,
        auth_dir: Path,
        session_dir: Path,
        artifact_root: Path,
        workspace_root: Path,
        intake_gate: IntakeGate,
    ) -> None:
        self._database = database
        self._backup_root = backup_root.resolve()
        self._auth_dir = auth_dir.resolve()
        self._session_dir = session_dir.resolve()
        self._artifact_root = artifact_root.resolve()
        self._workspace_root = workspace_root.resolve()
        self._intake_gate = intake_gate

    def create_backup(self) -> BackupRecord:
        created_at = datetime.now(timezone.utc)
        backup_id = f"{created_at:%Y%m%dT%H%M%S%fZ}-{uuid4().hex[:8]}"
        directory = self._backup_root / backup_id
        directory.mkdir(parents=True, exist_ok=False)
        database_path = directory / "gateway.sqlite"
        try:
            with self._database.connection() as source:
                with sqlite3.connect(database_path) as target:
                    source.backup(target)
            database_sha256 = _sha256(database_path)
            schema_version = _schema_version(database_path)
            manifest = {
                "manifest_version": 1,
                "backup_id": backup_id,
                "created_at": created_at.isoformat(),
                "schema_version": schema_version,
                "file_bodies_included": False,
                "database": {
                    "file": database_path.name,
                    "sha256": database_sha256,
                    "size_bytes": database_path.stat().st_size,
                },
                "auth": _directory_manifest(self._auth_dir),
                "sessions": _directory_manifest(self._session_dir),
                "artifacts": self._artifact_manifest(),
                "workspace": {
                    "exists": self._workspace_root.exists(),
                    "writable": os.access(self._workspace_root, os.W_OK),
                },
            }
            (directory / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        return self.get_backup(backup_id)

    def get_backup(self, backup_id: str) -> BackupRecord:
        directory = self._backup_directory(backup_id)
        manifest = _load_manifest(directory / "manifest.json")
        database = manifest.get("database")
        if not isinstance(database, dict):
            raise BackupError("Backup manifest database entry is invalid")
        return BackupRecord(
            id=backup_id,
            created_at=str(manifest.get("created_at") or ""),
            schema_version=int(manifest.get("schema_version") or 0),
            database_sha256=str(database.get("sha256") or ""),
            database_size_bytes=int(database.get("size_bytes") or 0),
            directory=directory,
        )

    def list_backups(self) -> list[BackupRecord]:
        if not self._backup_root.exists():
            return []
        records: list[BackupRecord] = []
        for directory in sorted(self._backup_root.iterdir(), reverse=True):
            if not directory.is_dir():
                continue
            try:
                records.append(self.get_backup(directory.name))
            except BackupError:
                continue
        return records

    def dry_run(
        self,
        backup_id: str,
        *,
        target_path: Path | None = None,
    ) -> BackupValidation:
        record = self.get_backup(backup_id)
        manifest = _load_manifest(record.manifest_path)
        if manifest.get("manifest_version") != 1:
            raise BackupError("Unsupported backup manifest version")
        database = manifest.get("database")
        if not isinstance(database, dict) or database.get("file") != "gateway.sqlite":
            raise BackupError("Backup database entry is invalid")
        if not record.database_path.is_file():
            raise BackupError("Backup database file is missing")
        actual_sha256 = _sha256(record.database_path)
        if actual_sha256 != database.get("sha256"):
            raise BackupError("Backup database checksum mismatch")
        schema_version = _schema_version(record.database_path)
        if schema_version != int(manifest.get("schema_version") or -1):
            raise BackupError("Backup schema version mismatch")
        _integrity_check(record.database_path)

        resolved_target = target_path.resolve() if target_path is not None else None
        if resolved_target is not None:
            if resolved_target.exists():
                raise BackupError("Restore target already exists")
            if resolved_target == self._database.path.resolve():
                raise BackupError("Restore target must be separate from the live database")
            if record.directory in resolved_target.parents:
                raise BackupError("Restore target must be outside the backup directory")
        return BackupValidation(
            backup_id=backup_id,
            valid=True,
            schema_version=schema_version,
            database_sha256=actual_sha256,
            target_path=resolved_target,
        )

    def restore_to(self, backup_id: str, target_path: Path) -> Path:
        if self._intake_gate.is_open:
            raise BackupError("Execution intake must be stopped before restore")
        validation = self.dry_run(backup_id, target_path=target_path)
        assert validation.target_path is not None
        source = self.get_backup(backup_id).database_path
        validation.target_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source, validation.target_path)
            restored = Database(validation.target_path)
            restored.initialize()
            if restored.fetchone("select 1 as ready") is None:
                raise BackupError("Restored database read probe failed")
        except Exception:
            validation.target_path.unlink(missing_ok=True)
            raise
        return validation.target_path

    def _backup_directory(self, backup_id: str) -> Path:
        if not backup_id or any(character not in "0123456789TZ-abcdef" for character in backup_id):
            raise BackupError("Invalid backup id")
        directory = (self._backup_root / backup_id).resolve()
        if directory.parent != self._backup_root or not directory.is_dir():
            raise BackupError("Backup not found")
        return directory

    def _artifact_manifest(self) -> dict[str, object]:
        rows = self._database.fetchall(
            "select type, count(*) as count, coalesce(sum(size_bytes), 0) as size_bytes "
            "from artifacts group by type order by type"
        )
        return {
            "root_exists": self._artifact_root.exists(),
            "record_count": sum(int(row["count"]) for row in rows),
            "size_bytes": sum(int(row["size_bytes"]) for row in rows),
            "by_type": [
                {
                    "type": str(row["type"]),
                    "count": int(row["count"]),
                    "size_bytes": int(row["size_bytes"]),
                }
                for row in rows
            ],
        }


def _directory_manifest(root: Path) -> dict[str, object]:
    files: list[dict[str, object]] = []
    if root.exists():
        for current, directories, names in os.walk(root, followlinks=False):
            directories[:] = [
                name for name in directories if not (Path(current) / name).is_symlink()
            ]
            for name in sorted(names):
                path = Path(current) / name
                if path.is_symlink() or not path.is_file():
                    continue
                stat = path.stat()
                files.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "size_bytes": stat.st_size,
                        "modified_at": datetime.fromtimestamp(
                            stat.st_mtime,
                            tz=timezone.utc,
                        ).isoformat(),
                    }
                )
    return {"root_exists": root.exists(), "files": files}


def _load_manifest(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError("Backup manifest is missing or invalid") from exc
    if not isinstance(payload, dict):
        raise BackupError("Backup manifest is invalid")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _schema_version(path: Path) -> int:
    try:
        with sqlite3.connect(path) as connection:
            row = connection.execute(
                "select max(version) from schema_migrations"
            ).fetchone()
    except sqlite3.Error as exc:
        raise BackupError("Backup database schema cannot be read") from exc
    return int(row[0] or 0) if row is not None else 0


def _integrity_check(path: Path) -> None:
    try:
        with sqlite3.connect(path) as connection:
            row = connection.execute("pragma integrity_check").fetchone()
    except sqlite3.Error as exc:
        raise BackupError("Backup database integrity check failed") from exc
    if row is None or row[0] != "ok":
        raise BackupError("Backup database integrity check failed")
