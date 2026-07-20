from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from personal_agent_gateway.db import Database
from personal_agent_gateway.hook_runs import HookRunService
from personal_agent_gateway.hook_secrets import HookSecretStore
from personal_agent_gateway.hooks import HookService, render_prompt
from personal_agent_gateway.sources.base import HookEvent, PollResult


class StubAdapter:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.result = PollResult(events=[], cursor={"uidvalidity": 1, "last_uid": 0})
        self.error: Exception | None = None

    def poll(self, connection, secret, cursor, filter_config):
        self.calls.append({"secret": secret, "cursor": cursor})
        if self.error is not None:
            raise self.error
        return self.result


def _service(tmp_path: Path, adapter: StubAdapter) -> tuple[HookService, HookRunService]:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    secret_store = HookSecretStore(tmp_path / "hooks")
    hook_service = HookService(db, secret_store, {"email": adapter})
    return hook_service, HookRunService(db)


def _create(hook_service: HookService) -> str:
    hook = hook_service.create_hook(
        name="Inbox watcher",
        source_type="email",
        connection={"host": "imap.test", "port": 993, "username": "me@test"},
        secret="app-pw",
        filter={"from_contains": "boss"},
        target_backend="codex",
        target_model="default",
        target_options={},
        prompt_template="요약: {{subject}}",
        poll_interval_seconds=300,
    )
    return hook.id


def test_create_hook_stores_secret_out_of_db(tmp_path: Path) -> None:
    adapter = StubAdapter()
    hook_service, _ = _service(tmp_path, adapter)
    hook_id = _create(hook_service)
    hook = hook_service.get_hook(hook_id)

    assert hook.enabled is True
    assert hook.target_backend == "codex"
    # 비밀은 DB에 없고 secret store에서만 로드된다.
    assert hook_service._secret_store.load(hook.connection_ref) == "app-pw"


def test_create_team_run_hook_requires_continuous_execution_run(tmp_path: Path) -> None:
    adapter = StubAdapter()
    hook_service, _ = _service(tmp_path, adapter)
    hook_service._db.execute(
        "insert into team_runs "
        "(id, goal, status, run_mode, lifecycle_mode, execution_policy, "
        "max_workers, workspace_root, created_at, updated_at) "
        "values ('mail-run','mailbox','draft','plan_and_execute','continuous',"
        "'triggered',1,'w','t','t')"
    )

    hook = hook_service.create_hook(
        name="Mail team",
        source_type="email",
        connection={},
        secret="pw",
        filter={},
        target_backend="",
        target_model="",
        target_options={},
        prompt_template="{{subject}}",
        poll_interval_seconds=300,
        target_kind="team_run",
        target_team_run_id="mail-run",
    )

    assert hook.target_kind == "team_run"
    assert hook.target_team_run_id == "mail-run"


def test_hook_rejects_auto_team_run_target(tmp_path: Path) -> None:
    adapter = StubAdapter()
    hooks, _ = _service(tmp_path, adapter)
    hooks._db.execute(
        "insert into team_runs "
        "(id, goal, status, run_mode, lifecycle_mode, execution_policy, "
        "max_workers, workspace_root, created_at, updated_at) "
        "values ('auto-run','goal','draft','plan_and_execute','continuous',"
        "'auto',1,'w','t','t')"
    )

    with pytest.raises(ValueError, match="TRIGGERED"):
        hooks.create_hook(
            name="mail",
            source_type="email",
            connection={"host": "imap.example.com", "port": 993, "username": "u"},
            secret="app-password",
            filter={"folder": "INBOX"},
            target_backend="",
            target_model="",
            target_options={},
            prompt_template="{{subject}}",
            poll_interval_seconds=300,
            target_kind="team_run",
            target_team_run_id="auto-run",
        )


def test_enabling_hook_rechecks_team_run_target(tmp_path: Path) -> None:
    adapter = StubAdapter()
    hooks, _ = _service(tmp_path, adapter)
    hooks._db.execute(
        "insert into team_runs "
        "(id, goal, status, run_mode, lifecycle_mode, execution_policy, "
        "max_workers, workspace_root, created_at, updated_at) "
        "values ('mail-run','goal','draft','plan_and_execute','continuous',"
        "'triggered',1,'w','t','t')"
    )
    hook = hooks.create_hook(
        name="mail",
        source_type="email",
        connection={},
        secret="pw",
        filter={},
        target_backend="",
        target_model="",
        target_options={},
        prompt_template="{{subject}}",
        poll_interval_seconds=300,
        target_kind="team_run",
        target_team_run_id="mail-run",
    )
    hooks.set_enabled(hook.id, False)
    hooks._db.execute(
        "update team_runs set execution_policy = 'auto' where id = 'mail-run'"
    )

    with pytest.raises(ValueError, match="TRIGGERED"):
        hooks.set_enabled(hook.id, True)


