from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_agent_gateway.app import _team_model_factory, create_app
from personal_agent_gateway.config import AppConfig
from personal_agent_gateway.execution_contract import ExecutionContractError
from personal_agent_gateway.lmg_client import ProviderExecutionCapabilities
from personal_agent_gateway.remote_model_client import HttpModelClient
from personal_agent_gateway.teams import TeamAgent


def _config(tmp_path):
    return AppConfig(
        workspace_root=tmp_path,
        session_dir=tmp_path / "sessions",
        lmg_local_token="local-secret",
    )


def _agent(
    backend: str,
    session: str | None = None,
    options: dict[str, object] | None = None,
    workspace_path: str | None = None,
    current_task_id: str | None = None,
) -> TeamAgent:
    return TeamAgent(
        id="a1", team_run_id="r1", name="A", role="member", persona_id="p1",
        persona_snapshot={"default_options": options or {}}, backend=backend, model="default", status="pending",
        workspace_path=workspace_path, current_task_id=current_task_id, reinvocations=0,
        upstream_session_id=session, created_at="t", updated_at="t",
    )


class _TeamRuns:
    def __init__(
        self,
        *,
        artifact_root: str,
        space_policy: dict[str, object] | None,
        cycle_space_policy: dict[str, object] | None = None,
    ):
        self._run = SimpleNamespace(
            artifact_root=artifact_root,
            space_policy=space_policy,
        )
        self.execution_metadata = {
            "provider_capabilities": {
                "codex": _frozen_capability(
                    network_modes=["unspecified", "denied", "required"],
                    sandbox_modes=[
                        "read-only",
                        "workspace-write",
                        "danger-full-access",
                    ],
                ),
                "claude": _frozen_capability(
                    network_modes=["unspecified"],
                    permission_modes=["default", "acceptEdits", "plan"],
                ),
            }
        }
        self.cycle_space_policy = cycle_space_policy

    def get_team_run(self, team_run_id: str):
        assert team_run_id == "r1"
        return self._run

    def get_task(self, task_id: str):
        assert task_id == "task-1"
        return SimpleNamespace(cycle_id="cycle-1")

    def get_cycle(self, cycle_id: str):
        assert cycle_id == "cycle-1"
        return SimpleNamespace(
            team_run_id="r1",
            execution_metadata=self.execution_metadata,
            space_policy=self.cycle_space_policy,
        )

    def set_cycle_execution_metadata(self, cycle_id: str, metadata):
        assert cycle_id == "cycle-1"
        self.execution_metadata = metadata

    def set_cycle_agent_execution_metadata(
        self,
        cycle_id: str,
        agent_id: str,
        metadata: dict[str, object],
    ):
        assert cycle_id == "cycle-1"
        agents = dict(self.execution_metadata.get("agents", {}))
        agents[agent_id] = metadata
        self.execution_metadata = {
            **self.execution_metadata,
            "agents": agents,
        }


def _frozen_capability(
    *,
    network_modes: list[str],
    sandbox_modes: list[str] | None = None,
    permission_modes: list[str] | None = None,
) -> dict[str, object]:
    return {
        "ready": True,
        "readiness_error": None,
        "snapshot_status": "fresh",
        "detected_at": "2026-07-30T00:00:00Z",
        "execution": {
            "resume": True,
            "external_read_only_roots": False,
            "network_modes": network_modes,
            "sandbox_modes": sandbox_modes or [],
            "permission_modes": permission_modes or [],
        },
    }


def _space_policy(
    read_path: str | None,
    read_mode: str = "selected",
    *,
    write_mode: str = "isolated",
    workspace_path: str | None = None,
) -> dict[str, object]:
    return {
        "scope": "team",
        "scope_id": "team-1",
        "read_mode": read_mode,
        "read_path": read_path,
        "write_mode": write_mode,
        "workspace_path": workspace_path,
        "created_at": "t",
        "updated_at": "t",
    }


