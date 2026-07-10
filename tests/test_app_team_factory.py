from personal_agent_gateway.app import _team_model_factory
from personal_agent_gateway.config import AppConfig
from personal_agent_gateway.model_client import ClaudeModelClient, CodexModelClient
from personal_agent_gateway.teams import TeamAgent


def _config(tmp_path):
    return AppConfig(workspace_root=tmp_path, session_dir=tmp_path / "sessions")


def _agent(backend: str, session: str | None = None) -> TeamAgent:
    return TeamAgent(
        id="a1", team_run_id="r1", name="A", role="member", persona_id="p1",
        persona_snapshot={}, backend=backend, model="default", status="pending",
        workspace_path=None, current_task_id=None, reinvocations=0,
        upstream_session_id=session, created_at="t", updated_at="t",
    )


def test_factory_picks_codex_by_default(tmp_path):
    factory = _team_model_factory(_config(tmp_path))
    assert isinstance(factory(_agent("codex")), CodexModelClient)


def test_factory_picks_claude_when_backend_claude(tmp_path):
    factory = _team_model_factory(_config(tmp_path))
    assert isinstance(factory(_agent("claude")), ClaudeModelClient)
