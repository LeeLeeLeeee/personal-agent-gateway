import subprocess
from pathlib import Path

import pytest

from personal_agent_gateway.db import Database
from personal_agent_gateway.personas import PersonaService
from personal_agent_gateway.space_policies import SpacePolicy, SpacePolicyService, TeamSpaceManager
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
        read_mode="none",
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


def test_new_isolated_policies_default_to_explicit_no_source(tmp_path: Path) -> None:
    _, personas, spaces, teams = _services(tmp_path)
    lead = _persona(personas, "Lead")
    team = teams.create_team("Crew", "", lead.id, [])

    assert spaces.global_policy().read_mode == "none"
    assert spaces.global_policy().read_path is None
    assert spaces.resolve(team_id=team.id).policy.read_mode == "none"


def test_none_read_mode_rejects_a_read_path(tmp_path: Path) -> None:
    _, _, spaces, _ = _services(tmp_path)

    policy = spaces.upsert(
        "global",
        "",
        read_mode="none",
        read_path=str(tmp_path),
        write_mode="isolated",
        workspace_path=None,
    )

    assert policy.read_path is None


def _git_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
    (repository / "README.md").write_text("root", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-m", "init"], check=True, capture_output=True)
    return repository


def _worktree_policy(tmp_path: Path, workspace_path: Path):
    _, personas, spaces, teams = _services(tmp_path)
    lead = _persona(personas, "Lead")
    team = teams.create_team("Crew", "", lead.id, [])
    return spaces.upsert(
        "team",
        team.id,
        read_mode="home",
        read_path=None,
        write_mode="worktree",
        workspace_path=str(workspace_path),
    )


def _branches(repository: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repository), "branch", "--format=%(refname:short)"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.split()


def test_cleanup_succeeds_when_worktree_and_branch_are_already_gone(tmp_path: Path) -> None:
    repository = _git_repository(tmp_path)
    policy = _worktree_policy(tmp_path, repository)
    run_root = tmp_path / "runs" / "run-1"
    run_root.mkdir(parents=True)
    prepared = TeamSpaceManager().prepare("run-1", run_root, policy)

    # Someone cleaned the worktree up outside the gateway.
    subprocess.run(
        ["git", "-C", str(repository), "worktree", "remove", "--force", str(prepared.working_root)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "branch", "-D", prepared.worktree_branch],
        check=True,
        capture_output=True,
    )

    TeamSpaceManager().cleanup(run_root, policy, prepared.working_root, prepared.worktree_branch)

    assert not run_root.exists()


def test_cleanup_deletes_a_branch_that_still_exists(tmp_path: Path) -> None:
    repository = _git_repository(tmp_path)
    policy = _worktree_policy(tmp_path, repository)
    run_root = tmp_path / "runs" / "run-1"
    run_root.mkdir(parents=True)
    prepared = TeamSpaceManager().prepare("run-1", run_root, policy)
    assert prepared.worktree_branch in _branches(repository)

    TeamSpaceManager().cleanup(run_root, policy, prepared.working_root, prepared.worktree_branch)

    assert prepared.worktree_branch not in _branches(repository)
    assert not run_root.exists()


def test_cleanup_raises_when_an_existing_branch_cannot_be_deleted(tmp_path: Path) -> None:
    repository = _git_repository(tmp_path)
    policy = _worktree_policy(tmp_path, repository)
    run_root = tmp_path / "runs" / "run-1"
    run_root.mkdir(parents=True)
    prepared = TeamSpaceManager().prepare("run-1", run_root, policy)

    # The branch is still checked out in the live worktree, so git refuses to
    # delete it. Point cleanup at an absent working root so it skips worktree
    # removal and reaches the branch deletion.
    with pytest.raises(ValueError, match="Git worktree command failed"):
        TeamSpaceManager().cleanup(
            run_root,
            policy,
            run_root / "absent",
            prepared.worktree_branch,
        )

    assert prepared.worktree_branch in _branches(repository)
    assert run_root.exists()


def test_cleanup_skips_git_when_the_workspace_path_is_not_a_repository(tmp_path: Path) -> None:
    plain_directory = tmp_path / "not-a-repository"
    plain_directory.mkdir()
    policy = SpacePolicy(
        scope="team",
        scope_id="legacy-team",
        read_mode="none",
        read_path=None,
        write_mode="worktree",
        workspace_path=str(plain_directory),
        created_at="",
        updated_at="",
    )
    run_root = tmp_path / "runs" / "run-1"
    (run_root / "artifacts").mkdir(parents=True)

    TeamSpaceManager().cleanup(run_root, policy, run_root / "project", "team-run/run-1")

    assert not run_root.exists()


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
