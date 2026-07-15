from datetime import datetime, timezone
from typing import Literal

from personal_agent_gateway.db import Database


AccessMode = Literal["restricted", "full_access"]


class SecuritySettingsService:
    def __init__(self, database: Database, default_access_mode: AccessMode = "restricted") -> None:
        self._database = database
        self._default_access_mode = default_access_mode

    @property
    def access_mode(self) -> AccessMode:
        row = self._database.fetchone(
            "select value from runtime_settings where key = 'access_mode'"
        )
        value = str(row["value"]) if row is not None else self._default_access_mode
        return "full_access" if value == "full_access" else "restricted"

    def set_access_mode(self, mode: AccessMode) -> AccessMode:
        if mode not in {"restricted", "full_access"}:
            raise ValueError(f"Unsupported access mode: {mode}")
        self._database.execute(
            """
            insert into runtime_settings (key, value, updated_at) values ('access_mode', ?, ?)
            on conflict(key) do update set value = excluded.value, updated_at = excluded.updated_at
            """,
            (mode, datetime.now(timezone.utc).isoformat()),
        )
        return mode
