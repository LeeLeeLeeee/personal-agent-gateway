import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from personal_agent_gateway.db import Database


@dataclass(frozen=True)
class HookRun:
    id: str
    hook_id: str
    dedup_key: str
    trigger_summary: str
    trigger_payload: dict[str, object]
    status: str
    result_text: str | None
    error_message: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    team_run_cycle_id: str | None = None


class HookRunService:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create_run(
        self,
        hook_id: str,
        dedup_key: str,
        trigger_summary: str,
        trigger_payload: dict[str, object],
        now: datetime | None = None,
    ) -> HookRun | None:
        run_id = uuid4().hex
        created_at = _now(now)
        try:
            self._db.execute(
                """
                insert into hook_runs (
                    id, hook_id, dedup_key, trigger_summary, trigger_payload_json,
                    status, created_at
                )
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    hook_id,
                    dedup_key,
                    trigger_summary,
                    json.dumps(trigger_payload, sort_keys=True),
                    "queued",
                    created_at,
                ),
            )
        except sqlite3.IntegrityError:
            return None
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> HookRun:
        row = self._db.fetchone("select * from hook_runs where id = ?", (run_id,))
        if row is None:
            raise KeyError(f"Hook run not found: {run_id}")
        return _run_from_row(row)

    def list_runs(self, hook_id: str) -> list[HookRun]:
        return [
            _run_from_row(row)
            for row in self._db.fetchall(
                "select * from hook_runs where hook_id = ? order by created_at desc",
                (hook_id,),
            )
        ]

    def mark_running(self, run_id: str) -> HookRun:
        self._db.execute(
            "update hook_runs set status = 'running', started_at = ? where id = ?",
            (_now(None), run_id),
        )
        return self.get_run(run_id)

    def mark_succeeded(self, run_id: str, result_text: str) -> HookRun:
        self._db.execute(
            "update hook_runs set status = 'succeeded', result_text = ?, finished_at = ? "
            "where id = ?",
            (result_text, _now(None), run_id),
        )
        return self.get_run(run_id)

    def mark_failed(self, run_id: str, message: str) -> HookRun:
        self._db.execute(
            "update hook_runs set status = 'failed', error_message = ?, finished_at = ? "
            "where id = ?",
            (message, _now(None), run_id),
        )
        return self.get_run(run_id)

    def mark_waiting_for_user(self, run_id: str) -> HookRun:
        self._db.execute(
            "update hook_runs set status = 'waiting_for_user' where id = ?",
            (run_id,),
        )
        return self.get_run(run_id)

    def mark_interrupted(self, run_id: str, message: str) -> HookRun:
        self._db.execute(
            "update hook_runs set status = 'interrupted', error_message = ? where id = ?",
            (message, run_id),
        )
        return self.get_run(run_id)

    def link_cycle(self, run_id: str, cycle_id: str) -> HookRun:
        run = self.get_run(run_id)
        if run.team_run_cycle_id is not None:
            if run.team_run_cycle_id != cycle_id:
                raise ValueError("Hook Run is already linked to another Team Run Cycle")
            return run
        self._db.execute(
            "update hook_runs set team_run_cycle_id = ? where id = ?",
            (cycle_id, run_id),
        )
        return self.get_run(run_id)

    def get_run_for_cycle(self, cycle_id: str) -> HookRun | None:
        row = self._db.fetchone(
            "select * from hook_runs where team_run_cycle_id = ?", (cycle_id,)
        )
        return _run_from_row(row) if row is not None else None

    def next_queued_for_team_run(self, team_run_id: str) -> HookRun | None:
        row = self._db.fetchone(
            """
            select hook_runs.* from hook_runs
            join team_run_cycles on team_run_cycles.id = hook_runs.team_run_cycle_id
            where team_run_cycles.team_run_id = ? and hook_runs.status = 'queued'
            order by team_run_cycles.sequence asc limit 1
            """,
            (team_run_id,),
        )
        return _run_from_row(row) if row is not None else None

    def list_queued_runs(self) -> list[HookRun]:
        return [
            _run_from_row(row)
            for row in self._db.fetchall(
                "select * from hook_runs where status = 'queued' order by created_at asc"
            )
        ]

    def list_active_runs(self) -> list[HookRun]:
        return [
            _run_from_row(row)
            for row in self._db.fetchall(
                """
                select * from hook_runs
                where status in ('queued', 'running')
                order by created_at asc
                """
            )
        ]

    def recover_interrupted_runs(self) -> None:
        for row in self._db.fetchall(
            "select id, team_run_cycle_id from hook_runs where status = 'running'"
        ):
            if row["team_run_cycle_id"]:
                self.mark_interrupted(
                    row["id"], "Gateway restarted while Team Run Cycle was running"
                )
            else:
                self.mark_failed(row["id"], "Gateway restarted while hook run was running")


def _run_from_row(row: object) -> HookRun:
    return HookRun(
        id=row["id"],
        hook_id=row["hook_id"],
        dedup_key=row["dedup_key"],
        trigger_summary=row["trigger_summary"],
        trigger_payload=json.loads(row["trigger_payload_json"]),
        status=row["status"],
        result_text=row["result_text"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        team_run_cycle_id=(
            row["team_run_cycle_id"]
            if "team_run_cycle_id" in row.keys()
            else None
        ),
    )


def _now(value: datetime | None) -> str:
    return (value or datetime.now(timezone.utc)).isoformat()
