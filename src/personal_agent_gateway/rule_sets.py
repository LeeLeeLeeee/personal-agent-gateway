import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from personal_agent_gateway.db import Database

_LEVELS = {"REQUIRED", "GUIDELINE"}

DEFAULT_GLOBAL_PERSONALITY = (
    "팀은 규율 있는 릴리스 크루처럼 움직인다. 사실을 단정적으로 말하고, 위험을 일찍 드러내며, "
    "추측으로 배포하지 않는다. 결정은 기록하고, 이견은 합의 표류가 아니라 리더가 정리한다."
)
DEFAULT_GLOBAL_RULES = [
    {"level": "REQUIRED", "text": "선언된 워크스페이스 루트와 ./data/artifacts 밖에는 쓰지 않는다."},
    {"level": "REQUIRED", "text": "파일 삭제·덮어쓰기 명령은 실행 전에 승인을 받는다."},
    {"level": "REQUIRED", "text": "검증 근거가 붙기 전에는 실행을 완료로 표시하지 않는다."},
    {"level": "REQUIRED", "text": "페르소나 스냅샷은 실행 시작 시 동결되며 실행 중 교체하지 않는다."},
    {"level": "GUIDELINE", "text": "목표를 만족하는 가장 작은 변경을 선호하고, 투기적 작업을 피한다."},
    {"level": "GUIDELINE", "text": "막히면 조용히 재시도하지 말고 한 턴 안에 블로커를 보고한다."},
]
DEFAULT_PERSONA_PERSONALITY = (
    "모든 에이전트는 정밀하고 간결하며 근거 중심이다. 무엇을 할지 먼저 말하고, 건드린 파일이나 "
    "명령을 인용하며, 추측 대신 불확실성을 인정한다."
)
DEFAULT_PERSONA_RULES = [
    {"level": "REQUIRED", "text": "맡은 역할 범위를 지키고, 다른 페르소나의 일은 넘긴다."},
    {"level": "REQUIRED", "text": "모든 보고에 실제 경로·명령·라인을 인용한다."},
    {"level": "REQUIRED", "text": "입력이 모호하면 추측하지 말고 확인 질문을 한다."},
    {"level": "GUIDELINE", "text": "명령이나 diff를 보일 때가 아니면 메시지는 몇 줄로 유지한다."},
    {"level": "GUIDELINE", "text": "위험은 즉시 리더에게 알리고 심각도를 낮추지 않는다."},
]


@dataclass(frozen=True)
class RuleSet:
    id: str
    scope: str
    team_id: str | None
    personality: str
    rules: list[dict]
    updated_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_rules(rules: list[dict]) -> list[dict]:
    validated: list[dict] = []
    for rule in rules:
        level = rule.get("level")
        text = rule.get("text")
        if level not in _LEVELS:
            raise ValueError(f"Invalid rule level: {level!r}")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Rule text is required")
        validated.append({"level": level, "text": text})
    return validated


class RuleSetService:
    def __init__(self, db: Database) -> None:
        self._db = db

    def seed_defaults(self) -> None:
        if self._db.fetchone("select id from rule_sets where scope = 'global'") is None:
            self.upsert("global", None, DEFAULT_GLOBAL_PERSONALITY, DEFAULT_GLOBAL_RULES)
        if self._db.fetchone("select id from rule_sets where scope = 'persona_baseline'") is None:
            self.upsert("persona_baseline", None, DEFAULT_PERSONA_PERSONALITY, DEFAULT_PERSONA_RULES)

    def get_global(self) -> RuleSet:
        return self._get_or_empty("global", None)

    def get_persona_baseline(self) -> RuleSet:
        return self._get_or_empty("persona_baseline", None)

    def get_team(self, team_id: str) -> RuleSet:
        return self._get_or_empty("team", team_id)

    def list_team_rule_sets(self) -> list[RuleSet]:
        rows = self._db.fetchall("select * from rule_sets where scope = 'team' order by updated_at desc")
        return [_from_row(row) for row in rows]

    def upsert(self, scope: str, team_id: str | None, personality: str, rules: list[dict]) -> RuleSet:
        validated = _validate_rules(rules)
        now = _now()
        existing = self._find_row(scope, team_id)
        if existing is None:
            self._db.execute(
                "insert into rule_sets (id, scope, team_id, personality, rules_json, updated_at) "
                "values (?, ?, ?, ?, ?, ?)",
                (uuid4().hex, scope, team_id, personality,
                 json.dumps(validated, ensure_ascii=False), now),
            )
        else:
            self._db.execute(
                "update rule_sets set personality = ?, rules_json = ?, updated_at = ? where id = ?",
                (personality, json.dumps(validated, ensure_ascii=False), now, existing["id"]),
            )
        return self._get_or_empty(scope, team_id)

    def delete_team(self, team_id: str) -> None:
        self._db.execute("delete from rule_sets where scope = 'team' and team_id = ?", (team_id,))

    def snapshot_for_team(self, team_id: str | None) -> dict:
        team = self.get_team(team_id) if team_id else None
        return {
            "global": _as_dict(self.get_global()),
            "team": _as_dict(team) if team else None,
            "persona_baseline": _as_dict(self.get_persona_baseline()),
        }

    def _find_row(self, scope: str, team_id: str | None):
        if team_id is None:
            return self._db.fetchone(
                "select * from rule_sets where scope = ? and team_id is null", (scope,)
            )
        return self._db.fetchone(
            "select * from rule_sets where scope = ? and team_id = ?", (scope, team_id)
        )

    def _get_or_empty(self, scope: str, team_id: str | None) -> RuleSet:
        row = self._find_row(scope, team_id)
        if row is None:
            return RuleSet(id="", scope=scope, team_id=team_id, personality="", rules=[], updated_at="")
        return _from_row(row)


def _from_row(row) -> RuleSet:
    return RuleSet(
        id=row["id"],
        scope=row["scope"],
        team_id=row["team_id"],
        personality=row["personality"],
        rules=list(json.loads(row["rules_json"])),
        updated_at=row["updated_at"],
    )


def _as_dict(rule_set: RuleSet) -> dict:
    return {"personality": rule_set.personality, "rules": rule_set.rules}