class _AgentRegistry:
    def get(self, provider: str):
        capabilities = (
            ProviderExecutionCapabilities(
                resume=True,
                external_read_only_roots=False,
                network_modes=("unspecified", "denied", "required"),
                sandbox_modes=("read-only", "workspace-write", "danger-full-access"),
                permission_modes=(),
            )
            if provider == "codex"
            else ProviderExecutionCapabilities(
                resume=True,
                external_read_only_roots=False,
                network_modes=("unspecified",),
                sandbox_modes=(),
                permission_modes=("default", "acceptEdits", "plan"),
            )
        )
        return SimpleNamespace(available=True, execution_capabilities=capabilities)


def _factory(config, team_runs=None):
    return _team_model_factory(
        config,
        team_runs,
        agent_registry=_AgentRegistry(),
    )


def test_factory_picks_codex_by_default(tmp_path):
    factory = _factory(_config(tmp_path))
    client = factory(_agent("codex"))
    assert isinstance(client, HttpModelClient)
    assert client._provider == "codex"
    assert client._local_token == "local-secret"
    assert client._consumer == "personal-agent-gateway"
    assert client._consumer_session_id == "r1"


def test_app_wires_one_shared_team_operation_graph(tmp_path):
    app = create_app(_config(tmp_path))

    operations = app.state.team_model_operation_service
    effects = app.state.team_model_effect_service
    invoker = app.state.team_model_invoker
    recovery = app.state.team_provider_recovery

    assert app.state.team_runtime._operations is operations
    assert app.state.team_runtime._model_effects is effects
    assert app.state.team_runtime._model_invoker is invoker
    assert app.state.team_runtime._provider_recovery is recovery
    assert app.state.team_cycle_dispatcher._provider_recovery is recovery
    assert effects._operations is operations
    assert invoker._operations is operations
    assert recovery._operations is operations


def test_factory_picks_claude_when_backend_claude(tmp_path):
    factory = _factory(_config(tmp_path))
    client = factory(_agent("claude"))
    assert isinstance(client, HttpModelClient)
    assert client._provider == "claude"
    assert client._local_token == "local-secret"
    assert client._consumer == "personal-agent-gateway"
    assert client._consumer_session_id == "r1"


def test_factory_applies_codex_persona_options(tmp_path):
    client = _factory(_config(tmp_path))(
        _agent(
            "codex",
            options={
                "effort": "max",
                "sandbox": "read-only",
                "approval_policy": "on-request",
                "profile": "review",
            },
        )
    )

    assert client._execution["effort"] == "max"
    assert client._execution["sandbox"] == "workspace-write"
    assert client._execution["approval_policy"] == "never"
    assert client._execution["profile"] == "review"


def test_factory_applies_claude_persona_options(tmp_path):
    client = _factory(_config(tmp_path))(
        _agent(
            "claude",
            options={"effort": "xhigh", "permission_mode": "plan", "agent": "reviewer"},
        )
    )

    assert client._execution["effort"] == "xhigh"
    assert client._execution["permission_mode"] == "acceptEdits"
    assert client._execution["agent"] == "reviewer"


@pytest.mark.parametrize("backend", ["codex", "claude"])
def test_factory_does_not_send_sibling_artifact_root_as_cli_read_root(
    tmp_path,
    backend,
):
    run_root = tmp_path / "r1"
    workspace = run_root / "workspace"
    artifact_root = run_root / "artifacts"
    workspace.mkdir(parents=True)
    artifact_root.mkdir()
    factory = _factory(
        _config(tmp_path),
        _TeamRuns(
            artifact_root=str(artifact_root),
            space_policy=_space_policy(None, read_mode="none"),
        ),
    )

    client = factory(_agent(backend, workspace_path=str(workspace)))

    assert client._execution["workspace_root"] == str(workspace)
    assert client._execution["read_roots"] == []


@pytest.mark.parametrize("backend", ["codex", "claude"])
def test_factory_stages_selected_source_inside_workspace(tmp_path, backend):
    workspace = tmp_path / "r1" / "workspace"
    artifact_root = tmp_path / "r1" / "artifacts"
    external_read_root = tmp_path / "shared"
    workspace.mkdir(parents=True)
    artifact_root.mkdir()
    external_read_root.mkdir()
    (external_read_root / "evidence.txt").write_text("evidence", encoding="utf-8")
    factory = _factory(
        _config(tmp_path),
        _TeamRuns(
            artifact_root=str(artifact_root),
            space_policy=_space_policy(str(external_read_root)),
        ),
    )

    client = factory(_agent(backend, workspace_path=str(workspace)))

    inputs = Path(client._execution["read_roots"][0])
    assert inputs == workspace / "_inputs"
    assert (inputs / "01-shared" / "evidence.txt").is_file()
    assert str(artifact_root) not in client._execution["read_roots"]


