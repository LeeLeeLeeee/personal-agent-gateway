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
from collections.abc import Callable
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
FORBIDDEN_GOAL_COMMANDS = (
    "git push",
    "git remote add",
    "npm publish",
    "pypi upload",
    "twine upload",
    "gh pr",
    "gh release",
    "curl -x",
    "rm -rf /",
)
_ABSOLUTE_PATH = re.compile(r"(?:^|\s)(?:/[\w.]|[A-Za-z]:[\\/])")
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
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{ref}^{{commit}}"],
        capture_output=True,
        check=False,
    )
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
    lowered = goal.lower()
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
