from __future__ import annotations

import json
from pathlib import Path

from personal_agent_gateway.artifacts import Artifact, ArtifactStore
from personal_agent_gateway.file_safety import iter_safe_files
from personal_agent_gateway.teams import TeamMessage, TeamRun, TeamRunService, TeamTask

WorkspaceSnapshot = dict[str, tuple[int, int]]

_PACKAGE_FILES = {
    "run-result.json": ("text", "Team Run result", "application/json"),
    "file-manifest.json": ("text", "Team Run file manifest", "application/json"),
    "verification.md": ("text", "Team Run verification", "text/markdown"),
}
_LEGACY_PACKAGE_FILES = {"workspace.zip"}
_VERIFICATION_WORDS = {
    "test",
    "qa",
    "quality",
    "verify",
    "verification",
    "validation",
    "security",
    "build",
    "deploy",
    "테스트",
    "검증",
    "품질",
    "보안",
    "빌드",
    "배포",
}


def _workspace_files(root: Path):
    yield from iter_safe_files(root)


def workspace_snapshot(root: Path) -> WorkspaceSnapshot:
    snapshot: WorkspaceSnapshot = {}
    for path, relative in _workspace_files(root):
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[relative] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def workspace_changes(before: WorkspaceSnapshot, after: WorkspaceSnapshot) -> dict[str, list[str]]:
    before_paths = set(before)
    after_paths = set(after)
    return {
        "files_created": sorted(after_paths - before_paths),
        "files_modified": sorted(
            path for path in before_paths & after_paths if before[path] != after[path]
        ),
        "files_deleted": sorted(before_paths - after_paths),
    }


def _message_paths(message: TeamMessage, key: str) -> list[str]:
    value = message.metadata.get(key)
    if not isinstance(value, list):
        return []
    return [path for path in value if isinstance(path, str)]


