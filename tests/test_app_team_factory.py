from personal_agent_gateway.app import _team_model_factory
from personal_agent_gateway.config import AppConfig
from personal_agent_gateway.model_client import ClaudeModelClient, CodexModelClient
from personal_agent_gateway.teams import TeamAgent


def _config(tmp_path):
    return AppConfig(workspace_root=tmp_path, session_dir=tmp_path / "sessions")


def _agent(
    backend: str,
    session: str | None = None,
    options: dict[str, object] | None = None,
) -> TeamAgent:
    return TeamAgent(
        id="a1", team_run_id="r1", name="A", role="member", persona_id="p1",
        persona_snapshot={"default_options": options or {}}, backend=backend, model="default", status="pending",
        workspace_path=None, current_task_id=None, reinvocations=0,
        upstream_session_id=session, created_at="t", updated_at="t",
    )


def test_factory_picks_codex_by_default(tmp_path):
    factory = _team_model_factory(_config(tmp_path))
    assert isinstance(factory(_agent("codex")), CodexModelClient)


def test_factory_picks_claude_when_backend_claude(tmp_path):
    factory = _team_model_factory(_config(tmp_path))
    assert isinstance(factory(_agent("claude")), ClaudeModelClient)


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

    assert client._effort == "max"
    assert client._sandbox == "read-only"
    assert client._approval_policy == "on-request"
    assert client._profile == "review"


def test_factory_applies_claude_persona_options(tmp_path):
    client = _team_model_factory(_config(tmp_path))(
        _agent(
            "claude",
            options={"effort": "xhigh", "permission_mode": "plan", "agent": "reviewer"},
        )
    )

    assert client._effort == "xhigh"
    assert client._permission_mode == "plan"
    assert client._agent == "reviewer"