def test_factory_uses_task_cycle_space_before_run_space(tmp_path):
    workspace = tmp_path / "r1" / "workspace"
    source = tmp_path / "shared"
    workspace.mkdir(parents=True)
    source.mkdir()
    (source / "evidence.txt").write_text("evidence", encoding="utf-8")
    team_runs = _TeamRuns(
        artifact_root=str(tmp_path / "r1" / "artifacts"),
        space_policy=_space_policy(None, read_mode="none"),
        cycle_space_policy=_space_policy(str(source), read_mode="selected"),
    )

    client = _factory(_config(tmp_path), team_runs)(
        _agent("codex", workspace_path=str(workspace), current_task_id="task-1")
    )

    inputs = Path(client._execution["read_roots"][0])
    assert client._execution["workspace_root"] == str(workspace)
    assert inputs == workspace / "_inputs"
    assert (inputs / "01-shared" / "evidence.txt").is_file()


def test_factory_uses_explicit_cycle_space_for_leader_without_task(tmp_path):
    workspace = tmp_path / "r1" / "workspace"
    source = tmp_path / "shared"
    workspace.mkdir(parents=True)
    source.mkdir()
    (source / "evidence.txt").write_text("evidence", encoding="utf-8")
    team_runs = _TeamRuns(
        artifact_root=str(tmp_path / "r1" / "artifacts"),
        space_policy=_space_policy(None, read_mode="none"),
        cycle_space_policy=_space_policy(str(source), read_mode="selected"),
    )

    client = _factory(_config(tmp_path), team_runs)(
        _agent("codex", workspace_path=str(workspace)),
        "cycle-1",
    )

    inputs = Path(client._execution["read_roots"][0])
    assert inputs == workspace / "_inputs"
    assert (inputs / "01-shared" / "evidence.txt").is_file()


def test_factory_rejects_cycle_write_mode_change_before_compilation(tmp_path):
    workspace = tmp_path / "project"
    source = tmp_path / "shared"
    workspace.mkdir()
    source.mkdir()
    team_runs = _TeamRuns(
        artifact_root=str(tmp_path / "artifacts"),
        space_policy=_space_policy(
            str(workspace),
            read_mode="all",
            write_mode="full_access",
            workspace_path=str(workspace),
        ),
        cycle_space_policy=_space_policy(
            str(source),
            read_mode="selected",
            write_mode="isolated",
        ),
    )

    with pytest.raises(ExecutionContractError) as error:
        _factory(_config(tmp_path), team_runs)(
            _agent(
                "codex",
                workspace_path=str(workspace),
                current_task_id="task-1",
            )
        )

    assert error.value.code == "cycle_space_write_mode_changed"
    assert "start a new Team Run" in str(error.value)
    assert not (workspace / "_inputs").exists()


def test_factory_rejects_run_without_frozen_space_snapshot(tmp_path):
    workspace = tmp_path / "r1" / "workspace"
    workspace.mkdir(parents=True)
    team_runs = _TeamRuns(
        artifact_root=str(tmp_path / "r1" / "artifacts"),
        space_policy=None,
        cycle_space_policy=None,
    )

    with pytest.raises(
        RuntimeError,
        match="^Team run has no frozen SPACE policy$",
    ):
        _factory(_config(tmp_path), team_runs)(
            _agent(
                "codex",
                workspace_path=str(workspace),
                current_task_id="task-1",
            )
        )


def test_factory_rejects_missing_run_space_even_with_cycle_space(tmp_path):
    workspace = tmp_path / "r1" / "workspace"
    workspace.mkdir(parents=True)
    team_runs = _TeamRuns(
        artifact_root=str(tmp_path / "r1" / "artifacts"),
        space_policy=None,
        cycle_space_policy=_space_policy(None, read_mode="none"),
    )

    with pytest.raises(
        RuntimeError,
        match="^Team run has no frozen SPACE policy$",
    ):
        _factory(_config(tmp_path), team_runs)(
            _agent("codex", workspace_path=str(workspace)),
            "cycle-1",
        )