class TeamRunResultPackager:
    def __init__(self, teams: TeamRunService, artifacts: ArtifactStore) -> None:
        self._teams = teams
        self._artifacts = artifacts

    def build(self, run: TeamRun, cycle_id: str | None = None) -> list[Artifact]:
        current = self._teams.get_team_run(run.id)
        artifact_root = Path(current.artifact_root or current.workspace_root).resolve()
        artifact_root.mkdir(parents=True, exist_ok=True)
        tasks = self._teams.list_tasks(current.id, cycle_id)
        messages = self._teams.list_messages(current.id, cycle_id)
        deliverables = self._published_deliverables(current.id, cycle_id)

        result_path = artifact_root / "run-result.json"
        result_path.write_text(
            json.dumps(
                self._result_payload(
                    current,
                    tasks,
                    messages,
                    deliverables,
                    cycle_id,
                ),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        manifest_path = artifact_root / "file-manifest.json"
        manifest_path.write_text(
            json.dumps(
                self._manifest_payload(current, deliverables, cycle_id),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        verification_path = artifact_root / "verification.md"
        verification_path.write_text(
            self._verification_markdown(current, tasks, cycle_id),
            encoding="utf-8",
        )

        package_names = [
            result_path.name,
            manifest_path.name,
            verification_path.name,
        ]
        archive_path = artifact_root / "workspace.zip"
        archive_path.unlink(missing_ok=True)

        self._delete_previous_registrations(current.id, cycle_id)
        scope = cycle_id or "run"
        registered: list[Artifact] = []
        for name in package_names:
            artifact_type, title, mime_type = _PACKAGE_FILES[name]
            registered.append(
                self._artifacts.register_existing_file(
                    artifact_type=artifact_type,
                    title=title,
                    source_path=artifact_root / name,
                    relative_path=f"team-runs/{current.id}/{scope}/{name}",
                    mime_type=mime_type,
                    tags=["team-run", current.status],
                    metadata={
                        "team_run_id": current.id,
                        "cycle_id": cycle_id,
                        "package_kind": name,
                    },
                )
            )
        return registered

    def _result_payload(
        self,
        run: TeamRun,
        tasks: list[TeamTask],
        messages: list[TeamMessage],
        deliverables: list[dict[str, object]],
        cycle_id: str | None,
    ) -> dict[str, object]:
        reports: dict[str, list[TeamMessage]] = {}
        for message in messages:
            task_id = message.metadata.get("task_id")
            if message.kind != "agent_output" or not isinstance(task_id, str):
                continue
            reports.setdefault(task_id, []).append(message)
        cycle = self._teams.get_cycle(cycle_id) if cycle_id is not None else None
        objective = (
            self._teams.get_cycle_objective(cycle_id)
            if cycle_id is not None
            else run.goal
        )
        return {
            "protocol_version": 1,
            "team_run_id": run.id,
            "cycle_id": cycle_id,
            "goal": run.goal,
            "objective": objective,
            "status": run.status,
            "summary": run.summary,
            "error_message": run.error_message,
            "finished_at": run.finished_at,
            "working_root": run.working_root or run.workspace_root,
            "artifact_root": run.artifact_root or run.workspace_root,
            "execution_metadata": (
                cycle.execution_metadata if cycle is not None else None
            ),
            "deliverables": deliverables,
            "tasks": [self._task_payload(task, reports.get(task.id, [])) for task in tasks],
        }

    @staticmethod
    def _task_payload(task: TeamTask, reports: list[TeamMessage]) -> dict[str, object]:
        file_groups = {
            key: sorted(
                {
                    path
                    for report in reports
                    for path in _message_paths(report, key)
                }
            )
            for key in ("files_created", "files_modified", "files_deleted")
        }
        return {
            "id": task.id,
            "cycle_id": task.cycle_id,
            "title": task.title,
            "description": task.description,
            "owner_agent_id": task.owner_agent_id,
            "status": task.status,
            "required": task.required,
            "acceptance": {
                "required_outputs": list(task.acceptance.required_outputs),
                "required_verifications": list(
                    task.acceptance.required_verifications
                ),
            },
            "outcome": task.outcome,
            "acceptance_result": task.acceptance_result,
            "result": task.result,
            "error_message": task.error_message,
            **file_groups,
            "reports": [report.content for report in reports],
        }

    @staticmethod
    def _manifest_payload(
        run: TeamRun,
        deliverables: list[dict[str, object]],
        cycle_id: str | None,
    ) -> dict[str, object]:
        return {
            "team_run_id": run.id,
            "cycle_id": cycle_id,
            "files": deliverables,
        }

    @staticmethod
    def _verification_markdown(run: TeamRun, tasks: list[TeamTask], cycle_id: str | None) -> str:
        verification_tasks = [
            task
            for task in tasks
            if any(word in task.title.lower() for word in _VERIFICATION_WORDS)
        ]
        lines = [
            "# Team Run Verification",
            "",
            f"- Team Run: `{run.id}`",
            f"- Cycle: `{cycle_id or '-'}`",
            f"- Run status: `{run.status}`",
            "",
        ]
        if not verification_tasks:
            lines.append(
                "전용 검증 태스크가 없어 테스트·빌드·보안 검증 통과 여부를 확정할 수 없습니다."
            )
            lines.append("")
            return "\n".join(lines)
        lines.extend(["## Verification tasks", ""])
        for task in verification_tasks:
            lines.append(f"### {task.title}")
            lines.append("")
            lines.append(f"- Status: `{task.status}`")
            lines.append("")
            lines.append(task.result or task.error_message or "보고된 결과가 없습니다.")
            lines.append("")
        return "\n".join(lines)

    def _published_deliverables(
        self,
        team_run_id: str,
        cycle_id: str | None,
    ) -> list[dict[str, object]]:
        deliverables: list[dict[str, object]] = []
        for artifact in self._artifacts.list():
            metadata = artifact.metadata
            if metadata.get("team_run_id") != team_run_id:
                continue
            if metadata.get("cycle_id") != cycle_id:
                continue
            source_path = metadata.get("source_path")
            digest = metadata.get("sha256")
            task_id = metadata.get("task_id")
            if not all(
                isinstance(value, str) and value
                for value in (source_path, digest, task_id)
            ):
                continue
            deliverables.append(
                {
                    "path": source_path,
                    "artifact_id": artifact.id,
                    "artifact_path": artifact.relative_path,
                    "artifact_type": artifact.type,
                    "mime_type": artifact.mime_type,
                    "size_bytes": artifact.size_bytes,
                    "sha256": digest,
                    "task_id": task_id,
                }
            )
        return sorted(
            deliverables,
            key=lambda item: (str(item["path"]), str(item["task_id"])),
        )

    def _delete_previous_registrations(self, team_run_id: str, cycle_id: str | None) -> None:
        for artifact in self._artifacts.list():
            if artifact.metadata.get("team_run_id") != team_run_id:
                continue
            if artifact.metadata.get("cycle_id") != cycle_id:
                continue
            if artifact.metadata.get("package_kind") not in (
                _PACKAGE_FILES.keys() | _LEGACY_PACKAGE_FILES
            ):
                continue
            self._artifacts.delete(artifact.id)
