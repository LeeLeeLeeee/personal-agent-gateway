import json
from pathlib import Path

import pytest

from personal_agent_gateway.source_staging import (
    InputSnapshotModified,
    SourceStager,
    SourceStagingError,
)


def test_stage_copies_safe_files_and_writes_verifiable_manifest(tmp_path: Path) -> None:
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    source.mkdir()
    (source / "package.json").write_text('{"name":"demo"}', encoding="utf-8")
    (source / ".env.local").write_text("SECRET=value", encoding="utf-8")
    (source / ".git").mkdir()
    (source / ".git" / "config").write_text("ignored", encoding="utf-8")
    (source / "node_modules").mkdir()
    (source / "node_modules" / "dep.js").write_text("ignored", encoding="utf-8")
    stager = SourceStager(home=tmp_path / "home")

    staged = stager.stage((source,), workspace)

    copied = workspace / "_inputs" / "01-source" / "package.json"
    assert copied.is_file()
    assert not (workspace / "_inputs" / "01-source" / ".env.local").exists()
    assert not (workspace / "_inputs" / "01-source" / ".git").exists()
    assert staged.manifest_path == workspace / "_inputs" / "manifest.json"
    assert staged.read_roots == (workspace / "_inputs",)
    manifest = json.loads(staged.manifest_path.read_text(encoding="utf-8"))
    assert [entry["staged_path"] for entry in manifest["files"]] == [
        "01-source/package.json"
    ]
    stager.verify(staged)


@pytest.mark.parametrize("mutation", ["changed", "deleted", "added"])
def test_verify_rejects_snapshot_mutation(tmp_path: Path, mutation: str) -> None:
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    source.mkdir()
    (source / "file.txt").write_text("original", encoding="utf-8")
    stager = SourceStager(home=tmp_path / "home")
    staged = stager.stage((source,), workspace)
    copied = workspace / "_inputs" / "01-source" / "file.txt"
    if mutation == "changed":
        copied.write_text("changed", encoding="utf-8")
    elif mutation == "deleted":
        copied.unlink()
    else:
        (copied.parent / "extra.txt").write_text("extra", encoding="utf-8")

    with pytest.raises(InputSnapshotModified):
        stager.verify(staged)


def test_stage_deduplicates_roots_and_sanitizes_names(tmp_path: Path) -> None:
    source = tmp_path / "source with spaces"
    source.mkdir()
    (source / "file.txt").write_text("content", encoding="utf-8")

    staged = SourceStager(home=tmp_path / "home").stage(
        (source, source.resolve()),
        tmp_path / "workspace",
    )

    assert (staged.read_roots[0] / "01-source-with-spaces" / "file.txt").is_file()
    assert not (staged.read_roots[0] / "02-source-with-spaces").exists()


@pytest.mark.parametrize("kind", ["missing", "home", "inside_workspace"])
def test_stage_rejects_unsafe_roots(tmp_path: Path, kind: str) -> None:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    home.mkdir()
    if kind == "missing":
        source = tmp_path / "missing"
    elif kind == "home":
        source = home
    else:
        workspace.mkdir()
        source = workspace / "source"
        source.mkdir()

    with pytest.raises(SourceStagingError):
        SourceStager(home=home).stage((source,), workspace)


def test_stage_skips_symlinks_when_supported(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    target = source / "target.txt"
    target.write_text("target", encoding="utf-8")
    link = source / "link.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable for this Windows account")

    staged = SourceStager(home=tmp_path / "home").stage(
        (source,),
        tmp_path / "workspace",
    )

    assert not (staged.read_roots[0] / "01-source" / "link.txt").exists()