def test_factory_persists_compiled_cycle_execution_metadata(tmp_path):
    workspace = tmp_path / "r1" / "workspace"
    artifact_root = tmp_path / "r1" / "artifacts"
    source_root = tmp_path / "shared"
    workspace.mkdir(parents=True)
    artifact_root.mkdir()
    source_root.mkdir()
    (source_root / "evidence.txt").write_text("evidence", encoding="utf-8")
    team_runs = _TeamRuns(
        artifact_root=str(artifact_root),
        space_policy=_space_policy(str(source_root)),
    )

    _factory(_config(tmp_path), team_runs)(
        _agent(
            "codex",
            workspace_path=str(workspace),
            current_task_id="task-1",
        )
    )

    metadata = team_runs.execution_metadata
    assert metadata is not None
    agent_metadata = metadata["agents"]["a1"]
    assert agent_metadata["provider"] == "codex"
    assert agent_metadata["model"] == "default"
    assert agent_metadata["sandbox"] == "workspace-write"
    assert agent_metadata["input_manifest_sha256"]


def test_team_factory_uses_frozen_cycle_capability_without_registry_lookup(tmp_path):
    team_runs = _TeamRuns(
        artifact_root=str(tmp_path / "artifacts"),
        space_policy=_space_policy(None, read_mode="none"),
    )
    team_runs.execution_metadata = {
        "provider_capabilities": {
            "codex": {
                "snapshot_status": "fresh",
                "detected_at": "2026-07-30T00:00:00Z",
                "execution": {
                    "resume": True,
                    "external_read_only_roots": False,
                    "network_modes": ["unspecified"],
                    "sandbox_modes": ["workspace-write"],
                    "permission_modes": [],
                },
            }
        }
    }
    registry = SimpleNamespace(
        get=lambda _provider: pytest.fail("registry refreshed on cycle hot path")
    )

    client = _team_model_factory(
        _config(tmp_path),
        team_runs,
        agent_registry=registry,
    )(_agent("codex", workspace_path=str(tmp_path / "workspace")), "cycle-1")

    assert isinstance(client, HttpModelClient)


@pytest.mark.parametrize("backend", ["codex", "claude"])
def test_factory_requires_bounded_selection_for_home_isolated(tmp_path, backend):
    workspace = tmp_path / "r1" / "workspace"
    artifact_root = tmp_path / "r1" / "artifacts"
    home_read_root = tmp_path / "home"
    workspace.mkdir(parents=True)
    artifact_root.mkdir()
    home_read_root.mkdir()
    factory = _factory(
        _config(tmp_path),
        _TeamRuns(
            artifact_root=str(artifact_root),
            space_policy=_space_policy(str(home_read_root), read_mode="home"),
        ),
    )

    with pytest.raises(ExecutionContractError) as error:
        factory(_agent(backend, workspace_path=str(workspace)))

    assert error.value.code == "source_scope_requires_selection"


@pytest.mark.parametrize("write_mode", ["worktree", "full_access"])
def test_factory_uses_direct_workspace_without_staging(tmp_path, write_mode):
    workspace = tmp_path / "project"
    workspace.mkdir()
    team_runs = _TeamRuns(
        artifact_root=str(tmp_path / "artifacts"),
        space_policy=_space_policy(
            str(workspace),
            read_mode="home",
            write_mode=write_mode,
            workspace_path=str(workspace),
        ),
    )

    client = _factory(_config(tmp_path), team_runs)(
        _agent("codex", workspace_path=str(workspace))
    )

    assert client._execution["workspace_root"] == str(workspace)
    assert client._execution["read_roots"] == [str(workspace)]
    assert not (workspace / "_inputs").exists()


def test_factory_accepts_codex_required_network(tmp_path):
    client = _factory(_config(tmp_path))(
        _agent("codex", options={"network": "required"})
    )

    assert client._execution["network"] == "required"


def test_factory_rejects_claude_required_network_before_model_call(tmp_path):
    with pytest.raises(ExecutionContractError) as error:
        _factory(_config(tmp_path))(
            _agent("claude", options={"network": "required"})
        )

    assert error.value.code == "unsupported_execution_capability"
