import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from personal_agent_gateway.db import Database


@dataclass(frozen=True)
class SessionPrincipal:
    id: str
    created_at: datetime
    last_seen_at: datetime
    absolute_expires_at: datetime
    idle_expires_at: datetime


@dataclass(frozen=True)
class IssuedAuthSession:
    token: str
    principal: SessionPrincipal


@dataclass(frozen=True)
class AuthSessionInfo:
    id: str
    created_at: datetime
    last_seen_at: datetime
    absolute_expires_at: datetime
    idle_expires_at: datetime


class AuthSessionService:
    def __init__(
        self,
        database: Database,
        *,
        absolute_ttl_seconds: int,
        idle_ttl_seconds: int,
    ) -> None:
        if absolute_ttl_seconds <= 0 or idle_ttl_seconds <= 0:
            raise ValueError("Auth session TTL values must be positive")
        self._database = database
        self._absolute_ttl = timedelta(seconds=absolute_ttl_seconds)
        self._idle_ttl = timedelta(seconds=idle_ttl_seconds)

    def issue(self, *, now: datetime | None = None) -> IssuedAuthSession:
        issued_at = _normalize(now)
        absolute_expires_at = issued_at + self._absolute_ttl
        idle_expires_at = min(issued_at + self._idle_ttl, absolute_expires_at)
        token = secrets.token_urlsafe(32)
        principal = SessionPrincipal(
            id=uuid4().hex,
            created_at=issued_at,
            last_seen_at=issued_at,
            absolute_expires_at=absolute_expires_at,
            idle_expires_at=idle_expires_at,
        )
        self._database.execute(
            """
            insert into auth_sessions (
                id, token_hash, created_at, last_seen_at,
                absolute_expires_at, idle_expires_at, revoked_at
            )
            values (?, ?, ?, ?, ?, ?, null)
            """,
            (
                principal.id,
                _token_hash(token),
                principal.created_at.isoformat(),
                principal.last_seen_at.isoformat(),
                principal.absolute_expires_at.isoformat(),
                principal.idle_expires_at.isoformat(),
            ),
        )
        return IssuedAuthSession(token=token, principal=principal)

    def validate(
        self,
        token: str | None,
        *,
        now: datetime | None = None,
    ) -> SessionPrincipal | None:
        if not token:
            return None
        token_hash = _token_hash(token)
        row = self._database.fetchone(
            "select * from auth_sessions where token_hash = ?",
            (token_hash,),
        )
        if row is None or not hmac.compare_digest(str(row["token_hash"]), token_hash):
            return None
        if row["revoked_at"] is not None:
            return None

        checked_at = _normalize(now)
        absolute_expires_at = _parse(str(row["absolute_expires_at"]))
        idle_expires_at = _parse(str(row["idle_expires_at"]))
        if checked_at >= absolute_expires_at or checked_at >= idle_expires_at:
            return None

        refreshed_idle_expires_at = min(
            checked_at + self._idle_ttl,
            absolute_expires_at,
        )
        self._database.execute(
            """
            update auth_sessions
            set last_seen_at = ?, idle_expires_at = ?
            where id = ? and revoked_at is null
            """,
            (checked_at.isoformat(), refreshed_idle_expires_at.isoformat(), row["id"]),
        )
        return SessionPrincipal(
            id=str(row["id"]),
            created_at=_parse(str(row["created_at"])),
            last_seen_at=checked_at,
            absolute_expires_at=absolute_expires_at,
            idle_expires_at=refreshed_idle_expires_at,
        )

    def revoke(self, token: str | None, *, now: datetime | None = None) -> bool:
        if not token:
            return False
        token_hash = _token_hash(token)
        row = self._database.fetchone(
            "select id, revoked_at from auth_sessions where token_hash = ?",
            (token_hash,),
        )
        if row is None or row["revoked_at"] is not None:
            return False
        self._database.execute(
            "update auth_sessions set revoked_at = ? where id = ? and revoked_at is null",
            (_normalize(now).isoformat(), row["id"]),
        )
        return True

    def revoke_by_id(self, session_id: str, *, now: datetime | None = None) -> bool:
        row = self._database.fetchone(
            "select id, revoked_at from auth_sessions where id = ?",
            (session_id,),
        )
        if row is None or row["revoked_at"] is not None:
            return False
        self._database.execute(
            "update auth_sessions set revoked_at = ? where id = ? and revoked_at is null",
            (_normalize(now).isoformat(), session_id),
        )
        return True

    def list_sessions(self, *, now: datetime | None = None) -> list[AuthSessionInfo]:
        checked_at = _normalize(now)
        sessions: list[AuthSessionInfo] = []
        for row in self._database.fetchall(
            """
            select * from auth_sessions
            where revoked_at is null
            order by last_seen_at desc
            """
        ):
            absolute_expires_at = _parse(str(row["absolute_expires_at"]))
            idle_expires_at = _parse(str(row["idle_expires_at"]))
            if checked_at >= absolute_expires_at or checked_at >= idle_expires_at:
                continue
            sessions.append(
                AuthSessionInfo(
                    id=str(row["id"]),
                    created_at=_parse(str(row["created_at"])),
                    last_seen_at=_parse(str(row["last_seen_at"])),
                    absolute_expires_at=absolute_expires_at,
                    idle_expires_at=idle_expires_at,
                )
            )
        return sessions

    def revoke_all(self, *, now: datetime | None = None) -> int:
        active = self._database.fetchall(
            "select id from auth_sessions where revoked_at is null"
        )
        if not active:
            return 0
        self._database.execute(
            "update auth_sessions set revoked_at = ? where revoked_at is null",
            (_normalize(now).isoformat(),),
        )
        return len(active)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _parse(value: str) -> datetime:
    return _normalize(datetime.fromisoformat(value))
