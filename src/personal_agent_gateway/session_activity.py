import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from personal_agent_gateway.db import Database


class SessionActivityEvent(BaseModel):
    id: int
    session_id: str
    event_seq: int
    type: str
    source: str
    payload: dict[str, object]
    transcript_event_id: str | None
    created_at: datetime

    def to_event_payload(self) -> dict[str, object]:
        payload = self.model_dump(mode="json")
        payload["created_at"] = self.created_at.isoformat().replace("+00:00", "Z")
        return payload


class SessionActivityService:
    def __init__(self, db: Database) -> None:
        self._db = db

    def record(
        self,
        session_id: str,
        event_type: str,
        source: str,
        payload: dict[str, object],
        transcript_event_id: str | None = None,
    ) -> SessionActivityEvent:
        created_at = datetime.now(UTC)
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        connection = self._db.connect()
        try:
            connection.execute("begin immediate")
            row = connection.execute(
                "select coalesce(max(event_seq), 0) + 1 as next_seq "
                "from session_activity_events where session_id = ?",
                (session_id,),
            ).fetchone()
            event_seq = int(row["next_seq"])
            cursor = connection.execute(
                """
                insert into session_activity_events
                  (session_id, event_seq, event_type, source, payload_json, transcript_event_id, created_at)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    event_seq,
                    event_type,
                    source,
                    payload_json,
                    transcript_event_id,
                    created_at.isoformat(),
                ),
            )
            event_id = int(cursor.lastrowid)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return SessionActivityEvent(
            id=event_id,
            session_id=session_id,
            event_seq=event_seq,
            type=event_type,
            source=source,
            payload=dict(payload),
            transcript_event_id=transcript_event_id,
            created_at=created_at,
        )

    def list(self, session_id: str) -> list[SessionActivityEvent]:
        rows = self._db.fetchall(
            """
            select id, session_id, event_seq, event_type, source, payload_json,
                   transcript_event_id, created_at
            from session_activity_events
            where session_id = ?
            order by event_seq asc
            """,
            (session_id,),
        )
        return [_event_from_row(row) for row in rows]

    def delete_session(self, session_id: str) -> None:
        self._db.execute(
            "delete from session_activity_events where session_id = ?",
            (session_id,),
        )


def _event_from_row(row: Any) -> SessionActivityEvent:
    return SessionActivityEvent(
        id=int(row["id"]),
        session_id=str(row["session_id"]),
        event_seq=int(row["event_seq"]),
        type=str(row["event_type"]),
        source=str(row["source"]),
        payload=json.loads(str(row["payload_json"])),
        transcript_event_id=row["transcript_event_id"],
        created_at=datetime.fromisoformat(str(row["created_at"])),
    )
