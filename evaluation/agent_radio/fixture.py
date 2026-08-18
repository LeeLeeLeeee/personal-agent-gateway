"""Fixture definitions and run records, and the rules that make one usable.

Evaluation tooling, deliberately outside the shipped package: it is not
product code and must never be deployed with it. It also never calls a model.
Its whole value at this stage is failing before anyone pays for a provider
call.
"""

import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

FIXTURE_SCHEMA = "gateway.eval-fixture/v1"
FIXTURE_TYPES = frozenset(
    {"understanding", "architecture_impact", "bounded_implementation"}
)
EXECUTION_PROFILES = frozenset({"read_only", "bounded_write"})
RUBRIC_SIZE = range(3, 7)

# The ADR's Stage 0 gate is that evaluation makes no real external mutation.
# These are the commands that reach past this machine; a local commit does not.
# "curl -x" is deliberately absent: -X is the request-method flag, and this is
# an API project where "curl -X POST" is an ordinary ask, not a mutation past
# this machine.
FORBIDDEN_GOAL_COMMANDS = (
    "git push",
    "git remote add",
    "npm publish",
    "pypi upload",
    "twine upload",
    "gh pr",
    "gh release",
    "rm -rf /",
)
# A bare leading "/" cannot be refused here: this project is a gateway whose
# routes all start with "/api/", so "/api/events 가 어떤 이벤트를 내보내는지
# 설명하라" is the prototypical fixture, not an edge case. Only refuse a path
# whose first segment names a real filesystem root, so an HTTP route stays
# distinguishable from a filesystem path.
_POSIX_ROOTS = (
    "etc", "usr", "var", "home", "root", "bin", "sbin", "tmp",
    "proc", "sys", "dev", "opt", "mnt", "media",
)
_ABSOLUTE_PATH = re.compile(
    r"(?:^|\s)(?:[A-Za-z]:[\\/]|~[\\/]|/(?:" + "|".join(_POSIX_ROOTS) + r")(?:[\\/]|$))"
)
_PARENT_ESCAPE = re.compile(r"(?:^|[\s(\"'])\.\.[\\/]")


class FixtureError(ValueError):
    """A definition or record that cannot be used as evidence."""


@dataclass(frozen=True)
class RubricItem:
    id: str
    criterion: str
    check: str


@dataclass(frozen=True)
class Fixture:
    id: str
    type: str
    title: str
    goal: str
    repo_ref: str
    execution_profile: str
    rubric: tuple[RubricItem, ...]
    sha256: str


