import hashlib
import mimetypes
from datetime import datetime, timedelta, timezone
from pathlib import Path

from personal_agent_gateway.artifacts import Artifact, ArtifactStore
from personal_agent_gateway.team_outcomes import TaskOutcome
from personal_agent_gateway.teams import TeamTask


class ArtifactPublicationError(RuntimeError):
    pass


class TeamArtifactPublisher:
    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def publish(
        self,
        run_id: str,
        cycle_id: str | None,
        task: TeamTask,
        outcome: TaskOutcome,
        workspace_root: Path,
    ) -> tuple[Artifact, ...]:
        workspace = workspace_root.resolve()
        expiry = datetime.now(timezone.utc) + timedelta(days=30)
        published: list[Artifact] = []
        try:
            for deliverable in outcome.deliverables:
                source = (workspace / deliverable.path).resolve()
                source.relative_to(workspace)
                digest = _sha256(source)
                destination = (
                    Path("team-runs")
                    / run_id
                    / (cycle_id or "run")
                    / "deliverables"
                    / task.id
                    / source.name
                )
                artifact = self._store.register_existing_file(
                    artifact_type=deliverable.kind,
                    title=source.name,
                    source_path=source,
                    relative_path=destination.as_posix(),
                    mime_type=mimetypes.guess_type(source.name)[0]
                    or "application/octet-stream",
                    metadata={
                        "source_path": deliverable.path,
                        "sha256": digest,
                        "task_id": task.id,
                        "cycle_id": cycle_id,
                        "team_run_id": run_id,
                    },
                    retention_class="temporary",
                    expires_at=expiry,
                    origin_kind="team_task_output",
                    artifact_role="deliverable",
                    source_team_task_id=task.id,
                    source_team_run_id=run_id,
                    source_cycle_id=cycle_id,
                    origin_group_label_snapshot="Team output",
                    origin_item_label_snapshot=task.title,
                )
                published.append(artifact)
        except Exception as exc:
            for artifact in reversed(published):
                self._store.delete(artifact.id)
            raise ArtifactPublicationError("artifact_publication_failed") from exc
        return tuple(published)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
