import json
import os
import stat
import tempfile
from pathlib import Path


def write_private_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _set_private_directory_permissions(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        handle = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = -1
        with handle:
            json.dump(payload, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _set_private_file_permissions(path)
        _sync_directory(path.parent)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def _set_private_directory_permissions(path: Path) -> None:
    if os.name != "nt":
        path.chmod(stat.S_IRWXU)


def _set_private_file_permissions(path: Path) -> None:
    if os.name == "nt":
        path.chmod(stat.S_IREAD | stat.S_IWRITE)
        return
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
