import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from personal_agent_gateway.file_safety import iter_safe_files


_IGNORED_ROOTS = {"_inputs"}
_IGNORED_FILES = {".delivery-applied.json", ".delivery-session.json"}


def inherit_workspace(
    source_root: Path,
    destination_root: Path,
    manifest_path: Path,
    source_team_run_id: str,
) -> None:
    if not source_root.is_dir():
        raise ValueError("Parent Team Run workspace does not exist")
    if not destination_root.is_dir():
        raise ValueError("Target Team Run workspace does not exist")

    safe_files = [
        (source, relative)
        for source, relative in iter_safe_files(
            source_root,
            allowed_sensitive_names=frozenset({".env.example"}),
        )
        if _should_inherit(relative)
    ]
    for _source, relative in safe_files:
        if (destination_root / relative).exists():
            raise ValueError(f"Inherited workspace file conflicts with target: {relative}")

    copied_files: list[dict[str, object]] = []
    for source, relative in safe_files:
        destination = destination_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied_files.append(
            {
                "path": relative,
                "size_bytes": destination.stat().st_size,
                "sha256": _sha256(destination),
            }
        )

    manifest_path.write_text(
        json.dumps(
            {
                "source_team_run_id": source_team_run_id,
                "copied_at": datetime.now(timezone.utc).isoformat(),
                "files": copied_files,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _should_inherit(relative: str) -> bool:
    path = Path(relative)
    if path.parts and path.parts[0] in _IGNORED_ROOTS:
        return False
    if path.name in _IGNORED_FILES:
        return False
    return not path.name.startswith(".delivery-integration-")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
