import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import uuid4

from personal_agent_gateway.db import Database
from personal_agent_gateway.hook_runs import HookRun, HookRunService
from personal_agent_gateway.hook_secrets import HookSecretStore
from personal_agent_gateway.personas import PersonaService, persona_snapshot
from personal_agent_gateway.redaction import redact_text
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
    target_kind: Literal["agent", "persona", "team_run"]
    target_persona_id: str | None
    target_persona_snapshot: dict[str, object]
    target_team_run_id: str | None
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
        personas: PersonaService | None = None,
    ) -> None:
        self._db = db
        self._secret_store = secret_store
        self._adapters = adapters
        self._personas = personas

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
        target_kind: Literal["agent", "persona", "team_run"] = "agent",
        target_persona_id: str | None = None,
        target_team_run_id: str | None = None,
    ) -> Hook:
        if source_type not in self._adapters:
            raise ValueError(f"Unsupported source type: {source_type}")
        target_persona_snapshot: dict[str, object] = {}
        if target_kind == "persona":
            if not target_persona_id:
                raise ValueError("Persona target is required")
            if self._personas is None:
                raise ValueError("Persona targets are unavailable")
            try:
                persona = self._personas.get_persona(target_persona_id)
            except KeyError as exc:
                raise ValueError("Persona target not found") from exc
            target_persona_snapshot = persona_snapshot(persona)
            target_backend = persona.default_backend
            target_model = persona.default_model
            target_options = dict(persona.default_options)
        elif target_kind == "team_run":
            if not target_team_run_id:
                raise ValueError("Team Run target is required")
            self._validate_team_run_target(target_team_run_id)
        elif target_kind != "agent":
            raise ValueError(f"Unsupported target kind: {target_kind}")
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
                target_kind, target_persona_id, target_persona_snapshot_json,
                target_team_run_id,
                poll_interval_seconds, enabled, cursor_json, last_polled_at, last_error,
                created_at, updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, null, null, null, ?, ?)
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
                target_kind,
                target_persona_id,
                json.dumps(target_persona_snapshot, ensure_ascii=False, sort_keys=True),
                target_team_run_id,
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
        hook = self.get_hook(hook_id)
        if enabled and hook.target_kind == "team_run":
            if hook.target_team_run_id is None:
                raise ValueError("Team Run target is required")
            self._validate_team_run_target(hook.target_team_run_id)
        self._db.execute(
            "update hooks set enabled = ?, updated_at = ? where id = ?",
            (1 if enabled else 0, _now(now), hook_id),
        )
        return self.get_hook(hook_id)

    def _validate_team_run_target(self, team_run_id: str) -> None:
        target = self._db.fetchone(
            "select lifecycle_mode, run_mode, execution_policy from team_runs where id = ?",
            (team_run_id,),
        )
        if target is None:
            raise ValueError("Team Run target not found")
        if (
            target["lifecycle_mode"] != "continuous"
            or target["run_mode"] != "plan_and_execute"
            or target["execution_policy"] != "triggered"
        ):
            raise ValueError(
                "Hook target must be a continuous plan_and_execute TRIGGERED Team Run"
            )

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
            message = redact_text(exc, secrets=[secret])
            self._db.execute(
                "update hooks set last_error = ?, updated_at = ? where id = ?",
                (message or type(exc).__name__, stamp, hook_id),
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

    def verify_connection(
        self,
        source_type: str,
        connection: dict[str, object],
        secret: str,
        filter: dict[str, object],
    ) -> None:
        adapter = self._adapters.get(source_type)
        if adapter is None or not hasattr(adapter, "verify"):
            raise ValueError(f"Connection test not supported for source: {source_type}")
        folder = str(filter.get("folder") or "INBOX")
        adapter.verify(connection, secret, folder)

    def redact_error(self, hook_id: str, value: object) -> str:
        hook = self.get_hook(hook_id)
        secret = self._secret_store.load(hook.connection_ref)
        return redact_text(value, secrets=[secret] if secret else [])


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
        target_kind=row["target_kind"] if "target_kind" in row.keys() else "agent",
        target_persona_id=(
            row["target_persona_id"]
            if "target_persona_id" in row.keys()
            else None
        ),
        target_persona_snapshot=(
            json.loads(row["target_persona_snapshot_json"])
            if (
                "target_persona_snapshot_json" in row.keys()
                and row["target_persona_snapshot_json"]
            )
            else {}
        ),
        target_team_run_id=(
            row["target_team_run_id"]
            if "target_team_run_id" in row.keys()
            else None
        ),
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