def git_commit_exists(ref: str) -> bool:
    """Whether this repository has that commit.

    Injected everywhere it is used so the rules can be tested without a git
    repository, and so a caller working on a different checkout can say so.
    """
    try:
        result = subprocess.run(
            ["git", "cat-file", "-e", f"{ref}^{{commit}}"],
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise FixtureError("git is not available on PATH") from exc
    return result.returncode == 0


def parse_fixture(
    payload: dict,
    *,
    sha256: str,
    commit_exists: Callable[[str], bool] = git_commit_exists,
) -> Fixture:
    if not isinstance(payload, dict):
        raise FixtureError("fixture is not an object")
    if payload.get("schema") != FIXTURE_SCHEMA:
        raise FixtureError(f"unknown fixture schema: {payload.get('schema')!r}")
    identifier = _required_text(payload, "id")
    fixture_type = _required_text(payload, "type")
    if fixture_type not in FIXTURE_TYPES:
        raise FixtureError(f"unknown fixture type: {fixture_type!r}")
    profile = _required_text(payload, "execution_profile")
    if profile not in EXECUTION_PROFILES:
        raise FixtureError(f"unknown execution profile: {profile!r}")
    goal = _required_text(payload, "goal")
    _refuse_unsafe_goal(goal)
    repo_ref = _required_text(payload, "repo_ref")
    if not commit_exists(repo_ref):
        raise FixtureError(f"repo_ref is not a commit in this repository: {repo_ref!r}")
    return Fixture(
        id=identifier,
        type=fixture_type,
        title=_required_text(payload, "title"),
        goal=goal,
        repo_ref=repo_ref,
        execution_profile=profile,
        rubric=_parse_rubric(payload.get("rubric")),
        sha256=sha256,
    )


def load_fixture(
    path: Path,
    *,
    commit_exists: Callable[[str], bool] = git_commit_exists,
) -> Fixture:
    raw = path.read_bytes()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FixtureError(f"{path.name} is not JSON") from exc
    return parse_fixture(
        payload,
        sha256=hashlib.sha256(raw).hexdigest(),
        commit_exists=commit_exists,
    )


def load_fixtures(
    directory: Path,
    *,
    commit_exists: Callable[[str], bool] = git_commit_exists,
) -> dict[str, Fixture]:
    fixtures: dict[str, Fixture] = {}
    for path in sorted(directory.glob("*.json")):
        fixture = load_fixture(path, commit_exists=commit_exists)
        if fixture.id in fixtures:
            raise FixtureError(f"two definitions claim the id {fixture.id!r}")
        fixtures[fixture.id] = fixture
    return fixtures


def _parse_rubric(value: object) -> tuple[RubricItem, ...]:
    if not isinstance(value, list) or len(value) not in RUBRIC_SIZE:
        raise FixtureError(
            f"a rubric needs {RUBRIC_SIZE.start}-{RUBRIC_SIZE.stop - 1} items"
        )
    items: list[RubricItem] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != {"id", "criterion", "check"}:
            raise FixtureError("malformed rubric item")
        item = RubricItem(
            _required_text(raw, "id"),
            _required_text(raw, "criterion"),
            _required_text(raw, "check"),
        )
        if item.id in seen:
            raise FixtureError(f"duplicate rubric id: {item.id!r}")
        seen.add(item.id)
        items.append(item)
    return tuple(items)


def _refuse_unsafe_goal(goal: str) -> None:
    """Stop a definition asking for something no evaluation should do.

    Matching is deliberately narrow. Almost every real goal names a file in
    this repository, so a rule that rejects those would be switched off within
    a week -- which is worse than no rule.
    """
    # Runs of whitespace are normalized to a single space so "git    push" and
    # "git\tpush" match the same as "git push". This is about accidental
    # phrasing, not adversarial evasion -- these definitions live in git and
    # get reviewed, so "git-push" (a hyphenated script name, not the command)
    # is deliberately left alone.
    lowered = re.sub(r"\s+", " ", goal.lower())
    for command in FORBIDDEN_GOAL_COMMANDS:
        if command in lowered:
            raise FixtureError(f"goal asks for an external mutation: {command!r}")
    if _ABSOLUTE_PATH.search(goal):
        raise FixtureError("goal names an absolute path")
    if _PARENT_ESCAPE.search(goal):
        raise FixtureError("goal reaches outside the repository")


def _required_text(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise FixtureError(f"{key} is missing or empty")
    return value.strip()


RECORD_SCHEMA = "gateway.eval-record/v1"
MODES = frozenset({"single_agent", "legacy", "radio_lite", "passive"})


@dataclass(frozen=True)
class RubricResult:
    id: str
    passed: bool
    note: str


@dataclass(frozen=True)
class Record:
    fixture_id: str
    fixture_sha256: str
    # Which execution this verdict is about. Without it a record cannot be
    # traced back to the artefact that says whether the run was even scoreable
    # -- whether it stayed isolated, what it cost, which commit it read. One
    # record per fixture hides the need; the moment repeats exist, two verdicts
    # for the same fixture become indistinguishable.
    run_id: str
    mode: str
    repeat: int
    harness_version: str
    started_at: str
    finished_at: str
    wall_ms: int
    cost: dict[str, object]
    rubric_results: tuple[RubricResult, ...]
    rework_count: int
    conflict_count: int
    critical_defects_found: int
    mode_metrics: dict[str, object]

    @property
    def succeeded(self) -> bool:
        """Every item passed. Items are binary, so there is no middle."""
        return all(result.passed for result in self.rubric_results)


def parse_record(payload: dict) -> Record:
    if not isinstance(payload, dict):
        raise FixtureError("record is not an object")
    if payload.get("schema") != RECORD_SCHEMA:
        raise FixtureError(f"unknown record schema: {payload.get('schema')!r}")
    mode = _required_text(payload, "mode")
    if mode not in MODES:
        raise FixtureError(f"unknown mode: {mode!r}")
    results = payload.get("rubric_results")
    if not isinstance(results, list) or not results:
        raise FixtureError("a record needs at least one rubric result")
    for key in ("cost", "mode_metrics"):
        if not isinstance(payload.get(key), dict):
            raise FixtureError(f"{key} must be an object")
    return Record(
        fixture_id=_required_text(payload, "fixture_id"),
        fixture_sha256=_required_text(payload, "fixture_sha256"),
        run_id=_required_text(payload, "run_id"),
        mode=mode,
        repeat=_positive_int(payload, "repeat"),
        harness_version=_required_text(payload, "harness_version"),
        started_at=_required_text(payload, "started_at"),
        finished_at=_required_text(payload, "finished_at"),
        wall_ms=_counter(payload, "wall_ms"),
        cost=dict(payload["cost"]),
        rubric_results=_parse_results(results),
        rework_count=_counter(payload, "rework_count"),
        conflict_count=_counter(payload, "conflict_count"),
        critical_defects_found=_counter(payload, "critical_defects_found"),
        mode_metrics=dict(payload["mode_metrics"]),
    )


def load_records(directory: Path) -> list[Record]:
    records: list[Record] = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_bytes())
        except json.JSONDecodeError as exc:
            raise FixtureError(f"{path.name} is not JSON") from exc
        records.append(parse_record(payload))
    return records


def is_stale(record: Record, fixtures: Mapping[str, Fixture]) -> bool:
    """Whether this record was measured against a definition that no longer
    exists in that form.

    An unknown fixture counts as stale rather than as an error: a definition
    can legitimately be retired, and the records it produced then describe an
    experiment nobody can reproduce.
    """
    fixture = fixtures.get(record.fixture_id)
    return fixture is None or fixture.sha256 != record.fixture_sha256


def rubric_is_fully_reported(record: Record, fixture: Fixture) -> bool:
    """Whether the record answers exactly the rubric it claims to measure.

    Kept separate from is_stale because the two say different things and a
    reader has to be able to tell them apart: stale means someone changed the
    definition after the fact, this means someone reported fewer items than the
    definition asks for. Folding the second into the first would report a
    scoring gap as tampering.
    """
    return {result.id for result in record.rubric_results} == {
        item.id for item in fixture.rubric
    }


def _parse_results(raw_results: list) -> tuple[RubricResult, ...]:
    results: list[RubricResult] = []
    seen: set[str] = set()
    for raw in raw_results:
        result = _parse_result(raw)
        if result.id in seen:
            raise FixtureError(f"duplicate rubric result id: {result.id!r}")
        seen.add(result.id)
        results.append(result)
    return tuple(results)


def _parse_result(raw: object) -> RubricResult:
    if not isinstance(raw, dict) or set(raw) != {"id", "passed", "note"}:
        raise FixtureError("malformed rubric result")
    if not isinstance(raw["passed"], bool):
        raise FixtureError("rubric result passed must be a boolean")
    return RubricResult(_required_text(raw, "id"), raw["passed"], str(raw["note"]))


def _counter(payload: dict, key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise FixtureError(f"{key} must be a non-negative integer")
    return value


def _positive_int(payload: dict, key: str) -> int:
    value = _counter(payload, key)
    if value < 1:
        raise FixtureError(f"{key} must be at least 1")
    return value
