from personal_agent_gateway.db import Database
from personal_agent_gateway.personas import PersonaService
from personal_agent_gateway.teams import TeamRunService


def make_services(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace_root=tmp_path / "workspace")
    return personas, teams


def test_create_team_run_snapshots_personas(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("Tech Lead", "Planning", "Plans work.", ["Plan"], ["Stay scoped"])
    member = personas.create_persona("QA Tester", "Quality", "Checks risk.", ["Test"], ["Report evidence"])

    run = teams.create_team_run(
        goal="Design Agent Teams",
        leader_persona_id=leader.id,
        member_persona_ids=[member.id],
        run_mode="planning_only",
        max_workers=2,
    )

    agents = teams.list_agents(run.id)
    assert run.status == "draft"
    assert len(agents) == 2
    assert agents[0].persona_snapshot["name"] == "Tech Lead"
    assert agents[1].persona_snapshot["name"] == "QA Tester"

    personas.update_persona(member.id, name="Changed QA")

    unchanged = teams.list_agents(run.id)[1]
    assert unchanged.persona_snapshot["name"] == "QA Tester"


def test_append_team_message(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    personas, teams = make_services(tmp_path)
    leader = personas.create_persona("Tech Lead", "Planning", "Plans work.", ["Plan"], [])
    run = teams.create_team_run("Goal", leader.id, [], "planning_only", 1)
    agent = teams.list_agents(run.id)[0]

    message = teams.append_message(run.id, agent.id, None, "note", "Planning started", {"phase": "planning"})

    assert message.content == "Planning started"
    assert teams.list_messages(run.id)[0].metadata == {"phase": "planning"}
