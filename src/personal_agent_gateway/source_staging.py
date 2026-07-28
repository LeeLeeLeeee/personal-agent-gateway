import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from personal_agent_gateway.file_safety import iter_safe_files


class SourceStagingError(RuntimeError):
    pass


class InputSnapshotModified(SourceStagingError):
    pass


@dataclass(frozen=True)
class StagedInputs:
    read_roots: tuple[Path, ...]
    manifest_path: Path
    manifest_sha256: str


class SourceStager:
    def __init__(self, *, home: Path | None = None) -> None:
        self._home = (home or Path.home()).resolve()

    def stage(self, roots: tuple[Path, ...], workspace_root: Path) -> StagedInputs:
        workspace = workspace_root.resolve()
        canonical_roots = self._validate_roots(roots, workspace)
        inputs = workspace / "_inputs"
        if inputs.exists():
            raise SourceStagingError("Input staging directory already exists")
        workspace.mkdir(parents=True, exist_ok=True)
        temporary = workspace / f"._inputs-{uuid4().hex}"
        temporary.mkdir()
        try:
            entries: list[dict[str, object]] = []
            root_rows: list[dict[str, str]] = []
            for ordinal, source in enumerate(canonical_roots, start=1):
                directory_name = f"{ordinal:02d}-{_safe_name(source.name)}"
                destination = temporary / directory_name
                destination.mkdir()
                root_rows.append(
                    {
                        "origin": str(source),
                        "staged_path": directory_name,
                    }
                )
                for path, relative in iter_safe_files(source):
                    target = destination / Path(relative)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target)
                    entries.append(
                        {
                            "origin": str(path.resolve()),
                            "staged_path": f"{directory_name}/{relative}",
                            "size_bytes": target.stat().st_size,
                            "sha256": _sha256(target),
                        }
                    )
            entries.sort(key=lambda item: str(item["staged_path"]))
            manifest = {
                "version": 1,
                "roots": root_rows,
                "files": entries,
            }
            _write_json_atomically(temporary / "manifest.json", manifest)
            temporary.replace(inputs)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        manifest_path = inputs / "manifest.json"
        return StagedInputs(
            read_roots=(inputs,),
            manifest_path=manifest_path,
            manifest_sha256=_sha256(manifest_path),
        )

    def verify(self, staged_inputs: StagedInputs) -> None:
        manifest_path = staged_inputs.manifest_path.resolve()
        inputs = manifest_path.parent
        if _sha256(manifest_path) != staged_inputs.manifest_sha256:
            raise InputSnapshotModified("Input manifest was modified")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            rows = manifest["files"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise InputSnapshotModified("Input manifest is invalid") from exc
        if not isinstance(rows, list):
            raise InputSnapshotModified("Input manifest is invalid")
        expected: dict[str, tuple[int, str]] = {}
        for row in rows:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("staged_path"), str)
                or type(row.get("size_bytes")) is not int
                or not isinstance(row.get("sha256"), str)
            ):
                raise InputSnapshotModified("Input manifest is invalid")
            relative = row["staged_path"]
            if relative in expected:
                raise InputSnapshotModified("Input manifest contains duplicate paths")
            expected[relative] = (row["size_bytes"], row["sha256"])

        actual: dict[str, tuple[int, str]] = {}
        for path in inputs.rglob("*"):
            if path == manifest_path:
                continue
            if path.is_symlink() or (path.exists() and not (path.is_file() or path.is_dir())):
                raise InputSnapshotModified("Input snapshot contains an unsafe file")
            if not path.is_file():
                continue
            relative = path.relative_to(inputs).as_posix()
            actual[relative] = (path.stat().st_size, _sha256(path))
        if actual != expected:
            raise InputSnapshotModified("Input snapshot was modified")

    def _validate_roots(
        self,
        roots: tuple[Path, ...],
        workspace: Path,
    ) -> tuple[Path, ...]:
        canonical: list[Path] = []
        for root in roots:
            source = root.resolve()
            if not source.is_dir():
                raise SourceStagingError("Source root must be an existing directory")
            if source == self._home:
                raise SourceStagingError("The home directory cannot be staged")
            if _contains(workspace, source) or _contains(source, workspace):
                raise SourceStagingError("Source root cannot overlap the execution workspace")
            if source not in canonical:
                canonical.append(source)
        if not canonical:
            raise SourceStagingError("At least one source root is required")
        return tuple(canonical)


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return normalized or "source"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomically(path: Path, payload: dict[str, object]) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".manifest-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            temporary_path = Path(stream.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
