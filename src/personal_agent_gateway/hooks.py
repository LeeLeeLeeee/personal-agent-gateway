import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from personal_agent_gateway.db import Database
from personal_agent_gateway.hook_runs import HookRun, HookRunService
from personal_agent_gateway.hook_secrets import HookSecretStore
from personal_agent_gateway.sources.base import SourceAdapter


@dataclass(frozen=True)
class Hook:
    id: str
    name: str
    source_type: str
    connection_ref: str
    connection: dict[str, object]
    filter: dict[str, object]
    target_backend: str
    target_model: str
    target_options: dict[str, object]
    prompt_template: str
    poll_interval_seconds: int
    enabled: bool
    cursor: dict[str, object] | None
    last_polled_at: str | None
    last_error: str | None
    created_at: str
    updated_at: str


class HookService:
    def __init__(
        self,
        db: Database,
        secret_store: HookSecretStore,
        adapters: dict[str, SourceAdapter],
    ) -> None:
        self._db = db
        self._secret_store = secret_store
        self._adapters = adapters

    def create_hook(
        self,
        name: str,
        source_type: str,
        connection: dict[str, object],
        secret: str,
        filter: dict[str, object],
        target_backend: str,
        target_model: str,
        target_options: dict[str, object],
        prompt_template: str,
        poll_interval_seconds: int,
        now: datetime | None = None,
    ) -> Hook:
        if source_type not in self._adapters:
            raise ValueError(f"Unsupported source type: {source_type}")
        hook_id = uuid4().hex
        connection_ref = uuid4().hex
        self._secret_store.save(connection_ref, secret)
        stamp = _now(now)
        filter_payload = dict(filter)
        filter_payload["_connection"] = connection
        self._db.execute(
            """
            insert into hooks (
                id, name, source_type, connection_ref, filter_json,
                target_backend, target_model, target_options_json, prompt_template,
                poll_interval_seconds, enabled, cursor_json, last_polled_at, last_error,
                created_at, updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, null, null, null, ?, ?)
            """,
            (
                hook_id,
                name,
                source_type,
                connection_ref,
                json.dumps(filter_payload, sort_keys=True),
                target_backend,
                target_model,
                json.dumps(target_options, sort_keys=True),
                prompt_template,
                poll_interval_seconds,
                stamp,
                stamp,
            ),
        )
        return self.get_hook(hook_id)

    def get_hook(self, hook_id: str) -> Hook:
        row = self._db.fetchone("select * from hooks where id = ?", (hook_id,))
        if row is None:
            raise KeyError(f"Hook not found: {hook_id}")
        return _hook_from_row(row)

    def list_hooks(self) -> list[Hook]:
        return [
            _hook_from_row(row)
            for row in self._db.fetchall("select * from hooks order by created_at desc")
        ]

    def set_enabled(self, hook_id: str, enabled: bool, now: datetime | None = None) -> Hook:
        self.get_hook(hook_id)
        self._db.execute(
            "update hooks set enabled = ?, updated_at = ? where id = ?",
            (1 if enabled else 0, _now(now), hook_id),
        )
        return self.get_hook(hook_id)

    def delete(self, hook_id: str) -> None:
        hook = self.get_hook(hook_id)
        self._db.execute("delete from hooks where id = ?", (hook_id,))
        self._secret_store.delete(hook.connection_ref)

    def due_hooks(self, now: datetime) -> list[Hook]:
        due: list[Hook] = []
        for hook in self.list_hooks():
            if not hook.enabled:
                continue
            if hook.last_polled_at is None:
                due.append(hook)
                continue
            last = datetime.fromisoformat(hook.last_polled_at)
            if last + timedelta(seconds=hook.poll_interval_seconds) <= now:
                due.append(hook)
        return due

    def poll_due(self, run_service: HookRunService, now: datetime | None = None) -> list[HookRun]:
        moment = now or datetime.now(timezone.utc)
        created: list[HookRun] = []
        for hook in self.due_hooks(moment):
            created.extend(self.poll_hook(hook.id, run_service, now=moment))
        return created

    def poll_hook(
        self,
        hook_id: str,
        run_service: HookRunService,
        now: datetime | None = None,
    ) -> list[HookRun]:
        hook = self.get_hook(hook_id)
        adapter = self._adapters[hook.source_type]
        secret = self._secret_store.load(hook.connection_ref) or ""
        stamp = _now(now)
        try:
            result = adapter.poll(hook.connection, secret, hook.cursor, hook.filter)
        except Exception as exc:
            self._db.execute(
                "update hooks set last_error = ?, updated_at = ? where id = ?",
                (str(exc)[:2000] or type(exc).__name__, stamp, hook_id),
            )
            return []
        created: list[HookRun] = []
        for event in result.events:
            run = run_service.create_run(
                hook_id, event.dedup_key, event.summary, event.payload, now=now
            )
            if run is not None:
                created.append(run)
        self._db.execute(
            "update hooks set cursor_json = ?, last_polled_at = ?, last_error = null, "
            "updated_at = ? where id = ?",
            (json.dumps(result.cursor, sort_keys=True), stamp, stamp, hook_id),
        )
        return created


def render_prompt(template: str, payload: dict[str, object]) -> str:
    return (
        template.replace("{{from}}", str(payload.get("from", "")))
        .replace("{{subject}}", str(payload.get("subject", "")))
        .replace("{{body}}", str(payload.get("body_text", "")))
        .replace("{{date}}", str(payload.get("date", "")))
    )


def _hook_from_row(row: object) -> Hook:
    filter_payload = json.loads(row["filter_json"])
    connection = filter_payload.pop("_connection", {})
    return Hook(
        id=row["id"],
        name=row["name"],
        source_type=row["source_type"],
        connection_ref=row["connection_ref"],
        connection=connection,
        filter=filter_payload,
        target_backend=row["target_backend"],
        target_model=row["target_model"],
        target_options=json.loads(row["target_options_json"]),
        prompt_template=row["prompt_template"],
        poll_interval_seconds=row["poll_interval_seconds"],
        enabled=bool(row["enabled"]),
        cursor=json.loads(row["cursor_json"]) if row["cursor_json"] else None,
        last_polled_at=row["last_polled_at"],
        last_error=row["last_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _now(value: datetime | None) -> str:
    return (value or datetime.now(timezone.utc)).isoformat()
