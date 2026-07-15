from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    adapter.error = RuntimeError("auth failed")
    hook_service, run_service = _service(tmp_path, adapter)
    hook_id = _create(hook_service)

    runs = hook_service.poll_hook(hook_id, run_service)

    assert runs == []
    hook = hook_service.get_hook(hook_id)
    assert hook.cursor is None
    assert "auth failed" in hook.last_error


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
