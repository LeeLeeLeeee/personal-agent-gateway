import hashlib
import json
from pathlib import Path

import pytest

from personal_agent_gateway.artifacts import ArtifactStore
from personal_agent_gateway.db import Database
from personal_agent_gateway.model_client import ModelResponse
from personal_agent_gateway.personas import PersonaService
from personal_agent_gateway.team_artifact_publisher import TeamArtifactPublisher
from personal_agent_gateway.team_cycles import TeamCycleService
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


def test_result_package_registers_run_outputs(
    tmp_path: Path,
) -> None:
    db, teams, run = _completed_run(tmp_path)
    artifacts = ArtifactStore(db, tmp_path / "global-artifacts")
    task = teams.list_tasks(run.id)[0]
    deliverable = Path(run.working_root or run.workspace_root) / "src" / "api.py"
    digest = hashlib.sha256(deliverable.read_bytes()).hexdigest()
    artifacts.register_existing_file(
        artifact_type="text",
        title="api.py",
        source_path=deliverable,
        relative_path=(
            f"team-runs/{run.id}/run/deliverables/{task.id}/api.py"
        ),
        mime_type="text/x-python",
        metadata={
            "source_path": "src/api.py",
            "sha256": digest,
            "task_id": task.id,
            "cycle_id": None,
            "team_run_id": run.id,
        },
    )
    teams.record_task_outcome(
        task.id,
        {
            "status": "completed",
            "summary": "API 구현 완료",
            "reason_code": None,
            "deliverables": [{"path": "src/api.py", "kind": "text"}],
            "verifications": [
                {
                    "name": "tests",
                    "status": "passed",
                    "evidence": "pytest passed",
                }
            ],
        },
        {
            "accepted": True,
            "status": "completed",
            "reason_code": None,
            "evidence": {"deliverables": ["src/api.py"]},
        },
    )
    packager = TeamRunResultPackager(teams, artifacts)

    created = packager.build(run)

    names = {artifact.file_path.name for artifact in created}
    expected = {"run-result.json", "file-manifest.json", "verification.md"}
    assert names == expected
    assert {path.name for path in Path(run.artifact_root).iterdir()} == expected

    result = json.loads((Path(run.artifact_root) / "run-result.json").read_text())
    assert result["protocol_version"] == 1
    assert result["team_run_id"] == run.id
    assert result["objective"] == run.goal
    assert result["execution_metadata"] is None
    assert result["tasks"][0]["result"] == "API 구현 완료"
    assert result["tasks"][0]["files_created"] == ["src/api.py"]
    assert result["tasks"][0]["outcome"]["status"] == "completed"
    assert result["tasks"][0]["acceptance_result"]["accepted"] is True
    assert result["deliverables"][0]["sha256"] == digest

    manifest = json.loads((Path(run.artifact_root) / "file-manifest.json").read_text())
    assert [item["path"] for item in manifest["files"]] == ["src/api.py"]
    assert not (Path(run.artifact_root) / "workspace.zip").exists()
    assert "node_modules/ignored.js" not in json.dumps(manifest)

    rebuilt = packager.build(run)
    registered = [
        artifact
        for artifact in artifacts.list()
        if artifact.metadata.get("team_run_id") == run.id
        and artifact.metadata.get("package_kind")
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
                "required": True,
                "acceptance": {
                    "required_outputs": ["src/api.py"],
                    "required_verifications": ["tests"],
                },
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
            return ModelResponse(
                content=json.dumps(
                    {
                        "status": "completed",
                        "summary": "API 구현 완료",
                        "reason_code": None,
                        "deliverables": [{"path": "src/api.py", "kind": "text"}],
                        "verifications": [
                            {
                                "name": "tests",
                                "status": "passed",
                                "evidence": "pytest passed",
                            }
                        ],
                    }
                ),
                tool_calls=[],
            )

    runtime = TeamRuntime(
        teams,
        lambda agent: LeaderModel() if agent.role == "leader" else WorkerModel(),
        artifact_publisher=TeamArtifactPublisher(artifacts),
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
        and artifact.metadata.get("package_kind")
    } == {
        "run-result.json",
        "file-manifest.json",
        "verification.md",
    }
    package_artifacts = [
        artifact for artifact in artifacts.list()
        if artifact.metadata.get("package_kind")
    ]
    assert package_artifacts
    assert {artifact.retention_class for artifact in package_artifacts} == {"temporary"}
    assert all(artifact.expires_at is not None for artifact in package_artifacts)
    assert not (Path(completed.artifact_root) / "workspace.zip").exists()
    result_payload = json.loads(
        (Path(completed.artifact_root) / "run-result.json").read_text(encoding="utf-8")
    )
    task_payload = result_payload["tasks"][0]
    assert task_payload["acceptance"]["required_verifications"] == [
        {"name": "tests", "check": None}
    ]


def test_cycle_result_uses_objective_and_stored_execution_metadata(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "runs")
    leader = personas.create_persona("Lead", "lead", "Plans", [], [])
    run = teams.create_team_run(
        "Base goal",
        leader.id,
        [],
        "planning_only",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    cycle_service = TeamCycleService(db)
    request = cycle_service.enqueue_request(
        run.id,
        "hook",
        "hook-1",
        "Current objective",
        previous_cycle_id=None,
    )
    assert cycle_service.claim_next(run.id) is not None
    cycle = teams.create_cycle(
        run.id,
        "hook",
        "hook-1",
        request_id=request.id,
    )
    metadata = {
        "agents": {
            "worker-1": {
                "provider": "codex",
                "sandbox": "workspace-write",
                "input_manifest_sha256": "abc123",
            }
        }
    }
    teams.set_cycle_execution_metadata(cycle.id, metadata)
    teams.set_cycle_status(cycle.id, "completed", summary="done")
    run = teams.set_run_status(run.id, "completed", summary="done")
    artifacts = ArtifactStore(db, tmp_path / "global-artifacts")

    TeamRunResultPackager(teams, artifacts).build(run, cycle.id)

    result = json.loads((Path(run.artifact_root) / "run-result.json").read_text())
    assert result["objective"] == "Current objective"
    assert result["execution_metadata"] == metadata
