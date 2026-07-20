import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from personal_agent_gateway.db import Database
from personal_agent_gateway.teams import TeamRunCycle

if TYPE_CHECKING:
    from personal_agent_gateway.teams import TeamRunService


ExecutionPolicy = Literal["auto", "triggered"]
AutoSeriesStatus = Literal[
    "running",
    "waiting_interval",
    "paused_failure",
    "paused_user",
    "paused_interrupted",
    "auto_completed",
    "canceled",
]
CycleRequestStatus = Literal["queued", "dispatching", "settled", "canceled"]

_ACTIVE_SERIES_STATUSES = {
    "running",
    "waiting_interval",
    "paused_failure",
    "paused_user",
    "paused_interrupted",
}
_SETTLED_CYCLE_STATUSES = {"completed", "completed_with_failures"}
_TERMINAL_CYCLE_STATUSES = {
    "completed",
    "completed_with_failures",
    "failed",
    "canceled",
}


@dataclass(frozen=True)
class TeamCycleRequest:
    id: str
    team_run_id: str
    auto_series_id: str | None
    slot_ordinal: int | None
    source_type: str
    source_id: str
    status: CycleRequestStatus
    instruction: str
    previous_cycle_id: str | None
    previous_summary_text: str | None
    retry_of_request_id: str | None
    created_at: str
    claimed_at: str | None
    settled_at: str | None
    updated_at: str


@dataclass(frozen=True)
class TeamAutoSeries:
    id: str
    team_run_id: str
    series_number: int
    status: AutoSeriesStatus
    target_slots: int
    settled_slots: int
    interval_seconds: int
    next_run_at: str | None
    pause_reason: str | None
    paused_cycle_id: str | None
    created_at: str
    started_at: str
    completed_at: str | None
    updated_at: str


@dataclass(frozen=True)
class CycleSettlement:
    request: TeamCycleRequest
    series: TeamAutoSeries | None
    queue_ready: bool
    transitioned: bool


