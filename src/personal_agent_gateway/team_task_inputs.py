from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from personal_agent_gateway.db import Database
from personal_agent_gateway.teams import TeamRunService, TeamTask


class TaskInputUnavailable(ValueError):
    def __init__(self) -> None:
        super().__init__("input_artifact_unavailable")


@dataclass(frozen=True)
class TaskInputManifest:
    paths: tuple[str, ...]
    sha256: str


class TaskInputStager:
    def __init__(self, db: Database, teams: TeamRunService) -> None:
        self._db = db
        self._teams = teams

    def stage(self, task: TeamTask, workspace_root: Path) -> TaskInputManifest:
        workspace = workspace_root.resolve()
        staged_paths: list[str] = []
        for record in self._teams.list_task_input_artifacts(task.id):
            artifact = self._db.fetchone(
                "select file_path from artifacts where id = ?",
                (record.artifact_id,),
            )
            source = Path(artifact["file_path"]) if artifact is not None else None
            if source is None or not source.is_file():
                raise TaskInputUnavailable()
            if source.stat().st_size != record.size_bytes or _sha256(source) != record.sha256:
                raise TaskInputUnavailable()
            destination = _bounded_destination(workspace, record.staged_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            staged_paths.append(record.staged_path)
        manifest_path = _bounded_destination(
            workspace,
            f"inputs/.manifests/{task.id}.json",
        )
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps({"task_id": task.id, "paths": staged_paths}, sort_keys=True),
            encoding="utf-8",
        )
        return TaskInputManifest(tuple(staged_paths), _sha256(manifest_path))


def _bounded_destination(workspace: Path, relative_path: str) -> Path:
    posix = PurePosixPath(relative_path)
    windows = PureWindowsPath(relative_path)
    if (
        not relative_path
        or posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or ".." in posix.parts
        or ".." in windows.parts
    ):
        raise TaskInputUnavailable()
    destination = (workspace / Path(*posix.parts)).resolve()
    try:
        destination.relative_to(workspace)
    except ValueError as exc:
        raise TaskInputUnavailable() from exc
    return destination


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
