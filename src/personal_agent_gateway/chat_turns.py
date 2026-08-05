from dataclasses import dataclass
from datetime import datetime, timezone

from personal_agent_gateway.db import Database


@dataclass(frozen=True)
class ChatTurn:
    id: str
    session_id: str
    user_event_id: str | None
    prompt_excerpt: str
    status: str
    created_at: str
    finished_at: str | None


class ChatTurnService:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create(
        self,
        turn_id: str,
        session_id: str,
        prompt_excerpt: str,
        user_event_id: str | None = None,
    ) -> ChatTurn:
        now = _now()
        self._db.execute(
            """
            insert into chat_turns (
                id, session_id, user_event_id, prompt_excerpt, status, created_at, finished_at
            ) values (?, ?, ?, ?, ?, ?, null)
            """,
            (turn_id, session_id, user_event_id, prompt_excerpt, "running", now),
        )
        return self.get(turn_id)

    def finish(self, turn_id: str, status: str) -> ChatTurn:
        self._db.execute(
            "update chat_turns set status = ?, finished_at = ? where id = ?",
            (status, _now(), turn_id),
        )
        return self.get(turn_id)

    def get(self, turn_id: str) -> ChatTurn:
        row = self._db.fetchone("select * from chat_turns where id = ?", (turn_id,))
        if row is None:
            raise KeyError(f"Chat turn not found: {turn_id}")
        return ChatTurn(
            id=row["id"],
            session_id=row["session_id"],
            user_event_id=row["user_event_id"],
            prompt_excerpt=row["prompt_excerpt"],
            status=row["status"],
            created_at=row["created_at"],
            finished_at=row["finished_at"],
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