class TeamCycleService:
    def __init__(self, db: Database) -> None:
        self._db = db

    def enqueue_request(
        self,
        team_run_id: str,
        source_type: str,
        source_id: str,
        instruction: str,
        *,
        previous_cycle_id: str | None,
        auto_series_id: str | None = None,
        slot_ordinal: int | None = None,
        retry_of_request_id: str | None = None,
        now: datetime | None = None,
    ) -> TeamCycleRequest:
        timestamp = _timestamp(now)
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            return self._enqueue_request(
                connection,
                team_run_id,
                source_type,
                source_id,
                instruction,
                previous_cycle_id=previous_cycle_id,
                auto_series_id=auto_series_id,
                slot_ordinal=slot_ordinal,
                retry_of_request_id=retry_of_request_id,
                now=timestamp,
            )

    def create_auto_series(
        self,
        team_run_id: str,
        target_slots: int,
        interval_seconds: int,
        now: datetime | None = None,
    ) -> tuple[TeamAutoSeries, TeamCycleRequest]:
        timestamp = _timestamp(now)
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            self._require_policy(connection, team_run_id, "auto")
            return self.initialize_auto_series(
                connection,
                team_run_id,
                target_slots,
                interval_seconds,
                timestamp,
            )

    def initialize_auto_series(
        self,
        connection: sqlite3.Connection,
        team_run_id: str,
        target_slots: int,
        interval_seconds: int,
        now: str,
    ) -> tuple[TeamAutoSeries, TeamCycleRequest]:
        now = _timestamp(datetime.fromisoformat(now))
        if target_slots < 1:
            raise ValueError("AUTO repeat count must be positive")
        if interval_seconds < 60:
            raise ValueError("AUTO interval must be at least 60 seconds")
        active = connection.execute(
            """
            select id from team_run_auto_series
            where team_run_id = ? and status in (
                'running', 'waiting_interval', 'paused_failure',
                'paused_user', 'paused_interrupted'
            )
            """,
            (team_run_id,),
        ).fetchone()
        if active is not None:
            raise ValueError("Team run already has an active AUTO series")
        number_row = connection.execute(
            """
            select coalesce(max(series_number), 0) + 1 as next
            from team_run_auto_series where team_run_id = ?
            """,
            (team_run_id,),
        ).fetchone()
        series_id = uuid4().hex
        series_number = int(number_row["next"])
        try:
            connection.execute(
                """
                insert into team_run_auto_series (
                    id, team_run_id, series_number, status, target_slots,
                    settled_slots, interval_seconds, next_run_at, pause_reason,
                    paused_cycle_id, created_at, started_at, completed_at, updated_at
                ) values (?, ?, ?, 'running', ?, 0, ?, null, null, null, ?, ?, null, ?)
                """,
                (
                    series_id,
                    team_run_id,
                    series_number,
                    target_slots,
                    interval_seconds,
                    now,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            active = connection.execute(
                """
                select * from team_run_auto_series
                where team_run_id = ? and status in (
                    'running', 'waiting_interval', 'paused_failure',
                    'paused_user', 'paused_interrupted'
                )
                """,
                (team_run_id,),
            ).fetchone()
            if active is None:
                raise
            raise ValueError("Team run already has an active AUTO series") from exc
        request = self._enqueue_request(
            connection,
            team_run_id,
            "auto",
            _auto_source_id(series_id, 1, 1),
            "Continue the team goal.",
            previous_cycle_id=None,
            auto_series_id=series_id,
            slot_ordinal=1,
            retry_of_request_id=None,
            now=now,
        )
        return self._get_series(connection, series_id), request

    def claim_next(
        self,
        team_run_id: str,
        now: datetime | None = None,
    ) -> TeamCycleRequest | None:
        timestamp = _timestamp(now)
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            self._require_run(connection, team_run_id)
            active = connection.execute(
                """
                select id from team_cycle_requests
                where team_run_id = ? and status = 'dispatching'
                """,
                (team_run_id,),
            ).fetchone()
            if active is not None:
                return None
            row = connection.execute(
                """
                select * from team_cycle_requests
                where team_run_id = ? and status = 'queued'
                order by created_at asc, rowid asc limit 1
                """,
                (team_run_id,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                update team_cycle_requests
                set status = 'dispatching', claimed_at = ?, updated_at = ?
                where id = ? and status = 'queued'
                """,
                (timestamp, timestamp, row["id"]),
            )
            return self._get_request(connection, row["id"])

    def settle_cycle(
        self,
        cycle_id: str,
        now: datetime | None = None,
    ) -> CycleSettlement:
        timestamp = _timestamp(now)
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            cycle = connection.execute(
                "select * from team_run_cycles where id = ?", (cycle_id,)
            ).fetchone()
            if cycle is None:
                raise KeyError(f"Team run cycle not found: {cycle_id}")
            if cycle["request_id"] is None:
                raise ValueError("Team run cycle is not linked to a cycle request")
            request = self._get_request(connection, cycle["request_id"])
            series = (
                self._get_series(connection, request.auto_series_id)
                if request.auto_series_id is not None
                else None
            )
            if cycle["status"] == "waiting_for_user":
                return self._pause_cycle(
                    connection,
                    cycle,
                    request,
                    series,
                    "paused_user",
                    "waiting_for_user",
                    timestamp,
                )
            if cycle["status"] == "interrupted":
                return self._pause_cycle(
                    connection,
                    cycle,
                    request,
                    series,
                    "paused_interrupted",
                    "interrupted",
                    timestamp,
                )
            if cycle["status"] not in _TERMINAL_CYCLE_STATUSES:
                raise ValueError("Cycle is not ready to settle")
            if request.status in {"settled", "canceled"}:
                return CycleSettlement(
                    request,
                    series,
                    False,
                    False,
                )
            if request.status != "dispatching":
                raise ValueError("Cycle request must be dispatching before settlement")
            request_status = "canceled" if cycle["status"] == "canceled" else "settled"
            connection.execute(
                """
                update team_cycle_requests
                set status = ?, settled_at = ?, updated_at = ? where id = ?
                """,
                (request_status, timestamp, timestamp, request.id),
            )
            if series is not None:
                if cycle["status"] in _SETTLED_CYCLE_STATUSES:
                    self._settle_auto_slot(connection, series, timestamp)
                elif cycle["status"] == "failed":
                    connection.execute(
                        """
                        update team_run_auto_series
                        set status = 'paused_failure', next_run_at = null,
                            pause_reason = ?, paused_cycle_id = ?, updated_at = ?
                        where id = ?
                        """,
                        (
                            cycle["error_message"] or "Cycle failed",
                            cycle_id,
                            timestamp,
                            series.id,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        update team_run_auto_series
                        set status = 'canceled', next_run_at = null,
                            pause_reason = 'canceled', paused_cycle_id = ?,
                            completed_at = ?, updated_at = ?
                        where id = ?
                        """,
                        (cycle_id, timestamp, timestamp, series.id),
                    )
                series = self._get_series(connection, series.id)
            request = self._get_request(connection, request.id)
            return CycleSettlement(
                request,
                series,
                self._queue_ready(connection, request.team_run_id),
                True,
            )

    def continue_failed(
        self,
        team_run_id: str,
        series_id: str,
        now: datetime | None = None,
    ) -> TeamAutoSeries:
        timestamp = _timestamp(now)
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            series = self._get_series(connection, series_id)
            if series.team_run_id != team_run_id:
                raise ValueError("AUTO series belongs to a different team run")
            if series.status != "paused_failure":
                raise ValueError("AUTO series is not paused after a failure")
            self._settle_auto_slot(connection, series, timestamp)
            return self._get_series(connection, series_id)

    def retry_failed(
        self,
        team_run_id: str,
        series_id: str,
        now: datetime | None = None,
    ) -> TeamCycleRequest:
        timestamp = _timestamp(now)
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            series = self._get_series(connection, series_id)
            if series.team_run_id != team_run_id:
                raise ValueError("AUTO series belongs to a different team run")
            if series.status != "paused_failure":
                raise ValueError("AUTO series is not paused after a failure")
            failed = connection.execute(
                """
                select request.* from team_cycle_requests request
                join team_run_cycles cycle on cycle.request_id = request.id
                where cycle.id = ? and request.auto_series_id = ?
                """,
                (series.paused_cycle_id, series_id),
            ).fetchone()
            if failed is None:
                raise ValueError("Paused AUTO series has no failed request")
            attempt_row = connection.execute(
                """
                select count(*) + 1 as next from team_cycle_requests
                where auto_series_id = ? and slot_ordinal = ?
                """,
                (series_id, failed["slot_ordinal"]),
            ).fetchone()
            attempt = int(attempt_row["next"])
            return self._enqueue_request(
                connection,
                team_run_id,
                "retry",
                _auto_source_id(series_id, failed["slot_ordinal"], attempt),
                failed["instruction"],
                previous_cycle_id=failed["previous_cycle_id"],
                auto_series_id=series_id,
                slot_ordinal=failed["slot_ordinal"],
                retry_of_request_id=failed["id"],
                now=timestamp,
                previous_snapshot=(
                    failed["previous_cycle_id"],
                    failed["previous_summary_text"],
                ),
            )

    def restart_series(
        self,
        team_run_id: str,
        now: datetime | None = None,
    ) -> tuple[TeamAutoSeries, TeamCycleRequest]:
        timestamp = _timestamp(now)
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            self._require_policy(connection, team_run_id, "auto")
            latest = connection.execute(
                """
                select * from team_run_auto_series
                where team_run_id = ?
                order by series_number desc limit 1
                """,
                (team_run_id,),
            ).fetchone()
            if latest is None:
                raise KeyError(f"AUTO series not found for team run: {team_run_id}")
            if latest["status"] != "auto_completed":
                raise ValueError("AUTO series can only restart after completion")
            return self.initialize_auto_series(
                connection,
                team_run_id,
                latest["target_slots"],
                latest["interval_seconds"],
                timestamp,
            )

    def latest_settled_cycle(self, team_run_id: str) -> TeamRunCycle | None:
        self._require_run_read(team_run_id)
        row = self._db.fetchone(
            """
            select * from team_run_cycles
            where team_run_id = ?
              and status in ('completed', 'completed_with_failures')
            order by sequence desc limit 1
            """,
            (team_run_id,),
        )
        return _cycle_from_row(row) if row is not None else None

    def get_request(self, request_id: str) -> TeamCycleRequest:
        row = self._db.fetchone("select * from team_cycle_requests where id = ?", (request_id,))
        if row is None:
            raise KeyError(f"Team cycle request not found: {request_id}")
        return _request_from_row(row)

    def list_requests(self, team_run_id: str) -> list[TeamCycleRequest]:
        self._require_run_read(team_run_id)
        return [
            _request_from_row(row)
            for row in self._db.fetchall(
                """
                select * from team_cycle_requests
                where team_run_id = ? order by created_at asc, rowid asc
                """,
                (team_run_id,),
            )
        ]

    def get_active_series(self, team_run_id: str) -> TeamAutoSeries | None:
        self._require_run_read(team_run_id)
        placeholders = ", ".join("?" for _ in _ACTIVE_SERIES_STATUSES)
        row = self._db.fetchone(
            f"""
            select * from team_run_auto_series
            where team_run_id = ? and status in ({placeholders})
            order by series_number desc limit 1
            """,
            (team_run_id, *_ACTIVE_SERIES_STATUSES),
        )
        return _series_from_row(row) if row is not None else None

    def get_dispatching(self, team_run_id: str) -> TeamCycleRequest | None:
        self._require_run_read(team_run_id)
        row = self._db.fetchone(
            """
            select * from team_cycle_requests
            where team_run_id = ? and status = 'dispatching'
            """,
            (team_run_id,),
        )
        return _request_from_row(row) if row is not None else None

    def count_queued(self, team_run_id: str) -> int:
        self._require_run_read(team_run_id)
        row = self._db.fetchone(
            """
            select count(*) as total from team_cycle_requests
            where team_run_id = ? and status = 'queued'
            """,
            (team_run_id,),
        )
        return int(row["total"])

    def policy_status(self, team_run_id: str) -> str:
        self._require_run_read(team_run_id)
        series = self.get_active_series(team_run_id)
        if series is not None and series.status in {
            "waiting_interval",
            "paused_failure",
            "paused_user",
            "paused_interrupted",
        }:
            return series.status
        if self.get_dispatching(team_run_id) is not None:
            return "running"
        if self.count_queued(team_run_id):
            return "queued"
        if series is not None:
            return series.status
        latest = self._db.fetchone(
            """
            select status from team_run_auto_series
            where team_run_id = ? order by series_number desc limit 1
            """,
            (team_run_id,),
        )
        return latest["status"] if latest is not None else "ready"

    def enqueue_due_auto_requests(
        self,
        now: datetime | None = None,
    ) -> list[TeamCycleRequest]:
        timestamp = _timestamp(now)
        created: list[TeamCycleRequest] = []
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            due = connection.execute(
                """
                select * from team_run_auto_series
                where status = 'waiting_interval' and next_run_at <= ?
                order by next_run_at asc, id asc
                """,
                (timestamp,),
            ).fetchall()
            for row in due:
                series = _series_from_row(row)
                slot = series.settled_slots + 1
                previous = connection.execute(
                    """
                    select * from team_run_cycles
                    where team_run_id = ?
                      and status in ('completed', 'completed_with_failures')
                    order by sequence desc limit 1
                    """,
                    (series.team_run_id,),
                ).fetchone()
                request = self._enqueue_request(
                    connection,
                    series.team_run_id,
                    "auto",
                    _auto_source_id(series.id, slot, 1),
                    "Continue the team goal.",
                    previous_cycle_id=previous["id"] if previous is not None else None,
                    auto_series_id=series.id,
                    slot_ordinal=slot,
                    retry_of_request_id=None,
                    now=timestamp,
                )
                connection.execute(
                    """
                    update team_run_auto_series
                    set status = 'running', next_run_at = null, updated_at = ?
                    where id = ? and status = 'waiting_interval'
                    """,
                    (timestamp, series.id),
                )
                created.append(request)
        return created

    def mark_request_settled(
        self,
        request_id: str,
        now: datetime | None = None,
    ) -> TeamCycleRequest:
        timestamp = _timestamp(now)
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            request = self._get_request(connection, request_id)
            if request.status == "canceled":
                raise ValueError("Canceled cycle request cannot be settled")
            if request.status != "settled":
                connection.execute(
                    """
                    update team_cycle_requests
                    set status = 'settled', settled_at = ?, updated_at = ?
                    where id = ?
                    """,
                    (timestamp, timestamp, request_id),
                )
            return self._get_request(connection, request_id)

    def queue_position(self, request_id: str) -> int:
        request = self.get_request(request_id)
        if request.status != "queued":
            return 0
        insertion = self._db.fetchone(
            "select rowid as insertion_order from team_cycle_requests where id = ?",
            (request_id,),
        )
        row = self._db.fetchone(
            """
            select count(*) as total from team_cycle_requests
            where team_run_id = ? and status = 'queued'
              and (
                  created_at < ?
                  or (created_at = ? and rowid <= ?)
              )
            """,
            (
                request.team_run_id,
                request.created_at,
                request.created_at,
                insertion["insertion_order"],
            ),
        )
        return int(row["total"])

    def reconcile(
        self,
        teams: "TeamRunService",
        now: datetime | None = None,
    ) -> list[str]:
        for request in self.list_dispatching_requests():
            cycle = teams.get_cycle_for_request(request.id)
            if cycle is None:
                self.requeue_claim(request.id)
            elif cycle.status in _TERMINAL_CYCLE_STATUSES:
                self.settle_cycle(cycle.id, now=now)
            elif cycle.status == "interrupted":
                self.pause_interrupted(cycle.id)
            elif cycle.status == "waiting_for_user":
                self.pause_for_user(cycle.id)
        self.enqueue_due_auto_requests(now=now)
        return self.list_runnable_team_run_ids()

    def list_dispatching_requests(self) -> list[TeamCycleRequest]:
        return [
            _request_from_row(row)
            for row in self._db.fetchall(
                """
                select * from team_cycle_requests where status = 'dispatching'
                order by created_at asc, rowid asc
                """
            )
        ]

    def requeue_claim(self, request_id: str) -> TeamCycleRequest:
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            request = self._get_request(connection, request_id)
            if request.status != "dispatching":
                raise ValueError("Only a dispatching request can be requeued")
            cycle = connection.execute(
                "select id from team_run_cycles where request_id = ?", (request_id,)
            ).fetchone()
            if cycle is not None:
                raise ValueError("A request with a cycle cannot be requeued")
            connection.execute(
                """
                update team_cycle_requests
                set status = 'queued', claimed_at = null, updated_at = ?
                where id = ?
                """,
                (_timestamp(), request_id),
            )
            return self._get_request(connection, request_id)

    def pause_for_user(self, cycle_id: str) -> CycleSettlement:
        return self._pause_by_id(
            cycle_id,
            expected_cycle_status="waiting_for_user",
            series_status="paused_user",
            pause_reason="waiting_for_user",
        )

    def pause_interrupted(self, cycle_id: str) -> CycleSettlement:
        return self._pause_by_id(
            cycle_id,
            expected_cycle_status="interrupted",
            series_status="paused_interrupted",
            pause_reason="interrupted",
        )

    def list_runnable_team_run_ids(self) -> list[str]:
        return [
            row["team_run_id"]
            for row in self._db.fetchall(
                """
                select request.team_run_id, min(request.created_at) as first_created
                from team_cycle_requests request
                where request.status = 'queued'
                  and not exists (
                      select 1 from team_cycle_requests active
                      where active.team_run_id = request.team_run_id
                        and active.status = 'dispatching'
                  )
                group by request.team_run_id
                order by first_created asc, request.team_run_id asc
                """
            )
        ]

    def _enqueue_request(
        self,
        connection: sqlite3.Connection,
        team_run_id: str,
        source_type: str,
        source_id: str,
        instruction: str,
        *,
        previous_cycle_id: str | None,
        auto_series_id: str | None,
        slot_ordinal: int | None,
        retry_of_request_id: str | None,
        now: str,
        previous_snapshot: tuple[str | None, str | None] | None = None,
    ) -> TeamCycleRequest:
        normalized_source_type = source_type.strip()
        normalized_source_id = source_id.strip()
        if not normalized_source_type or not normalized_source_id:
            raise ValueError("Cycle request source type and source id are required")
        expected_policy: ExecutionPolicy
        if normalized_source_type in {"manual", "hook"}:
            expected_policy = "triggered"
        elif normalized_source_type in {"auto", "retry"}:
            expected_policy = "auto"
        else:
            raise ValueError(f"Unsupported cycle request source: {normalized_source_type}")
        self._require_policy(connection, team_run_id, expected_policy)
        existing = connection.execute(
            """
            select * from team_cycle_requests
            where team_run_id = ? and source_type = ? and source_id = ?
            """,
            (team_run_id, normalized_source_type, normalized_source_id),
        ).fetchone()
        if existing is not None:
            return _request_from_row(existing)
        if normalized_source_type in {"auto", "retry"} and auto_series_id is None:
            raise ValueError("AUTO cycle requests require an AUTO series")
        if normalized_source_type in {"manual", "hook"} and auto_series_id is not None:
            raise ValueError("TRIGGERED cycle requests cannot have an AUTO series")
        if normalized_source_type == "retry" and retry_of_request_id is None:
            raise ValueError("Retry cycle requests require failed request lineage")
        if normalized_source_type != "retry" and retry_of_request_id is not None:
            raise ValueError("Only retry cycle requests can have retry lineage")
        if previous_snapshot is not None:
            previous_cycle_id, previous_summary = previous_snapshot
        elif previous_cycle_id is not None:
            previous = connection.execute(
                "select * from team_run_cycles where id = ?", (previous_cycle_id,)
            ).fetchone()
            if previous is None:
                raise ValueError("Previous cycle is not a settled cycle for this team run")
            if (
                previous["team_run_id"] != team_run_id
                or previous["status"] not in _SETTLED_CYCLE_STATUSES
            ):
                raise ValueError("Previous cycle is not a settled cycle for this team run")
            previous_summary = previous["summary"]
        else:
            previous_summary = None
        if auto_series_id is not None:
            series = self._get_series(connection, auto_series_id)
            if series.team_run_id != team_run_id:
                raise ValueError("AUTO series belongs to a different team run")
            if slot_ordinal is None:
                raise ValueError("AUTO cycle requests require a slot ordinal")
            if series.status not in _ACTIVE_SERIES_STATUSES:
                raise ValueError("AUTO cycle requests require an active AUTO series")
            if (
                slot_ordinal < 1
                or slot_ordinal > series.target_slots
                or slot_ordinal != series.settled_slots + 1
            ):
                raise ValueError("AUTO cycle request slot is outside the current series slot")
        elif slot_ordinal is not None or retry_of_request_id is not None:
            raise ValueError("Non-AUTO cycle requests cannot have AUTO lineage")
        if retry_of_request_id is not None:
            retry_of = self._get_request(connection, retry_of_request_id)
            if (
                retry_of.team_run_id != team_run_id
                or retry_of.auto_series_id != auto_series_id
                or retry_of.slot_ordinal != slot_ordinal
            ):
                raise ValueError("Retry request lineage does not match the AUTO slot")
            failed_cycle = connection.execute(
                "select * from team_run_cycles where request_id = ?",
                (retry_of_request_id,),
            ).fetchone()
            if (
                series.status != "paused_failure"
                or retry_of.status != "settled"
                or failed_cycle is None
                or failed_cycle["status"] != "failed"
                or series.paused_cycle_id != failed_cycle["id"]
            ):
                raise ValueError("Retry lineage must reference the paused failed cycle")
        elif auto_series_id is not None:
            if series.status not in {"running", "waiting_interval"}:
                raise ValueError("AUTO series is not ready to enqueue an automatic slot")
            occupied = connection.execute(
                """
                select id from team_cycle_requests
                where auto_series_id = ? and slot_ordinal = ?
                """,
                (auto_series_id, slot_ordinal),
            ).fetchone()
            if occupied is not None:
                raise ValueError("AUTO series slot already has a cycle request")
        request_id = uuid4().hex
        try:
            connection.execute(
                """
                insert into team_cycle_requests (
                    id, team_run_id, auto_series_id, slot_ordinal, source_type,
                    source_id, status, instruction, previous_cycle_id,
                    previous_summary_text, retry_of_request_id, created_at,
                    claimed_at, settled_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, null, null, ?)
                """,
                (
                    request_id,
                    team_run_id,
                    auto_series_id,
                    slot_ordinal,
                    normalized_source_type,
                    normalized_source_id,
                    instruction,
                    previous_cycle_id,
                    previous_summary,
                    retry_of_request_id,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError:
            existing = connection.execute(
                """
                select * from team_cycle_requests
                where team_run_id = ? and source_type = ? and source_id = ?
                """,
                (team_run_id, normalized_source_type, normalized_source_id),
            ).fetchone()
            if existing is None:
                raise
            return _request_from_row(existing)
        if normalized_source_type == "retry":
            connection.execute(
                """
                update team_run_auto_series
                set status = 'running', next_run_at = null, pause_reason = null,
                    paused_cycle_id = null, updated_at = ? where id = ?
                """,
                (now, auto_series_id),
            )
        elif normalized_source_type == "auto" and series.status == "waiting_interval":
            connection.execute(
                """
                update team_run_auto_series
                set status = 'running', next_run_at = null, updated_at = ?
                where id = ?
                """,
                (now, auto_series_id),
            )
        return self._get_request(connection, request_id)

    def _pause_by_id(
        self,
        cycle_id: str,
        *,
        expected_cycle_status: str,
        series_status: Literal["paused_user", "paused_interrupted"],
        pause_reason: str,
    ) -> CycleSettlement:
        timestamp = _timestamp()
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            cycle = connection.execute(
                "select * from team_run_cycles where id = ?", (cycle_id,)
            ).fetchone()
            if cycle is None:
                raise KeyError(f"Team run cycle not found: {cycle_id}")
            if cycle["status"] != expected_cycle_status:
                raise ValueError(f"Cycle must be {expected_cycle_status} before it can be paused")
            if cycle["request_id"] is None:
                raise ValueError("Team run cycle is not linked to a cycle request")
            request = self._get_request(connection, cycle["request_id"])
            series = (
                self._get_series(connection, request.auto_series_id)
                if request.auto_series_id is not None
                else None
            )
            return self._pause_cycle(
                connection,
                cycle,
                request,
                series,
                series_status,
                pause_reason,
                timestamp,
            )

    def _pause_cycle(
        self,
        connection: sqlite3.Connection,
        cycle: sqlite3.Row,
        request: TeamCycleRequest,
        series: TeamAutoSeries | None,
        series_status: Literal["paused_user", "paused_interrupted"],
        pause_reason: str,
        now: str,
    ) -> CycleSettlement:
        if request.status != "dispatching":
            raise ValueError("Only a dispatching cycle request can be paused")
        transitioned = False
        if series is not None:
            transitioned = (
                series.status != series_status
                or series.pause_reason != pause_reason
                or series.paused_cycle_id != cycle["id"]
            )
            connection.execute(
                """
                update team_run_auto_series
                set status = ?, next_run_at = null, pause_reason = ?,
                    paused_cycle_id = ?, updated_at = ? where id = ?
                """,
                (series_status, pause_reason, cycle["id"], now, series.id),
            )
            series = self._get_series(connection, series.id)
        return CycleSettlement(request, series, False, transitioned)

    def _settle_auto_slot(
        self,
        connection: sqlite3.Connection,
        series: TeamAutoSeries,
        now: str,
    ) -> None:
        settled_slots = series.settled_slots + 1
        if settled_slots >= series.target_slots:
            connection.execute(
                """
                update team_run_auto_series
                set status = 'auto_completed', settled_slots = ?,
                    next_run_at = null, pause_reason = null,
                    paused_cycle_id = null, completed_at = ?, updated_at = ?
                where id = ?
                """,
                (settled_slots, now, now, series.id),
            )
            return
        next_run_at = (
            datetime.fromisoformat(now) + timedelta(seconds=series.interval_seconds)
        ).isoformat()
        connection.execute(
            """
            update team_run_auto_series
            set status = 'waiting_interval', settled_slots = ?,
                next_run_at = ?, pause_reason = null, paused_cycle_id = null,
                completed_at = null, updated_at = ? where id = ?
            """,
            (settled_slots, next_run_at, now, series.id),
        )

    def _require_policy(
        self,
        connection: sqlite3.Connection,
        team_run_id: str,
        expected: ExecutionPolicy,
    ) -> sqlite3.Row:
        run = self._require_run(connection, team_run_id)
        if run["lifecycle_mode"] != "continuous":
            raise ValueError("Cycle requests require a continuous team run")
        if run["execution_policy"] != expected:
            raise ValueError(f"Cycle request requires the {expected.upper()} execution policy")
        return run

    @staticmethod
    def _require_run(
        connection: sqlite3.Connection,
        team_run_id: str,
    ) -> sqlite3.Row:
        row = connection.execute("select * from team_runs where id = ?", (team_run_id,)).fetchone()
        if row is None:
            raise KeyError(f"Team run not found: {team_run_id}")
        return row

    def _require_run_read(self, team_run_id: str) -> None:
        if self._db.fetchone("select id from team_runs where id = ?", (team_run_id,)) is None:
            raise KeyError(f"Team run not found: {team_run_id}")

    @staticmethod
    def _get_request(
        connection: sqlite3.Connection,
        request_id: str,
    ) -> TeamCycleRequest:
        row = connection.execute(
            "select * from team_cycle_requests where id = ?", (request_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Team cycle request not found: {request_id}")
        return _request_from_row(row)

    @staticmethod
    def _get_series(
        connection: sqlite3.Connection,
        series_id: str,
    ) -> TeamAutoSeries:
        row = connection.execute(
            "select * from team_run_auto_series where id = ?", (series_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"AUTO series not found: {series_id}")
        return _series_from_row(row)

    @staticmethod
    def _queue_ready(connection: sqlite3.Connection, team_run_id: str) -> bool:
        row = connection.execute(
            """
            select exists(
                select 1 from team_cycle_requests
                where team_run_id = ? and status = 'queued'
            ) as queued,
            exists(
                select 1 from team_cycle_requests
                where team_run_id = ? and status = 'dispatching'
            ) as dispatching
            """,
            (team_run_id, team_run_id),
        ).fetchone()
        return bool(row["queued"]) and not bool(row["dispatching"])


def _timestamp(value: datetime | None = None) -> str:
    timestamp = value or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Cycle timestamps must be timezone-aware")
    return timestamp.astimezone(timezone.utc).isoformat()


def _auto_source_id(series_id: str, slot_ordinal: int, attempt: int) -> str:
    return f"{series_id}:{slot_ordinal}:{attempt}"


def _request_from_row(row: object) -> TeamCycleRequest:
    return TeamCycleRequest(
        id=row["id"],
        team_run_id=row["team_run_id"],
        auto_series_id=row["auto_series_id"],
        slot_ordinal=row["slot_ordinal"],
        source_type=row["source_type"],
        source_id=row["source_id"],
        status=row["status"],
        instruction=row["instruction"],
        previous_cycle_id=row["previous_cycle_id"],
        previous_summary_text=row["previous_summary_text"],
        retry_of_request_id=row["retry_of_request_id"],
        created_at=row["created_at"],
        claimed_at=row["claimed_at"],
        settled_at=row["settled_at"],
        updated_at=row["updated_at"],
    )


def _series_from_row(row: object) -> TeamAutoSeries:
    return TeamAutoSeries(
        id=row["id"],
        team_run_id=row["team_run_id"],
        series_number=row["series_number"],
        status=row["status"],
        target_slots=row["target_slots"],
        settled_slots=row["settled_slots"],
        interval_seconds=row["interval_seconds"],
        next_run_at=row["next_run_at"],
        pause_reason=row["pause_reason"],
        paused_cycle_id=row["paused_cycle_id"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        updated_at=row["updated_at"],
    )


def _cycle_from_row(row: object) -> TeamRunCycle:
    return TeamRunCycle(
        id=row["id"],
        team_run_id=row["team_run_id"],
        sequence=row["sequence"],
        source_type=row["source_type"],
        source_id=row["source_id"],
        status=row["status"],
        rounds_budget=row["rounds_budget"],
        rounds_used=row["rounds_used"],
        summary=row["summary"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        updated_at=row["updated_at"],
        request_id=row["request_id"],
    )
