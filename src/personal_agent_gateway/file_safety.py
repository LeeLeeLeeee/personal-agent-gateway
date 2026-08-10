import os
from pathlib import Path

IGNORED_DIRECTORY_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".cache",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".pnpm-store",
    "coverage",
}


def is_sensitive_file(name: str) -> bool:
    lowered = name.lower()
    return lowered == ".env" or lowered.startswith(".env.")


def iter_safe_files(
    root: Path,
    *,
    allowed_sensitive_names: frozenset[str] = frozenset(),
):
    if not root.is_dir():
        return
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(
            name
            for name in dirs
            if name.lower() not in IGNORED_DIRECTORY_NAMES
            and not (Path(current) / name).is_symlink()
        )
        for name in sorted(files):
            path = Path(current) / name
            sensitive = (
                is_sensitive_file(name)
                and name.lower() not in allowed_sensitive_names
            )
            if sensitive or path.is_symlink() or not path.is_file():
                continue
            yield path, path.relative_to(root).as_posix()
