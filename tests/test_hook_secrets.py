from pathlib import Path

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
