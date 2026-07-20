import os
import stat
from pathlib import Path

import pytest

from personal_agent_gateway.hook_secrets import HookSecretStore


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    store = HookSecretStore(tmp_path / "hooks")
    store.save("conn-1", "app-password")
    assert store.load("conn-1") == "app-password"


def test_load_missing_returns_none(tmp_path: Path) -> None:
    store = HookSecretStore(tmp_path / "hooks")
    assert store.load("nope") is None


def test_delete_removes_secret(tmp_path: Path) -> None:
    store = HookSecretStore(tmp_path / "hooks")
    store.save("conn-1", "app-password")
    store.delete("conn-1")
    assert store.load("conn-1") is None


def test_delete_missing_is_noop(tmp_path: Path) -> None:
    store = HookSecretStore(tmp_path / "hooks")
    store.delete("nope")  # must not raise


def test_save_is_atomic_when_replace_fails(tmp_path: Path, monkeypatch) -> None:
    store = HookSecretStore(tmp_path / "hooks")
    store.save("conn-1", "old-secret")

    def fail_replace(_source, _target):
        raise OSError("replace failed")

    monkeypatch.setattr("personal_agent_gateway.private_json.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        store.save("conn-1", "new-secret")

    assert store.load("conn-1") == "old-secret"
    assert list(store.root.glob("*.tmp")) == []


def test_secret_file_uses_private_posix_permissions(tmp_path: Path) -> None:
    store = HookSecretStore(tmp_path / "hooks")
    store.save("conn-1", "secret")

    if os.name != "nt":
        mode = stat.S_IMODE((store.root / "conn-1.json").stat().st_mode)
        assert mode == 0o600


def test_malformed_secret_file_fails_closed(tmp_path: Path) -> None:
    store = HookSecretStore(tmp_path / "hooks")
    store.root.mkdir(parents=True)
    (store.root / "conn-1.json").write_text("{", encoding="utf-8")

    assert store.load("conn-1") is None
