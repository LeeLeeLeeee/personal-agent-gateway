from types import SimpleNamespace

import pytest

from personal_agent_gateway.app import _team_model_factory
from personal_agent_gateway.config import AppConfig
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
) -> TeamAgent:
    return TeamAgent(
        id="a1", team_run_id="r1", name="A", role="member", persona_id="p1",
        persona_snapshot={"default_options": options or {}}, backend=backend, model="default", status="pending",
        workspace_path=workspace_path, current_task_id=None, reinvocations=0,
        upstream_session_id=session, created_at="t", updated_at="t",
    )


class _TeamRuns:
    def __init__(self, *, artifact_root: str, space_policy: dict[str, object]):
        self._run = SimpleNamespace(
            artifact_root=artifact_root,
            space_policy=space_policy,
        )

    def get_team_run(self, team_run_id: str):
        assert team_run_id == "r1"
        return self._run


def _space_policy(
    read_path: str | None,
    read_mode: str = "selected",
) -> dict[str, object]:
    return {
        "scope": "team",
        "scope_id": "team-1",
        "read_mode": read_mode,
        "read_path": read_path,
        "write_mode": "isolated",
        "workspace_path": None,
        "created_at": "t",
        "updated_at": "t",
    }


def test_factory_picks_codex_by_default(tmp_path):
    factory = _team_model_factory(_config(tmp_path))
    client = factory(_agent("codex"))
    assert isinstance(client, HttpModelClient)
    assert client._provider == "codex"
    assert client._local_token == "local-secret"
    assert client._consumer == "personal-agent-gateway"
    assert client._consumer_session_id == "r1"


def test_factory_picks_claude_when_backend_claude(tmp_path):
    factory = _team_model_factory(_config(tmp_path))
    client = factory(_agent("claude"))
    assert isinstance(client, HttpModelClient)
    assert client._provider == "claude"
    assert client._local_token == "local-secret"
    assert client._consumer == "personal-agent-gateway"
    assert client._consumer_session_id == "r1"


def test_factory_applies_codex_persona_options(tmp_path):
    client = _team_model_factory(_config(tmp_path))(
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
    assert client._execution["sandbox"] == "read-only"
    assert client._execution["approval_policy"] == "on-request"
    assert client._execution["profile"] == "review"


def test_factory_applies_claude_persona_options(tmp_path):
    client = _team_model_factory(_config(tmp_path))(
        _agent(
            "claude",
            options={"effort": "xhigh", "permission_mode": "plan", "agent": "reviewer"},
        )
    )

    assert client._execution["effort"] == "xhigh"
    assert client._execution["permission_mode"] == "plan"
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
    factory = _team_model_factory(
        _config(tmp_path),
        _TeamRuns(
            artifact_root=str(artifact_root),
            space_policy=_space_policy(None),
        ),
    )

    client = factory(_agent(backend, workspace_path=str(workspace)))

    assert client._execution["workspace_root"] == str(workspace)
    assert client._execution["read_roots"] == []


@pytest.mark.parametrize("backend", ["codex", "claude"])
def test_factory_rejects_cli_read_path_outside_workspace(tmp_path, backend):
    workspace = tmp_path / "r1" / "workspace"
    artifact_root = tmp_path / "r1" / "artifacts"
    external_read_root = tmp_path / "shared"
    workspace.mkdir(parents=True)
    artifact_root.mkdir()
    external_read_root.mkdir()
    factory = _team_model_factory(
        _config(tmp_path),
        _TeamRuns(
            artifact_root=str(artifact_root),
            space_policy=_space_policy(str(external_read_root)),
        ),
    )

    with pytest.raises(ValueError, match="inside the team workspace"):
        factory(_agent(backend, workspace_path=str(workspace)))


@pytest.mark.parametrize("backend", ["codex", "claude"])
def test_factory_omits_default_home_read_path_outside_workspace(tmp_path, backend):
    workspace = tmp_path / "r1" / "workspace"
    artifact_root = tmp_path / "r1" / "artifacts"
    home_read_root = tmp_path / "home"
    workspace.mkdir(parents=True)
    artifact_root.mkdir()
    home_read_root.mkdir()
    factory = _team_model_factory(
        _config(tmp_path),
        _TeamRuns(
            artifact_root=str(artifact_root),
            space_policy=_space_policy(str(home_read_root), read_mode="home"),
        ),
    )

    client = factory(_agent(backend, workspace_path=str(workspace)))

    assert client._execution["read_roots"] == []
