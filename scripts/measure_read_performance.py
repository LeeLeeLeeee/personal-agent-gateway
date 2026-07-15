"""Measure bounded local read paths using disposable fixtures only."""

from __future__ import annotations

import json
import platform
import statistics
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from personal_agent_gateway.db import Database
from personal_agent_gateway.session_activity import SessionActivityService
from personal_agent_gateway.transcript import TranscriptStore


def _measure(callable_, repeats: int = 15) -> dict[str, float]:
    callable_()
    samples = []
    for _ in range(repeats):
        started = perf_counter()
        callable_()
        samples.append((perf_counter() - started) * 1000)
    ordered = sorted(samples)
    return {
        "p50_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 3),
    }


def _session_measurements(root: Path) -> list[dict[str, object]]:
    measurements = []
    for count in (10, 100, 1000):
        session_dir = root / f"sessions-{count}"
        store = TranscriptStore(session_dir)
        for index in range(count):
            store.start_new()
            store.append("user", {"content": f"Session {index}"})
        legacy = _measure(lambda: store.list_sessions()[:100])

        db = Database(root / f"sessions-{count}.db")
        db.initialize()
        rebuild_started = perf_counter()
        store.attach_database(db)
        rebuild_ms = (perf_counter() - rebuild_started) * 1000
        indexed = _measure(lambda: store.page_sessions(limit=100))
        page, _ = store.page_sessions(limit=100)
        payload_bytes = len(
            json.dumps(
                [item.model_dump(mode="json") for item in page],
                ensure_ascii=False,
                default=str,
            ).encode()
        )
        measurements.append(
            {
                "path": "session_list",
                "fixture_count": count,
                "legacy": legacy,
                "indexed": indexed,
                "one_time_rebuild_ms": round(rebuild_ms, 3),
                "page_items": len(page),
                "payload_bytes": payload_bytes,
            }
        )
    return measurements


def _activity_measurements(root: Path) -> list[dict[str, object]]:
    measurements = []
    for count in (100, 1000, 10000):
        db = Database(root / f"activity-{count}.db")
        db.initialize()
        now = datetime.now(UTC).isoformat()
        with db.connection() as connection:
            connection.executemany(
                """
                insert into session_activity_events (
                    session_id, event_seq, event_type, source, payload_json,
                    transcript_event_id, created_at
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("session-a", index, "runtime.event", "runtime", "{}", None, now)
                    for index in range(1, count + 1)
                ],
            )
        service = SessionActivityService(db)
        timing = _measure(lambda: service.page("session-a", limit=200))
        page, _ = service.page("session-a", limit=200)
        payload_bytes = len(
            json.dumps([event.to_event_payload() for event in page]).encode()
        )
        measurements.append(
            {
                "path": "session_activity_page",
                "fixture_count": count,
                **timing,
                "page_items": len(page),
                "payload_bytes": payload_bytes,
            }
        )
    return measurements


def _document_measurements(root: Path) -> list[dict[str, object]]:
    measurements = []
    for count in (100, 1000):
        document_root = root / f"documents-{count}"
        document_root.mkdir()
        for index in range(count):
            (document_root / f"{index:04d}.md").write_text("fixture", encoding="utf-8")

        def scan() -> tuple[int, int]:
            files = [path for path in document_root.rglob("*") if path.is_file()]
            return len(files), sum(path.stat().st_size for path in files)

        timing = _measure(scan)
        measurements.append(
            {
                "path": "team_document_summary",
                "fixture_count": count,
                **timing,
                "summary": dict(zip(("count", "size_bytes"), scan(), strict=True)),
            }
        )
    return measurements


def _team_task_measurements(root: Path) -> list[dict[str, object]]:
    measurements = []
    for count in (10, 100):
        db = Database(root / f"team-tasks-{count}.db")
        db.initialize()
        now = datetime.now(UTC).isoformat()
        db.execute(
            """
            insert into team_runs (
                id, goal, status, run_mode, max_workers, rounds_budget,
                rounds_used, workspace_root, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("run", "fixture", "running", "plan_and_execute", 1, 8, 0, str(root), now, now),
        )
        with db.connection() as connection:
            connection.executemany(
                """
                insert into team_tasks (
                    id, team_run_id, title, description, status, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (f"task-{index:04d}", "run", f"Task {index}", "fixture", "pending", now, now)
                    for index in range(count)
                ],
            )

        def read_tasks():
            return db.fetchall(
                "select * from team_tasks where team_run_id = ? "
                "order by created_at asc, id asc limit ?",
                ("run", 200),
            )

        timing = _measure(read_tasks)
        rows = read_tasks()
        measurements.append(
            {
                "path": "team_task_list",
                "fixture_count": count,
                **timing,
                "page_items": len(rows),
            }
        )
    return measurements


def _query_plans(root: Path) -> dict[str, list[str]]:
    db = Database(root / "plans.db")
    db.initialize()
    queries = {
        "jobs_status_created": (
            "select * from jobs where status = ? order by created_at desc limit ?",
            ("running", 100),
        ),
        "job_events_job_created": (
            "select * from job_events where job_id = ? order by created_at desc, id desc limit ?",
            ("job", 200),
        ),
        "artifacts_created": (
            "select * from artifacts order by created_at desc, id desc limit ?",
            (100,),
        ),
        "team_runs_status_created": (
            "select * from team_runs where status = ? order by created_at desc limit ?",
            ("running", 100),
        ),
        "team_tasks_run_status_created": (
            "select * from team_tasks where team_run_id = ? and status = ? order by created_at",
            ("run", "pending"),
        ),
        "activity_session_seq": (
            "select * from session_activity_events where session_id = ? "
            "order by event_seq desc limit ?",
            ("session", 200),
        ),
        "team_tasks_run_created": (
            "select * from team_tasks where team_run_id = ? "
            "order by created_at, id limit ?",
            ("run", 200),
        ),
    }
    return {
        name: [
            str(row["detail"])
            for row in db.fetchall(f"explain query plan {query}", parameters)
        ]
        for name, (query, parameters) in queries.items()
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="pag-read-performance-") as temporary:
        root = Path(temporary)
        report = {
            "measured_at": datetime.now(UTC).isoformat(),
            "environment": {
                "python": platform.python_version(),
                "platform": platform.platform(),
            },
            "measurements": [
                *_session_measurements(root),
                *_activity_measurements(root),
                *_team_task_measurements(root),
                *_document_measurements(root),
            ],
            "query_plans": _query_plans(root),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
