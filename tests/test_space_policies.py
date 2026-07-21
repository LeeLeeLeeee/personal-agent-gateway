import subprocess
from pathlib import Path

from personal_agent_gateway.db import Database
from personal_agent_gateway.personas import PersonaService
from personal_agent_gateway.space_policies import SpacePolicyService, TeamSpaceManager
from personal_agent_gateway.team_directory import TeamService


def _services(tmp_path: Path):
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    personas = PersonaService(db)
    spaces = SpacePolicyService(db, default_home=tmp_path)
    spaces.seed_defaults()
    return db, personas, spaces, TeamService(db, personas, spaces)


def _persona(personas: PersonaService, name: str):
    return personas.create_persona(name, "role", "description", [], [])


def test_space_policy_resolution_prefers_team_then_persona_then_global(tmp_path: Path) -> None:
    _, personas, spaces, teams = _services(tmp_path)
    lead = _persona(personas, "Lead")
    team = teams.create_team("Crew", "", lead.id, [])
    persona_root = tmp_path / "persona"
    team_root = tmp_path / "team"
    persona_root.mkdir()
    team_root.mkdir()
    spaces.upsert(
        "persona",
        lead.id,
        read_mode="selected",
        read_path=str(persona_root),
        write_mode="isolated",
        workspace_path=None,
    )
    spaces.upsert(
        "team",
        team.id,
        read_mode="selected",
        read_path=str(team_root),
        write_mode="isolated",
        workspace_path=None,
    )

    assert spaces.resolve().source == "global"
    assert spaces.resolve(persona_id=lead.id).source == "persona"
    effective = spaces.resolve(team_id=team.id, persona_id=lead.id)
    assert effective.source == "team"
    assert effective.policy.read_path == str(team_root.resolve())


def test_persona_override_can_be_removed_to_inherit_global(tmp_path: Path) -> None:
    _, personas, spaces, _ = _services(tmp_path)
    persona = _persona(personas, "Solo")
    spaces.upsert(
        "persona",
        persona.id,
        read_mode="home",
        read_path=None,
        write_mode="isolated",
        workspace_path=None,
    )

    spaces.delete_persona_override(persona.id)

    assert spaces.resolve(persona_id=persona.id).source == "global"


def test_team_creation_always_creates_required_space_policy(tmp_path: Path) -> None:
    _, personas, spaces, teams = _services(tmp_path)
    lead = _persona(personas, "Lead")

    team = teams.create_team("Crew", "", lead.id, [])

    assert spaces.resolve(team_id=team.id).source == "team"
    assert [policy.scope_id for policy in spaces.list_team_policies()] == [team.id]


def test_worktree_mode_prepares_isolated_git_worktree(tmp_path: Path) -> None:
    _, personas, spaces, teams = _services(tmp_path)
    lead = _persona(personas, "Lead")
    team = teams.create_team("Crew", "", lead.id, [])
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
    (repository / "README.md").write_text("root", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-m", "init"], check=True, capture_output=True)
    policy = spaces.upsert(
        "team",
        team.id,
        read_mode="home",
        read_path=None,
        write_mode="worktree",
        workspace_path=str(repository),
    )
    run_root = tmp_path / "runs" / "run-1"
    run_root.mkdir(parents=True)

    prepared = TeamSpaceManager().prepare("run-1", run_root, policy)

    assert prepared.working_root == run_root / "project"
    assert (prepared.working_root / "README.md").read_text(encoding="utf-8") == "root"
    assert prepared.artifact_root == run_root / "artifacts"
    TeamSpaceManager().cleanup(
        run_root,
        policy,
        prepared.working_root,
        prepared.worktree_branch,
    )
    assert not run_root.exists()

