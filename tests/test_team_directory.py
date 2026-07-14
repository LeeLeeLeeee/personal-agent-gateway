import pytest

from personal_agent_gateway.db import Database
from personal_agent_gateway.personas import PersonaService
from personal_agent_gateway.team_directory import TeamService


def make(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    return personas, TeamService(db, personas)


def test_create_and_get_team(tmp_path):
    personas, teams = make(tmp_path)
    lead = personas.create_persona("Lead", "lead", "d", [], [])
    m1 = personas.create_persona("M1", "eng", "d", [], [])
    team = teams.create_team("Release Crew", "ships", lead.id, [m1.id])
    got = teams.get_team(team.id)
    assert got.name == "Release Crew"
    assert got.leader_persona_id == lead.id
    assert got.member_persona_ids == [m1.id]


def test_create_rejects_unknown_persona(tmp_path):
    personas, teams = make(tmp_path)
    lead = personas.create_persona("Lead", "lead", "d", [], [])
    with pytest.raises(ValueError):
        teams.create_team("X", "", lead.id, ["nope"])


def test_update_team_roster(tmp_path):
    personas, teams = make(tmp_path)
    lead = personas.create_persona("Lead", "lead", "d", [], [])
    m1 = personas.create_persona("M1", "eng", "d", [], [])
    m2 = personas.create_persona("M2", "qa", "d", [], [])
    team = teams.create_team("T", "", lead.id, [m1.id])
    updated = teams.update_team(team.id, member_persona_ids=[m1.id, m2.id])
    assert updated.member_persona_ids == [m1.id, m2.id]


def test_delete_team(tmp_path):
    personas, teams = make(tmp_path)
    lead = personas.create_persona("Lead", "lead", "d", [], [])
    team = teams.create_team("T", "", lead.id, [])
    teams.delete_team(team.id)
    with pytest.raises(KeyError):
        teams.get_team(team.id)