def test_render_prompt_substitutes_placeholders() -> None:
    prompt = render_prompt(
        "From {{from}} / {{subject}}: {{body}}",
        {"from": "a@b", "subject": "hi", "body_text": "hello"},
    )
    assert prompt == "From a@b / hi: hello"


def test_poll_hook_creates_runs_and_advances_cursor(tmp_path: Path) -> None:
    adapter = StubAdapter()
    adapter.result = PollResult(
        events=[HookEvent("email:1:2", "메일: hi — boss", {"subject": "hi"})],
        cursor={"uidvalidity": 1, "last_uid": 2},
    )
    hook_service, run_service = _service(tmp_path, adapter)
    hook_id = _create(hook_service)

    runs = hook_service.poll_hook(hook_id, run_service)

    assert len(runs) == 1
    assert runs[0].status == "queued"
    hook = hook_service.get_hook(hook_id)
    assert hook.cursor == {"uidvalidity": 1, "last_uid": 2}
    assert hook.last_polled_at is not None
    assert hook.last_error is None
    assert adapter.calls[0]["secret"] == "app-pw"


def test_poll_hook_failure_records_error_without_cursor_advance(tmp_path: Path) -> None:
    adapter = StubAdapter()
    adapter.error = RuntimeError("auth failed with app-pw")
    hook_service, run_service = _service(tmp_path, adapter)
    hook_id = _create(hook_service)

    runs = hook_service.poll_hook(hook_id, run_service)

    assert runs == []
    hook = hook_service.get_hook(hook_id)
    assert hook.cursor is None
    assert "auth failed" in hook.last_error
    assert "app-pw" not in hook.last_error
    assert "[redacted]" in hook.last_error


def test_poll_due_skips_recently_polled(tmp_path: Path) -> None:
    adapter = StubAdapter()
    hook_service, run_service = _service(tmp_path, adapter)
    hook_id = _create(hook_service)
    now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)

    hook_service.poll_hook(hook_id, run_service, now=now)
    # 60초 뒤: poll_interval(300초) 미만 → due 아님
    assert hook_service.due_hooks(now + timedelta(seconds=60)) == []
    # 301초 뒤: due
    assert [h.id for h in hook_service.due_hooks(now + timedelta(seconds=301))] == [hook_id]


def test_disabled_hook_is_not_due(tmp_path: Path) -> None:
    adapter = StubAdapter()
    hook_service, _ = _service(tmp_path, adapter)
    hook_id = _create(hook_service)
    hook_service.set_enabled(hook_id, False)
    assert hook_service.due_hooks(datetime.now(timezone.utc)) == []


def test_delete_removes_hook_and_secret(tmp_path: Path) -> None:
    adapter = StubAdapter()
    hook_service, _ = _service(tmp_path, adapter)
    hook_id = _create(hook_service)
    ref = hook_service.get_hook(hook_id).connection_ref

    hook_service.delete(hook_id)

    assert hook_service.list_hooks() == []
    assert hook_service._secret_store.load(ref) is None


def test_verify_connection_success(tmp_path) -> None:
    class VerifyAdapter:
        def __init__(self):
            self.called = None
        def poll(self, connection, secret, cursor, filter_config):
            raise AssertionError("poll should not be called")
        def verify(self, connection, secret, folder):
            self.called = (connection, secret, folder)
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.hook_secrets import HookSecretStore
    from personal_agent_gateway.hooks import HookService
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    adapter = VerifyAdapter()
    service = HookService(db, HookSecretStore(tmp_path / "hooks"), {"email": adapter})
    service.verify_connection(
        "email",
        {"host": "h", "port": 993, "username": "u"},
        "pw",
        {"folder": "INBOX"},
    )
    assert adapter.called == ({"host": "h", "port": 993, "username": "u"}, "pw", "INBOX")


def test_verify_connection_unsupported_source_raises(tmp_path) -> None:
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.hook_secrets import HookSecretStore
    from personal_agent_gateway.hooks import HookService
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    service = HookService(db, HookSecretStore(tmp_path / "hooks"), {})
    import pytest
    with pytest.raises(ValueError):
        service.verify_connection("email", {}, "pw", {})
