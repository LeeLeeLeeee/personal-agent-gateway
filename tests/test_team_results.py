import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from personal_agent_gateway.artifacts import ArtifactStore
from personal_agent_gateway.db import Database
from personal_agent_gateway.model_client import ModelResponse
from personal_agent_gateway.personas import PersonaService
from personal_agent_gateway.team_results import (
    TeamRunResultPackager,
    workspace_changes,
    workspace_snapshot,
)
from personal_agent_gateway.team_runtime import TeamRuntime
from personal_agent_gateway.teams import TeamRunService


def _completed_run(tmp_path: Path, write_mode: str = "isolated"):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "runs")
    leader = personas.create_persona("Lead", "lead", "Plans", [], [])
    worker_persona = personas.create_persona("Worker", "member", "Builds", [], [])
    run = teams.create_team_run(
        "Build the service",
        leader.id,
        [worker_persona.id],
        "plan_and_execute",
        1,
    )
    if write_mode != "isolated":
        policy = dict(run.space_policy or {})
        policy["write_mode"] = write_mode
        db.execute(
            "update team_runs set space_policy_snapshot_json = ? where id = ?",
            (json.dumps(policy), run.id),
        )
        run = teams.get_team_run(run.id)
    worker = next(agent for agent in teams.list_agents(run.id) if agent.role == "member")
    task = teams.create_task(run.id, "Implement API", "Create the endpoint", worker.id)
    task, worker = teams.start_task(task.id, worker.id)
    working_root = Path(run.working_root or run.workspace_root)
    (working_root / "src").mkdir(parents=True, exist_ok=True)
    (working_root / "src" / "api.py").write_text("print('ok')\n", encoding="utf-8")
    (working_root / "node_modules").mkdir()
    (working_root / "node_modules" / "ignored.js").write_text("ignored", encoding="utf-8")
    teams.append_message(
        run.id,
        worker.id,
        None,
        "agent_output",
        "API 구현과 검증을 완료했습니다.",
        {
            "task_id": task.id,
            "files_created": ["src/api.py"],
            "files_modified": [],
            "files_deleted": [],
        },
    )
    teams.finish_task(task.id, worker.id, "completed", result="API 구현 완료")
    run = teams.set_run_status(run.id, "completed", summary="서비스 구현 완료")
    return db, teams, run


@pytest.mark.parametrize(
    ("write_mode", "expects_archive"),
    [("isolated", True), ("worktree", False), ("full_access", False)],
)
def test_result_package_registers_run_outputs(
    tmp_path: Path, write_mode: str, expects_archive: bool
) -> None:
    db, teams, run = _completed_run(tmp_path, write_mode)
    artifacts = ArtifactStore(db, tmp_path / "global-artifacts")
    packager = TeamRunResultPackager(teams, artifacts)

    created = packager.build(run)

    names = {artifact.file_path.name for artifact in created}
    expected = {"run-result.json", "file-manifest.json", "verification.md"}
    if expects_archive:
        expected.add("workspace.zip")
    assert names == expected
    assert {path.name for path in Path(run.artifact_root).iterdir()} == expected

    result = json.loads((Path(run.artifact_root) / "run-result.json").read_text())
    assert result["team_run_id"] == run.id
    assert result["tasks"][0]["result"] == "API 구현 완료"
    assert result["tasks"][0]["files_created"] == ["src/api.py"]

    manifest = json.loads((Path(run.artifact_root) / "file-manifest.json").read_text())
    assert [item["path"] for item in manifest["files"]] == ["src/api.py"]

    if expects_archive:
        with ZipFile(Path(run.artifact_root) / "workspace.zip") as archive:
            assert archive.namelist() == ["src/api.py"]

    rebuilt = packager.build(run)
    registered = [
        artifact for artifact in artifacts.list() if artifact.metadata.get("team_run_id") == run.id
    ]
    assert len(rebuilt) == len(expected)
    assert len(registered) == len(expected)


def test_workspace_changes_track_task_file_ownership(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "modified.txt").write_text("before", encoding="utf-8")
    (root / "deleted.txt").write_text("delete me", encoding="utf-8")
    before = workspace_snapshot(root)

    (root / "modified.txt").write_text("after with more bytes", encoding="utf-8")
    (root / "deleted.txt").unlink()
    (root / "created.txt").write_text("new", encoding="utf-8")
    changes = workspace_changes(before, workspace_snapshot(root))

    assert changes == {
        "files_created": ["created.txt"],
        "files_modified": ["modified.txt"],
        "files_deleted": ["deleted.txt"],
    }


@pytest.mark.asyncio
async def test_runtime_attaches_file_changes_and_builds_result_package(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "runs")
    artifacts = ArtifactStore(db, tmp_path / "global-artifacts")
    leader_persona = personas.create_persona("Lead", "lead", "Plans", [], [])
    worker_persona = personas.create_persona("Worker", "member", "Builds", [], [])
    run = teams.create_team_run(
        "Build the service",
        leader_persona.id,
        [worker_persona.id],
        "plan_and_execute",
        1,
    )
    worker = next(agent for agent in teams.list_agents(run.id) if agent.role == "member")
    plan = json.dumps(
        [
            {
                "title": "Implement API",
                "description": "Create the endpoint",
                "owner_agent_id": worker.id,
            }
        ]
    )
    leader_responses = iter([plan, "서비스 구현 완료"])
    working_root = Path(run.working_root or run.workspace_root)

    class LeaderModel:
        async def complete(self, _messages):
            return ModelResponse(content=next(leader_responses), tool_calls=[])

    class WorkerModel:
        async def complete(self, _messages):
            source = working_root / "src" / "api.py"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("print('ok')\n", encoding="utf-8")
            return ModelResponse(content="API 구현 완료", tool_calls=[])

    runtime = TeamRuntime(
        teams,
        lambda agent: LeaderModel() if agent.role == "leader" else WorkerModel(),
        result_packager=TeamRunResultPackager(teams, artifacts),
    )

    completed = await runtime.start(run.id)

    assert completed.status == "completed"
    report = next(
        message for message in teams.list_messages(run.id) if message.kind == "agent_output"
    )
    assert report.metadata["files_created"] == ["src/api.py"]
    assert (Path(completed.artifact_root) / "run-result.json").is_file()
    assert {
        artifact.metadata.get("package_kind")
        for artifact in artifacts.list()
        if artifact.metadata.get("team_run_id") == run.id
    } == {
        "run-result.json",
        "file-manifest.json",
        "verification.md",
        "workspace.zip",
    }
