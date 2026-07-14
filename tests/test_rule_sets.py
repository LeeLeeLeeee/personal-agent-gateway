import pytest

from personal_agent_gateway.db import Database
from personal_agent_gateway.rule_sets import RuleSetService


def make_service(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    return RuleSetService(db)


def test_seed_creates_global_and_persona_baseline(tmp_path):
    service = make_service(tmp_path)
    service.seed_defaults()
    g = service.get_global()
    pb = service.get_persona_baseline()
    assert g.scope == "global"
    assert g.personality
    assert any(r["level"] == "REQUIRED" for r in g.rules)
    assert pb.scope == "persona_baseline"
    assert pb.rules


def test_seed_is_idempotent(tmp_path):
    service = make_service(tmp_path)
    service.seed_defaults()
    service.upsert("global", None, "custom", [{"level": "GUIDELINE", "text": "keep custom"}])
    service.seed_defaults()
    assert service.get_global().personality == "custom"


def test_upsert_validates_level(tmp_path):
    service = make_service(tmp_path)
    with pytest.raises(ValueError):
        service.upsert("global", None, "x", [{"level": "MAYBE", "text": "bad"}])


def test_get_team_creates_empty_when_missing(tmp_path):
    service = make_service(tmp_path)
    rs = service.get_team("team-1")
    assert rs.scope == "team"
    assert rs.team_id == "team-1"
    assert rs.rules == []


def test_snapshot_composes_layers(tmp_path):
    service = make_service(tmp_path)
    service.seed_defaults()
    service.upsert("team", "team-1", "team voice",
                   [{"level": "REQUIRED", "text": "team rule"}])
    snap = service.snapshot_for_team("team-1")
    assert snap["global"]["personality"]
    assert snap["team"]["personality"] == "team voice"
    assert snap["persona_baseline"]["rules"]
    assert snap["team"]["rules"][0]["text"] == "team rule"


def test_delete_team_removes_rule_set(tmp_path):
    service = make_service(tmp_path)
    service.upsert("team", "team-1", "v", [])
    service.delete_team("team-1")
    assert service.get_team("team-1").rules == []  # recreated empty, not the deleted one
