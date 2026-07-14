import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from personal_agent_gateway.db import Database
from personal_agent_gateway.personas import PersonaService


@dataclass(frozen=True)
class Team:
    id: str
    name: str
    description: str
    leader_persona_id: str
    member_persona_ids: list[str]
    created_at: str
    updated_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TeamService:
    def __init__(self, db: Database, personas: PersonaService) -> None:
        self._db = db
        self._personas = personas

    def create_team(
        self, name: str, description: str, leader_persona_id: str, member_persona_ids: list[str]
    ) -> Team:
        self._validate_roster(leader_persona_id, member_persona_ids)
        team_id = uuid4().hex
        now = _now()
        self._db.execute(
            "insert into teams (id, name, description, leader_persona_id, member_persona_ids_json, "
            "created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?)",
            (team_id, name, description, leader_persona_id,
             json.dumps(member_persona_ids, ensure_ascii=False), now, now),
        )
        return self.get_team(team_id)

    def get_team(self, team_id: str) -> Team:
        row = self._db.fetchone("select * from teams where id = ?", (team_id,))
        if row is None:
            raise KeyError(f"Team not found: {team_id}")
        return _from_row(row)

    def list_teams(self) -> list[Team]:
        return [_from_row(row) for row in self._db.fetchall("select * from teams order by created_at asc")]

    def update_team(
        self,
        team_id: str,
        name: str | None = None,
        description: str | None = None,
        leader_persona_id: str | None = None,
        member_persona_ids: list[str] | None = None,
    ) -> Team:
        current = self.get_team(team_id)
        next_leader = leader_persona_id if leader_persona_id is not None else current.leader_persona_id
        next_members = member_persona_ids if member_persona_ids is not None else current.member_persona_ids
        self._validate_roster(next_leader, next_members)
        self._db.execute(
            "update teams set name = ?, description = ?, leader_persona_id = ?, "
            "member_persona_ids_json = ?, updated_at = ? where id = ?",
            (
                name if name is not None else current.name,
                description if description is not None else current.description,
                next_leader,
                json.dumps(next_members, ensure_ascii=False),
                _now(),
                team_id,
            ),
        )
        return self.get_team(team_id)

    def delete_team(self, team_id: str) -> None:
        self.get_team(team_id)
        self._db.execute("delete from teams where id = ?", (team_id,))

    def _validate_roster(self, leader_persona_id: str, member_persona_ids: list[str]) -> None:
        for persona_id in [leader_persona_id, *member_persona_ids]:
            try:
                self._personas.get_persona(persona_id)
            except KeyError as exc:
                raise ValueError(f"Unknown persona: {persona_id}") from exc


def _from_row(row) -> Team:
    return Team(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        leader_persona_id=row["leader_persona_id"],
        member_persona_ids=list(json.loads(row["member_persona_ids_json"])),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
