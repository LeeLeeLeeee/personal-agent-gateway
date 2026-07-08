import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from personal_agent_gateway.db import Database


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    role: str
    description: str
    responsibilities: list[str]
    constraints: list[str]
    default_backend: str
    default_model: str
    created_at: str
    updated_at: str


class PersonaService:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create_persona(
        self,
        name: str,
        role: str,
        description: str,
        responsibilities: list[str],
        constraints: list[str],
        default_backend: str = "codex",
        default_model: str = "default",
    ) -> Persona:
        persona_id = uuid4().hex
        now = _now()
        self._db.execute(
            """
            insert into personas (
                id, name, role, description, responsibilities_json,
                constraints_json, default_backend, default_model, created_at, updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                persona_id,
                name,
                role,
                description,
                json.dumps(responsibilities, ensure_ascii=False),
                json.dumps(constraints, ensure_ascii=False),
                default_backend,
                default_model,
                now,
                now,
            ),
        )
        return self.get_persona(persona_id)

    def get_persona(self, persona_id: str) -> Persona:
        row = self._db.fetchone("select * from personas where id = ?", (persona_id,))
        if row is None:
            raise KeyError(f"Persona not found: {persona_id}")
        return _persona_from_row(row)

    def list_personas(self) -> list[Persona]:
        return [
            _persona_from_row(row)
            for row in self._db.fetchall("select * from personas order by created_at asc")
        ]

    def update_persona(
        self,
        persona_id: str,
        name: str | None = None,
        role: str | None = None,
        description: str | None = None,
        responsibilities: list[str] | None = None,
        constraints: list[str] | None = None,
        default_backend: str | None = None,
        default_model: str | None = None,
    ) -> Persona:
        current = self.get_persona(persona_id)
        updated_at = _now()
        self._db.execute(
            """
            update personas
            set name = ?, role = ?, description = ?, responsibilities_json = ?,
                constraints_json = ?, default_backend = ?, default_model = ?, updated_at = ?
            where id = ?
            """,
            (
                name if name is not None else current.name,
                role if role is not None else current.role,
                description if description is not None else current.description,
                json.dumps(responsibilities if responsibilities is not None else current.responsibilities, ensure_ascii=False),
                json.dumps(constraints if constraints is not None else current.constraints, ensure_ascii=False),
                default_backend if default_backend is not None else current.default_backend,
                default_model if default_model is not None else current.default_model,
                updated_at,
                persona_id,
            ),
        )
        return self.get_persona(persona_id)

    def delete_persona(self, persona_id: str) -> None:
        self.get_persona(persona_id)
        self._db.execute("delete from personas where id = ?", (persona_id,))


def _persona_from_row(row) -> Persona:
    return Persona(
        id=row["id"],
        name=row["name"],
        role=row["role"],
        description=row["description"],
        responsibilities=list(json.loads(row["responsibilities_json"])),
        constraints=list(json.loads(row["constraints_json"])),
        default_backend=row["default_backend"],
        default_model=row["default_model"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
