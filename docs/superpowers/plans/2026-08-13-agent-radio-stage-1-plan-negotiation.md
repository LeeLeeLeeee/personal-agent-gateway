# Stage 1 Plan Negotiation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Team Run with negotiation enabled does not execute its plan until every worker who owns part of it has approved that exact revision.

**Architecture:** `_plan` already creates the tasks, so negotiation reviews created-but-unstarted `pending` tasks rather than a pre-DB proposal. A new phase sits between `_plan` and the `running` transition in `start()`. Pure decision logic (unanimity, the revision cap, the review contract) lives in a new module with no database; persistence goes in `teams.py` beside the other team tables; the runtime only orchestrates.

**Tech Stack:** Python 3.13, FastAPI, SQLite, pytest; React (Vite) with Vitest.

**Spec:** `docs/superpowers/specs/2026-08-13-agent-radio-stage-1-plan-negotiation-design.md`
**Parent decision:** `docs/adr/2026-08-13-agent-radio-team-collaboration.md`

## Global Constraints

- **Negotiation is opt-in per run.** A run with `plan_negotiation_enabled = 0` must take byte-for-byte the same code path it takes today. Every task that touches the runtime proves this with a test.
- **`PLAN_NEGOTIATION_MAX_REVISIONS = 3`.** The cap is checked in the single function that creates a revision — never at a call site. Two loop defects in this repo were "the cap existed but only one path checked it".
- **Superseded and abandoned tasks become `canceled`, never `skipped`.** `_required_terminal_cause` (`team_lifecycle.py:195-202`) raises `LifecycleIntegrityError` for a `skipped` task with no prerequisite, and draft-plan tasks usually have none. `pending → canceled` is a legal transition (`team_lifecycle.py:63`).
- **The failed-negotiation terminal status is set explicitly, never derived.** `cycle_execution_disposition` (`team_lifecycle.py:161-167`) returns `failed` when a required task's cause is `canceled`, so deriving would contradict the spec's `completed_with_failures`.
- **Tasks are shown to reviewers as `T-<plan_ordinal>` labels, never task IDs.** Label matching is exact set membership — never a substring test, so `T-1` is not read as `T-10`.
- Reviewer objection `kind` is exactly `overlap` · `gap` · `dependency_conflict` · `scope`.
- The leader is never an approver of its own plan.
- Backend tests: `PYTHONPATH=src python -m pytest <files> -q -p no:randomly` from the repo root. Quote any `-k` containing spaces — unquoted, pytest runs zero tests and reports success.
- Lint: `python -m ruff check <files>` (project ruff 0.15.20). Ignore `.venv/Scripts/ruff.exe`.
- **Backend baseline is 0 failures**: `1575 passed / 4 skipped` as of 2026-08-13. Frontend: `41 files / 401 tests / 0 failures`. Any failure you see is yours.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/personal_agent_gateway/team_plan_negotiation.py` (new) | Pure logic: the cap, unanimity, the review contract parser. No DB, no I/O. |
| `src/personal_agent_gateway/migrations.py` | Migration 31: two tables, one unique index, one `team_runs` column. |
| `src/personal_agent_gateway/teams.py` | Persistence + `TeamRun.plan_negotiation_enabled`. |
| `src/personal_agent_gateway/team_model_operations.py` | `plan_review`, `plan_review_repair` stages. |
| `src/personal_agent_gateway/team_repair_stages.py` | `REPAIR_STAGE` entry for the new stage. |
| `src/personal_agent_gateway/team_runtime.py` | `PLAN_REVIEW_PROMPT`, `_negotiate_plan`, wiring in `start()`. |
| `src/personal_agent_gateway/api/team_runs.py` | `plan_negotiation` in create; revisions in `/detail`. |
| `frontend/src/api/client.js`, `.../TeamRunDetail/PlanNegotiation.jsx` (new), `.../TeamRunDetail/index.jsx` | Show the current revision, who must approve, and the objections. |

---

## Task 1: Schema and the run flag

**Files:**
- Modify: `src/personal_agent_gateway/migrations.py` — add `_migration_31_team_plan_negotiation` and its `MIGRATIONS` entry after `(30, "operation-failure-shape", ...)`
- Modify: `src/personal_agent_gateway/teams.py` — `TeamRun` (line 49), its row mapper, `create_team_run` (line 235)
- Test: `tests/test_migrations.py`, `tests/test_teams.py`

**Interfaces:**
- Produces: tables `team_plan_revisions` and `team_plan_approvals`; `TeamRun.plan_negotiation_enabled: bool = False`; `create_team_run(..., plan_negotiation: bool = False)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_migrations.py` has **no shared database helper** — every test in it opens `sqlite3.connect(":memory:")`, creates only the tables that migration touches, and calls the migration function twice to prove idempotency. Follow that pattern exactly; read `test_migration_20_creates_team_model_operation_ledger_idempotently` (line 19) as the template.

```python
def _plan_negotiation_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("pragma foreign_keys = on")
    connection.executescript("create table team_runs (id text primary key);")
    return connection


def test_migration_31_creates_plan_negotiation_tables_idempotently() -> None:
    """Two tables and one column. Migrations run on every startup against
    databases at any version, so applying it twice must be a no-op."""
    connection = _plan_negotiation_connection()

    _migration_31_team_plan_negotiation(connection)
    _migration_31_team_plan_negotiation(connection)

    tables = {
        row["name"]
        for row in connection.execute("select name from sqlite_master where type='table'")
    }
    assert {"team_plan_revisions", "team_plan_approvals"} <= tables
    assert "plan_negotiation_enabled" in _columns(connection, "team_runs")


def test_one_review_per_agent_per_revision() -> None:
    """Resume has to answer 'did this agent already review this revision'
    atomically. A check-then-insert races; the index does not."""
    connection = _plan_negotiation_connection()
    _migration_31_team_plan_negotiation(connection)
    connection.execute(
        "insert into team_plan_revisions (id, team_run_id, cycle_id, revision,"
        " status, created_at) values ('r1', NULL, NULL, 1, 'awaiting_approval', 't')"
    )
    connection.execute(
        "insert into team_plan_approvals (id, plan_revision_id, agent_id, decision,"
        " objections_json, created_at) values ('a1','r1','agent-1','approve','[]','t')"
    )

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "insert into team_plan_approvals (id, plan_revision_id, agent_id, decision,"
            " objections_json, created_at) values ('a2','r1','agent-1','object','[]','t')"
        )
```

Note the first insert leaves `team_run_id` NULL, which the minimal `team_runs` table's foreign key permits. Add `pytest` and `_migration_31_team_plan_negotiation` to that file's imports — it already imports `sqlite3` and the other migration functions.

In `tests/test_teams.py`:

```python
def test_a_run_defaults_to_negotiation_off(tmp_path: Path) -> None:
    """Every stored run predates this column, and an existing run must keep
    behaving exactly as it did."""
    teams, run, _cycle = _run_with_cycle_and_agents(tmp_path)

    assert teams.get_team_run(run.id).plan_negotiation_enabled is False
```

`_run_with_cycle_and_agents(tmp_path)` is the existing helper at `tests/test_teams.py:99`; it returns `(teams, run, cycle)` and builds a `triggered` continuous run through `make_cycle_services`. Use it rather than calling `create_team_run` directly — the service validates `lifecycle_mode` against `execution_policy` and a hand-built run raises.

For the opt-in direction, `make_cycle_services` does not take the new flag, so assert it through `create_team_run` on the service that helper returns:

```python
def test_negotiation_can_be_requested_at_creation(tmp_path: Path) -> None:
    teams, run, _cycle = _run_with_cycle_and_agents(tmp_path)
    source = teams.get_team_run(run.id)

    negotiated = teams.create_team_run(
        source.goal,
        _leader_persona_id(teams, run.id),
        [_member_persona_id(teams, run.id)],
        "plan_and_execute",
        1,
        plan_negotiation=True,
    )

    assert teams.get_team_run(negotiated.id).plan_negotiation_enabled is True
```

`_leader_persona_id` / `_member_persona_id` read `teams.list_agents(run.id)` and return the `persona_id` of the leader and of the first non-leader. Write them in the test file; `TeamAgent.role` is `"leader"` for the leader (`_find_workers` filters on exactly that).

- [ ] **Step 2: Run them and watch them fail**

Run: `PYTHONPATH=src python -m pytest tests/test_migrations.py tests/test_teams.py -q -p no:randomly -k "plan_negotiation or one_review_per_agent or migration_31"`
Expected: FAIL — no such table, and `TeamRun` has no such attribute.

- [ ] **Step 3: Write migration 31**

```python
def _migration_31_team_plan_negotiation(connection: sqlite3.Connection) -> None:
    if "plan_negotiation_enabled" not in _columns(connection, "team_runs"):
        connection.execute(
            "alter table team_runs add column plan_negotiation_enabled"
            " integer not null default 0"
        )
    connection.executescript(
        """
        create table if not exists team_plan_revisions (
            id text primary key,
            team_run_id text not null,
            cycle_id text,
            revision integer not null,
            status text not null,
            task_ids_json text not null default '[]',
            required_approver_agent_ids_json text not null default '[]',
            created_at text not null,
            decided_at text,
            foreign key (team_run_id) references team_runs(id) on delete cascade
        );

        create unique index if not exists idx_team_plan_revisions_number
        on team_plan_revisions(team_run_id, cycle_id, revision);

        create table if not exists team_plan_approvals (
            id text primary key,
            plan_revision_id text not null,
            agent_id text not null,
            decision text not null,
            objections_json text not null default '[]',
            created_at text not null,
            foreign key (plan_revision_id)
                references team_plan_revisions(id) on delete cascade
        );

        create unique index if not exists idx_team_plan_approvals_one_per_agent
        on team_plan_approvals(plan_revision_id, agent_id);
        """
    )
```

Register it:

```python
    (31, "team-plan-negotiation", _migration_31_team_plan_negotiation),
```

Note `cycle_id` is nullable and takes part in the revision unique index. SQLite treats NULLs as distinct in unique indexes, so a cycle-less run could in principle store two revision 1 rows — that path is dead (every run is continuous) and the negotiation phase refuses to run without a cycle in Task 5, so do not add a workaround here.

- [ ] **Step 4: Carry the flag on `TeamRun`**

Add to the dataclass, in the trailing defaulted block so existing positional construction keeps working:

```python
    plan_negotiation_enabled: bool = False
```

In the row mapper, read it defensively the way the other late columns are read — find how `parent_team_run_id` is mapped and follow it, converting with `bool(row["plan_negotiation_enabled"])`.

In `create_team_run`, add the keyword after `parent_team_run_id`:

```python
        plan_negotiation: bool = False,
```

and include `1 if plan_negotiation else 0` in the insert's column list and values.

- [ ] **Step 5: Run the tests and watch them pass**

Run: `PYTHONPATH=src python -m pytest tests/test_migrations.py tests/test_teams.py -q -p no:randomly`
Expected: all pass. If a test asserts the exact column list of `team_runs` or an exact `TeamRun` tuple, extend it rather than reverting the column, and say which test it was.

- [ ] **Step 6: Lint and commit**

```bash
python -m ruff check src/personal_agent_gateway/migrations.py src/personal_agent_gateway/teams.py tests/test_migrations.py tests/test_teams.py
git add src/personal_agent_gateway/migrations.py src/personal_agent_gateway/teams.py tests/test_migrations.py tests/test_teams.py
git commit -m "feat(team-runs): store plan revisions and per-agent approvals"
```

---

## Task 2: The decision rules, with no database

The cap and unanimity are where this feature is correct or wrong. They go in a pure module so they can be tested exhaustively without a runtime.

**Files:**
- Create: `src/personal_agent_gateway/team_plan_negotiation.py`
- Test: `tests/test_team_plan_negotiation.py`

**Interfaces:**
- Produces:
  - `PLAN_NEGOTIATION_MAX_REVISIONS: int = 3`
  - `NegotiationVerdict = Literal["approved", "objected", "waiting"]`
  - `verdict_for(required_approver_ids: Sequence[str], reviews: Mapping[str, str]) -> NegotiationVerdict`
  - `next_revision(current: int) -> int | None` — `None` when the cap is spent
  - `task_label(plan_ordinal: int) -> str` — `"T-01"`
  - `parse_plan_review(text: str, allowed_labels: Set[str]) -> PlanReview` (Task 3 adds this to the same module)

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from personal_agent_gateway.team_plan_negotiation import (
    PLAN_NEGOTIATION_MAX_REVISIONS,
    next_revision,
    task_label,
    verdict_for,
)


def test_every_required_approver_must_approve_the_same_revision():
    assert verdict_for(["a", "b"], {"a": "approve", "b": "approve"}) == "approved"


def test_one_missing_review_is_not_approval():
    """Reviews arrive one model call at a time, and a crash between them must
    not read as consent."""
    assert verdict_for(["a", "b"], {"a": "approve"}) == "waiting"


def test_one_objection_decides_the_revision_without_waiting_for_the_rest():
    """The plan is already going to be replaced, so spending model calls on the
    remaining reviewers buys nothing."""
    assert verdict_for(["a", "b", "c"], {"a": "approve", "b": "object"}) == "objected"


def test_an_empty_required_set_is_not_silently_approved():
    """No approvers means the caller computed the set wrongly. Returning
    'approved' here would execute an unreviewed plan."""
    with pytest.raises(ValueError):
        verdict_for([], {})


def test_a_review_from_someone_who_was_not_asked_is_ignored():
    assert verdict_for(["a"], {"a": "approve", "stranger": "object"}) == "approved"


@pytest.mark.parametrize(
    ("current", "expected"),
    [(1, 2), (2, 3), (3, None)],
)
def test_the_cap_is_three_revisions(current, expected):
    assert next_revision(current) == expected


def test_the_cap_constant_is_three():
    assert PLAN_NEGOTIATION_MAX_REVISIONS == 3


def test_a_revision_beyond_the_cap_never_yields_another():
    """Defensive: a stored revision above the cap must not wrap around into a
    fresh budget. The two loop defects in this repo both resumed from stored
    state that the cap check trusted."""
    assert next_revision(4) is None
    assert next_revision(99) is None


@pytest.mark.parametrize(
    ("ordinal", "label"), [(0, "T-00"), (1, "T-01"), (9, "T-09"), (10, "T-10")]
)
def test_labels_are_zero_padded_to_two_digits(ordinal, label):
    assert task_label(ordinal) == label
```

- [ ] **Step 2: Run them and watch them fail**

Run: `PYTHONPATH=src python -m pytest tests/test_team_plan_negotiation.py -q -p no:randomly`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write the module**

```python
"""What negotiation decides, with nothing to mock.

The cap and the unanimity rule are the two places this feature is correct or
wrong, so they live here rather than inside the runtime: a test can enumerate
every combination without a database, a model client, or an event loop.
"""

from collections.abc import Mapping, Sequence
from typing import Literal

PLAN_NEGOTIATION_MAX_REVISIONS = 3

NegotiationVerdict = Literal["approved", "objected", "waiting"]


def verdict_for(
    required_approver_ids: Sequence[str],
    reviews: Mapping[str, str],
) -> NegotiationVerdict:
    """Decide a revision from the reviews collected so far.

    An objection settles it immediately: the plan is going to be replaced, so
    asking the remaining reviewers spends model calls on a dead revision. A
    missing review is never consent -- reviews arrive one call at a time and a
    crash between them must not read as approval.
    """
    if not required_approver_ids:
        raise ValueError("negotiation requires at least one approver")
    required = list(required_approver_ids)
    if any(reviews.get(agent_id) == "object" for agent_id in required):
        return "objected"
    if all(reviews.get(agent_id) == "approve" for agent_id in required):
        return "approved"
    return "waiting"


def next_revision(current: int) -> int | None:
    """The revision after ``current``, or None when the budget is spent.

    Every caller goes through here. Both loop defects previously fixed in this
    repo were a cap that one path checked and another did not, so this function
    also refuses to hand out a budget for a stored revision that is already
    past the cap rather than assuming it cannot happen.
    """
    if current >= PLAN_NEGOTIATION_MAX_REVISIONS:
        return None
    return current + 1


def task_label(plan_ordinal: int) -> str:
    """How a task is named to a reviewer.

    Task IDs are UUIDs. Asking a model to echo one back invites hallucination,
    and the label is both shorter and exactly checkable.
    """
    return f"T-{plan_ordinal:02d}"
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `PYTHONPATH=src python -m pytest tests/test_team_plan_negotiation.py -q -p no:randomly`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check src/personal_agent_gateway/team_plan_negotiation.py tests/test_team_plan_negotiation.py
git add src/personal_agent_gateway/team_plan_negotiation.py tests/test_team_plan_negotiation.py
git commit -m "feat(team-runs): decide plan negotiation with a capped, testable rule"
```

---

## Task 3: The reviewer's output contract

**Files:**
- Modify: `src/personal_agent_gateway/team_plan_negotiation.py`
- Test: `tests/test_team_plan_negotiation.py`

**Interfaces:**
- Consumes: `task_label` from Task 2.
- Produces:
  - `OBJECTION_KINDS: frozenset[str]` = `{"overlap", "gap", "dependency_conflict", "scope"}`
  - `@dataclass(frozen=True) class Objection: kind: str; task_ref: str; detail: str`
  - `@dataclass(frozen=True) class PlanReview: decision: Literal["approve", "object"]; objections: tuple[Objection, ...]`
  - `class PlanReviewError(ValueError)`
  - `parse_plan_review(text: str, allowed_labels: frozenset[str]) -> PlanReview`

- [ ] **Step 1: Write the failing tests**

```python
import json

from personal_agent_gateway.team_plan_negotiation import (
    PlanReviewError,
    parse_plan_review,
)

_LABELS = frozenset({"T-01", "T-02", "T-10"})


def test_an_approval_carries_no_objections():
    review = parse_plan_review(
        json.dumps({"decision": "approve", "objections": []}), _LABELS
    )

    assert review.decision == "approve"
    assert review.objections == ()


def test_an_objection_keeps_its_kind_reference_and_detail():
    review = parse_plan_review(
        json.dumps(
            {
                "decision": "object",
                "objections": [
                    {"kind": "overlap", "task_ref": "T-02", "detail": "같은 파일"}
                ],
            }
        ),
        _LABELS,
    )

    (objection,) = review.objections
    assert (objection.kind, objection.task_ref, objection.detail) == (
        "overlap",
        "T-02",
        "같은 파일",
    )


def test_a_fenced_response_still_parses():
    """Models wrap JSON in code fences no matter what the prompt says."""
    review = parse_plan_review(
        '```json\n{"decision": "approve", "objections": []}\n```', _LABELS
    )

    assert review.decision == "approve"


@pytest.mark.parametrize(
    "payload",
    [
        # objecting with nothing to act on -- unusable for replanning
        {"decision": "object", "objections": []},
        # approving while objecting: the two fields contradict each other
        {"decision": "approve", "objections": [
            {"kind": "gap", "task_ref": "T-01", "detail": "d"}]},
        # a kind outside the four the design allows
        {"decision": "object", "objections": [
            {"kind": "style", "task_ref": "T-01", "detail": "d"}]},
        # a label that is not in this revision
        {"decision": "object", "objections": [
            {"kind": "gap", "task_ref": "T-99", "detail": "d"}]},
        # empty detail gives the leader nothing to replan from
        {"decision": "object", "objections": [
            {"kind": "gap", "task_ref": "T-01", "detail": "   "}]},
        # a decision value outside the two allowed
        {"decision": "revise", "objections": []},
        # missing key
        {"decision": "approve"},
        # extra key
        {"decision": "approve", "objections": [], "confidence": 0.9},
    ],
)
def test_incoherent_reviews_are_rejected(payload):
    with pytest.raises(PlanReviewError):
        parse_plan_review(json.dumps(payload), _LABELS)


def test_prose_instead_of_json_is_rejected():
    with pytest.raises(PlanReviewError):
        parse_plan_review("계획이 괜찮아 보입니다.", _LABELS)


def test_a_short_label_is_not_read_as_a_longer_one():
    """T-1 must not be accepted as a reference to T-10. Substring matching is
    how three separate path checks in this repo were wrong before."""
    with pytest.raises(PlanReviewError):
        parse_plan_review(
            json.dumps(
                {
                    "decision": "object",
                    "objections": [{"kind": "gap", "task_ref": "T-1", "detail": "d"}],
                }
            ),
            _LABELS,
        )
```

- [ ] **Step 2: Run them and watch them fail**

Run: `PYTHONPATH=src python -m pytest tests/test_team_plan_negotiation.py -q -p no:randomly -k "review or label_is_not_read"`
Expected: FAIL — `parse_plan_review` does not exist.

- [ ] **Step 3: Implement the parser**

Add to `team_plan_negotiation.py`:

```python
import json
from dataclasses import dataclass

OBJECTION_KINDS = frozenset({"overlap", "gap", "dependency_conflict", "scope"})


class PlanReviewError(ValueError):
    """The reviewer's response cannot be acted on."""


@dataclass(frozen=True)
class Objection:
    kind: str
    task_ref: str
    detail: str


@dataclass(frozen=True)
class PlanReview:
    decision: Literal["approve", "object"]
    objections: tuple[Objection, ...]


def parse_plan_review(text: str, allowed_labels: frozenset[str]) -> PlanReview:
    """Read a reviewer's verdict, refusing anything the leader cannot replan from.

    The two fields have to agree. An objection with no items, or an approval
    carrying items, is a model hedging -- and a hedge recorded as either answer
    is worse than a parse failure, which the repair path already handles.
    """
    payload = _json_object(text)
    if set(payload) != {"decision", "objections"}:
        raise PlanReviewError("unexpected keys")
    decision = payload["decision"]
    raw_objections = payload["objections"]
    if decision not in {"approve", "object"}:
        raise PlanReviewError("unknown decision")
    if not isinstance(raw_objections, list):
        raise PlanReviewError("objections must be a list")
    objections = tuple(
        _objection(raw, allowed_labels) for raw in raw_objections
    )
    if decision == "object" and not objections:
        raise PlanReviewError("objecting without an objection")
    if decision == "approve" and objections:
        raise PlanReviewError("approving while objecting")
    return PlanReview(decision, objections)


def _objection(raw: object, allowed_labels: frozenset[str]) -> Objection:
    if not isinstance(raw, dict) or set(raw) != {"kind", "task_ref", "detail"}:
        raise PlanReviewError("malformed objection")
    kind = raw["kind"]
    task_ref = raw["task_ref"]
    detail = raw["detail"]
    if kind not in OBJECTION_KINDS:
        raise PlanReviewError(f"unknown objection kind: {kind!r}")
    if not isinstance(task_ref, str) or task_ref not in allowed_labels:
        # Exact set membership. A substring test would let T-1 stand in for T-10.
        raise PlanReviewError(f"unknown task reference: {task_ref!r}")
    if not isinstance(detail, str) or not detail.strip():
        raise PlanReviewError("objection has no detail")
    return Objection(kind, task_ref, detail.strip())


def _json_object(text: str) -> dict[str, object]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = [
            line for line in stripped.splitlines() if not line.startswith("```")
        ]
        stripped = "\n".join(lines).strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise PlanReviewError("response is not JSON") from exc
    if not isinstance(payload, dict):
        raise PlanReviewError("response is not a JSON object")
    return payload
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `PYTHONPATH=src python -m pytest tests/test_team_plan_negotiation.py -q -p no:randomly`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check src/personal_agent_gateway/team_plan_negotiation.py tests/test_team_plan_negotiation.py
git add src/personal_agent_gateway/team_plan_negotiation.py tests/test_team_plan_negotiation.py
git commit -m "feat(team-runs): reject a plan review the leader cannot replan from"
```

---

## Task 4: Persistence

**Files:**
- Modify: `src/personal_agent_gateway/teams.py`
- Test: `tests/test_teams.py`

**Interfaces:**
- Consumes: `next_revision` from Task 2; the tables from Task 1.
- Produces on `TeamRunService`:
  - `@dataclass(frozen=True) class TeamPlanRevision` with `id, team_run_id, cycle_id, revision, status, task_ids, required_approver_agent_ids, created_at, decided_at`
  - `create_plan_revision(team_run_id, cycle_id, task_ids, required_approver_agent_ids) -> TeamPlanRevision | None` — `None` when the cap is spent
  - `record_plan_review(plan_revision_id, agent_id, decision, objections) -> None`
  - `plan_reviews(plan_revision_id) -> dict[str, str]` — agent id to decision
  - `get_active_plan_revision(team_run_id, cycle_id) -> TeamPlanRevision | None`
  - `list_plan_revisions(team_run_id, cycle_id=None) -> list[TeamPlanRevision]`
  - `set_plan_revision_status(plan_revision_id, status) -> TeamPlanRevision`

- [ ] **Step 1: Write the failing tests**

```python
def test_the_first_revision_is_one_and_the_cap_stops_the_fourth(tmp_path):
    """The cap lives in create_plan_revision, so no caller can spend a fourth
    revision by forgetting to check."""
    service, run, cycle, workers = _negotiation_fixture(tmp_path)

    revisions = []
    for _ in range(4):
        created = service.create_plan_revision(
            run.id, cycle.id, ["task-1"], [workers[0].id]
        )
        revisions.append(created)
        if created is not None:
            service.set_plan_revision_status(created.id, "superseded")

    assert [r.revision for r in revisions[:3]] == [1, 2, 3]
    assert revisions[3] is None


def test_a_review_is_recorded_once_per_agent(tmp_path):
    service, run, cycle, workers = _negotiation_fixture(tmp_path)
    revision = service.create_plan_revision(
        run.id, cycle.id, ["task-1"], [workers[0].id]
    )

    service.record_plan_review(revision.id, workers[0].id, "approve", [])

    assert service.plan_reviews(revision.id) == {workers[0].id: "approve"}
    with pytest.raises(ValueError):
        service.record_plan_review(revision.id, workers[0].id, "object", [
            {"kind": "gap", "task_ref": "T-01", "detail": "d"}
        ])


def test_only_an_awaiting_revision_is_active(tmp_path):
    service, run, cycle, workers = _negotiation_fixture(tmp_path)
    first = service.create_plan_revision(run.id, cycle.id, ["t"], [workers[0].id])

    assert service.get_active_plan_revision(run.id, cycle.id).id == first.id

    service.set_plan_revision_status(first.id, "superseded")

    assert service.get_active_plan_revision(run.id, cycle.id) is None


def test_objections_survive_the_round_trip(tmp_path):
    """These are the only record of why nothing ran, so they must come back
    exactly as written."""
    service, run, cycle, workers = _negotiation_fixture(tmp_path)
    revision = service.create_plan_revision(run.id, cycle.id, ["t"], [workers[0].id])
    objections = [{"kind": "overlap", "task_ref": "T-02", "detail": "같은 파일"}]

    service.record_plan_review(revision.id, workers[0].id, "object", objections)

    stored = service.plan_review_objections(revision.id)
    assert stored == {workers[0].id: objections}
```

The last test needs one more accessor: add `plan_review_objections(plan_revision_id) -> dict[str, list[dict[str, str]]]` to the Interfaces above and implement it alongside `plan_reviews`.

Write `_negotiation_fixture(tmp_path)` in that test file: build a service, a continuous `plan_and_execute` run with one leader and two workers, and a cycle. **Look at how the existing tests in `tests/test_teams.py` create a run with a cycle and copy that** — the service validates `lifecycle_mode` against `execution_policy`, so a hand-built run raises.

- [ ] **Step 2: Run them and watch them fail**

Run: `PYTHONPATH=src python -m pytest tests/test_teams.py -q -p no:randomly -k "revision or plan_review"`
Expected: FAIL — the methods do not exist.

- [ ] **Step 3: Implement the persistence**

Add the dataclass beside the other team dataclasses, and the methods on `TeamRunService` near the other task/message methods. `create_plan_revision` computes the current maximum revision for the run+cycle and asks `next_revision` for the next one:

```python
    def create_plan_revision(
        self,
        team_run_id: str,
        cycle_id: str | None,
        task_ids: Sequence[str],
        required_approver_agent_ids: Sequence[str],
    ) -> TeamPlanRevision | None:
        """Open the next revision, or refuse when the budget is spent.

        The cap is enforced here and only here. Callers ask for a revision and
        handle None; they never compute the budget themselves, because the two
        loop defects already fixed in this repo were exactly that.
        """
        self.get_team_run(team_run_id)
        if cycle_id is not None:
            self._require_cycle_for_run(team_run_id, cycle_id)
        if not required_approver_agent_ids:
            raise ValueError("a plan revision needs at least one approver")
        row = self._db.fetchone(
            "select coalesce(max(revision), 0) as current from team_plan_revisions"
            " where team_run_id = ? and cycle_id is ?",
            (team_run_id, cycle_id),
        )
        revision = next_revision(int(row["current"]))
        if revision is None:
            return None
        revision_id = uuid4().hex
        now = _now()
        self._db.execute(
            """
            insert into team_plan_revisions (
                id, team_run_id, cycle_id, revision, status,
                task_ids_json, required_approver_agent_ids_json, created_at
            ) values (?, ?, ?, ?, 'awaiting_approval', ?, ?, ?)
            """,
            (
                revision_id,
                team_run_id,
                cycle_id,
                revision,
                json.dumps(list(task_ids)),
                json.dumps(list(required_approver_agent_ids)),
                now,
            ),
        )
        return self._get_plan_revision(revision_id)
```

`_get_plan_revision(revision_id)` fetches one row and maps it through a module-level `_team_plan_revision_from_row`, decoding the two JSON columns into tuples. Follow `_team_task_from_row` (`teams.py:3907`) for the mapper style, `append_message` (line 3361) for the insert style, and `list_tasks` (line 1797) for how queries build `where` clauses. `uuid4`, `json` and `_now` are already imported in that module.

`record_plan_review` must let the unique index do the work: catch `sqlite3.IntegrityError` and re-raise as `ValueError("plan review already recorded")`. Do not pre-check with a select — a check-then-insert is not atomic and resume races through exactly that gap.

`get_active_plan_revision` returns the single `awaiting_approval` row for the run+cycle, or `None`.

- [ ] **Step 4: Run the tests and watch them pass**

Run: `PYTHONPATH=src python -m pytest tests/test_teams.py -q -p no:randomly`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check src/personal_agent_gateway/teams.py tests/test_teams.py
git add src/personal_agent_gateway/teams.py tests/test_teams.py
git commit -m "feat(team-runs): persist plan revisions and enforce the cap in one place"
```

---

## Task 5: The negotiation phase

The biggest task. It adds the stage pair, the prompt, and the phase itself.

**Files:**
- Modify: `src/personal_agent_gateway/team_model_operations.py:15` — `OperationStage`
- Modify: `src/personal_agent_gateway/team_repair_stages.py` — `REPAIR_STAGE`
- Modify: `src/personal_agent_gateway/team_runtime.py` — new `PLAN_REVIEW_PROMPT`, new `_negotiate_plan`, wiring between line 1641 and line 1668
- Test: `tests/test_team_repair_stages.py`, `tests/test_team_runtime.py`

**Interfaces:**
- Consumes: everything from Tasks 2-4.
- Produces: `TeamRuntime._negotiate_plan(run, leader, workers, cycle_id) -> bool` — `True` when the plan is approved and execution may proceed, `False` when the run has already been settled as `completed_with_failures`.

- [ ] **Step 1: Add the stage pair and its repair entry**

Add to `OperationStage`, after `"cycle_contest_repair"`:

```python
    "plan_review",
    "plan_review_repair",
```

Add to `REPAIR_STAGE`:

```python
    "plan_review": "plan_review_repair",
```

Run: `PYTHONPATH=src python -m pytest tests/test_team_repair_stages.py -q -p no:randomly`
Expected: all pass. Adding the stage without the `REPAIR_STAGE` entry fails `test_every_stage_has_a_repair_target` — if you see that failure, you skipped half of this step.

- [ ] **Step 2: Write the failing runtime tests**

`tests/test_team_runtime.py` is large. Read its existing helpers for building a runtime with scripted model responses (search for `make_operation_runtime` and `ModelResponse`) and use them; do not build a runtime by hand.

```python
async def test_negotiation_off_keeps_the_current_path(tmp_path) -> None:
    """The opt-in guarantee. A run without the flag must reach execution with no
    revision row and no extra model call."""
    setup = make_negotiation_runtime(tmp_path, plan_negotiation=False)

    run = await setup.runtime.start(setup.run.id, setup.cycle.id)

    assert setup.teams.list_plan_revisions(setup.run.id) == []
    assert run.status in {"completed", "completed_with_failures", "running"}


async def test_unanimous_approval_lets_execution_start(tmp_path) -> None:
    setup = make_negotiation_runtime(tmp_path, plan_negotiation=True)
    setup.worker_clients[0].responses = [_approve()]
    setup.worker_clients[1].responses = [_approve()]

    await setup.runtime.start(setup.run.id, setup.cycle.id)

    (revision,) = setup.teams.list_plan_revisions(setup.run.id)
    assert revision.status == "approved"
    assert all(
        task.status != "canceled"
        for task in setup.teams.list_tasks(setup.run.id, setup.cycle.id)
    )


async def test_no_task_starts_before_the_plan_is_approved(tmp_path) -> None:
    """The whole point. If a worker objects, nothing should have run yet."""
    setup = make_negotiation_runtime(tmp_path, plan_negotiation=True)
    setup.worker_clients[0].responses = [_object("T-01", "overlap")]

    await setup.runtime.start(setup.run.id, setup.cycle.id)

    assert setup.worker_execution_calls == 0


async def test_an_objection_supersedes_the_revision_and_replans(tmp_path) -> None:
    setup = make_negotiation_runtime(tmp_path, plan_negotiation=True)
    setup.worker_clients[0].responses = [_object("T-01", "gap"), _approve()]
    setup.worker_clients[1].responses = [_approve(), _approve()]

    await setup.runtime.start(setup.run.id, setup.cycle.id)

    revisions = setup.teams.list_plan_revisions(setup.run.id)
    assert [r.revision for r in revisions] == [1, 2]
    assert revisions[0].status == "superseded"
    assert revisions[1].status == "approved"


async def test_the_objection_text_reaches_the_leader(tmp_path) -> None:
    """A replan that cannot see the objection is a re-roll, not a revision."""
    setup = make_negotiation_runtime(tmp_path, plan_negotiation=True)
    setup.worker_clients[0].responses = [
        _object("T-01", "gap", detail="아무도 마이그레이션을 담당하지 않는다"),
        _approve(),
    ]
    setup.worker_clients[1].responses = [_approve(), _approve()]

    await setup.runtime.start(setup.run.id, setup.cycle.id)

    replan_prompt = setup.lead_client.prompts[-1]
    assert "아무도 마이그레이션을 담당하지 않는다" in replan_prompt


async def test_three_unapproved_revisions_end_the_run_without_executing(tmp_path) -> None:
    setup = make_negotiation_runtime(tmp_path, plan_negotiation=True)
    setup.worker_clients[0].responses = [_object("T-01", "gap")] * 3
    setup.worker_clients[1].responses = [_approve()] * 3

    run = await setup.runtime.start(setup.run.id, setup.cycle.id)

    assert run.status == "completed_with_failures"
    assert run.error_message == "collaboration_plan_approval_incomplete"
    assert setup.worker_execution_calls == 0
    revisions = setup.teams.list_plan_revisions(setup.run.id)
    assert [r.status for r in revisions] == ["superseded", "superseded", "abandoned"]
    assert all(
        task.status == "canceled"
        for task in setup.teams.list_tasks(setup.run.id, setup.cycle.id)
    )


async def test_an_unparsable_review_is_not_counted_as_approval(tmp_path) -> None:
    setup = make_negotiation_runtime(tmp_path, plan_negotiation=True)
    setup.worker_clients[0].responses = [
        ModelResponse("괜찮아 보입니다.", []),
        ModelResponse("여전히 괜찮습니다.", []),
    ]
    setup.worker_clients[1].responses = [_approve()]

    run = await setup.runtime.start(setup.run.id, setup.cycle.id)

    assert run.status == "completed_with_failures"
    assert setup.worker_execution_calls == 0


async def test_a_terminal_approver_cannot_approve(tmp_path) -> None:
    setup = make_negotiation_runtime(tmp_path, plan_negotiation=True)
    setup.teams.set_agent_status(setup.workers[1].id, "failed")
    setup.worker_clients[0].responses = [_approve()]

    await setup.runtime.start(setup.run.id, setup.cycle.id)

    (revision,) = setup.teams.list_plan_revisions(setup.run.id)
    assert setup.workers[1].id not in revision.required_approver_agent_ids
    assert revision.status == "approved"
```

Write these helpers in the same test file:

```python
def _approve() -> ModelResponse:
    return ModelResponse('{"decision":"approve","objections":[]}', [])


def _object(task_ref: str, kind: str, detail: str = "겹친다") -> ModelResponse:
    return ModelResponse(
        json.dumps(
            {
                "decision": "object",
                "objections": [
                    {"kind": kind, "task_ref": task_ref, "detail": detail}
                ],
            }
        ),
        [],
    )
```

`make_negotiation_runtime(tmp_path, *, plan_negotiation)` must build a **real** continuous `plan_and_execute` run through the service API with two workers, a scripted lead client that returns a two-task plan, one scripted client per worker, and a counter of worker execution invocations. Model it on the existing runtime fixtures in that file. Do not pre-create a plan revision in the fixture — a fixture that creates the row the production code is supposed to create is how a missing implementation passed review in this repo before.

- [ ] **Step 3: Run them and watch them fail**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly -k "negotiation or approval or objection or unparsable_review or terminal_approver"`
Expected: FAIL — `_negotiate_plan` does not exist and `list_plan_revisions` returns nothing.

- [ ] **Step 4: Write the reviewer prompt**

Add next to the other prompts in `team_runtime.py`. It is a plain string used with `.format()`, so double every literal brace:

```python
PLAN_REVIEW_PROMPT = """You are {agent_label} in a personal-agent-gateway Team Run.

The leader proposed the task plan below. Review it before any work starts.
You own the tasks marked YOURS.

Goal: {goal}

Plan:
{plan_block}

Report only these four kinds of problem:
- overlap: two tasks would do the same work or write the same file
- gap: the goal needs work that no task covers
- dependency_conflict: a task assumes something another task has not produced yet
- scope: a task assigned to you is not something you can carry out

Do not object to wording, ordering, or style. If the plan is workable, approve it.

The final response must contain only this JSON object and no prose or code fences:
{{"decision":"approve|object","objections":[{{"kind":"overlap|gap|dependency_conflict|scope","task_ref":"T-01","detail":"what is wrong"}}]}}
Use "objections":[] when you approve. Every objection needs a task_ref from the
plan above and a concrete detail the leader can act on."""
```

The three keys in the schema line must be exactly `kind`, `task_ref` and `detail` — `parse_plan_review` pins the key set, so any other spelling makes every review a parse failure.

`plan_block` is built by the caller as one line per task: `f"{task_label(task.plan_ordinal)} [{owner}] {task.title} — {task.description}"`, with `owner` rendered as `YOURS` for the reviewer's own tasks and the agent's name otherwise. Task IDs never appear.

Then confirm the prompt still formats:

Run: `PYTHONPATH=src python -c "from personal_agent_gateway.team_runtime import PLAN_REVIEW_PROMPT; print(PLAN_REVIEW_PROMPT.format(agent_label='a worker', goal='g', plan_block='T-01 x')[-160:])"`
Expected: prints the tail without raising.

- [ ] **Step 5: Implement `_negotiate_plan`**

```python
    async def _negotiate_plan(
        self,
        run: TeamRun,
        leader: TeamAgent,
        workers: list[TeamAgent],
        cycle_id: str,
    ) -> bool:
        """Hold the plan until its owners agree, or end the run without executing.

        Returns True when execution may proceed. Returns False only after
        ``_abandon_negotiation`` has already settled the run, so the caller
        returns immediately instead of deciding the terminal status a second time.
        """
        while True:
            tasks = self._teams.list_tasks(run.id, cycle_id)
            # Resume continues the open revision. Creating a second one here is
            # how a restart would refresh the budget.
            revision = self._teams.get_active_plan_revision(run.id, cycle_id)
            if revision is None:
                approvers = [
                    worker.id
                    for worker in _find_workers(self._teams.list_agents(run.id))
                    if worker.status not in {"failed", "canceled"}
                ]
                if not approvers:
                    return True
                revision = self._teams.create_plan_revision(
                    run.id, cycle_id, [task.id for task in tasks], approvers
                )
            if revision is None:
                await self._abandon_negotiation(run, cycle_id, tasks)
                return False

            labels = {
                _plan_label(task): task for task in tasks
            }
            self._teams.append_message(
                run.id,
                leader.id,
                None,
                "plan_proposed",
                f"Plan revision {revision.revision} with {len(tasks)} tasks.",
                {"revision": revision.revision, "labels": sorted(labels)},
                cycle_id=cycle_id,
            )

            reviewed = self._teams.plan_reviews(revision.id)
            for agent_id in revision.required_approver_agent_ids:
                if agent_id in reviewed:
                    continue  # already answered; re-asking can flip an approval
                review = await self._review_plan(
                    run, revision, agent_id, tasks, frozenset(labels), cycle_id
                )
                if review is None:
                    break  # unparsable after repair: not consent, stop asking
                self._teams.record_plan_review(
                    revision.id,
                    agent_id,
                    review.decision,
                    [asdict(objection) for objection in review.objections],
                )
                self._teams.append_message(
                    run.id, agent_id, leader.id, "plan_reviewed",
                    review.decision,
                    {"revision": revision.revision,
                     "objections": [asdict(o) for o in review.objections]},
                    cycle_id=cycle_id,
                )
                if review.decision == "object":
                    break  # the revision is already dead

            verdict = verdict_for(
                revision.required_approver_agent_ids,
                self._teams.plan_reviews(revision.id),
            )
            if verdict == "approved":
                self._teams.set_plan_revision_status(revision.id, "approved")
                return True

            # "waiting" reaches here only when a review would not parse, which is
            # not consent -- so it is treated the same as an objection.
            self._teams.set_plan_revision_status(revision.id, "superseded")
            for task in tasks:
                if task.status == "pending":
                    self._teams.set_task_status(task.id, "canceled")
            await self._replan_after_objections(run, leader, revision, cycle_id)
```

Two helpers this needs, both small:

- `_plan_label(task)` returns `task_label(task.plan_ordinal)` from Task 2's module.
- `_review_plan(run, revision, agent_id, tasks, labels, cycle_id) -> PlanReview | None` builds the prompt, calls `self._invoke_with_repair` with `_operation_spec(run, cycle_id, agent, "plan_review", revision.revision, messages, task_id=revision.id)`, and parses with `parse_plan_review(response.content, labels)`. The revision goes in the `task_id` slot so the same agent reviewing revision 2 and revision 3 are distinct operations. It returns `None` when the repair path still cannot parse.
- `_replan_after_objections(run, leader, revision, cycle_id)` calls the existing ledger planning path (`_invoke_plan_with_repair` with stage `cycle_planning`) with the objection details appended to the leader's messages, then creates the new tasks the same way `_plan` does. Read `_plan` (`team_runtime.py:1685`) and reuse its task-creation loop rather than writing a second one; if that means extracting the loop into a helper both call, do that and say so in your report.

Import `asdict` from `dataclasses` and `verdict_for`, `task_label`, `parse_plan_review`, `PlanReview` from `personal_agent_gateway.team_plan_negotiation`.

`_abandon_negotiation` settles the run explicitly — the derived status would be `failed`, and the spec requires `completed_with_failures`:

```python
    async def _abandon_negotiation(
        self,
        run: TeamRun,
        cycle_id: str,
        tasks: list[TeamTask],
    ) -> None:
        """End the run without executing an unapproved plan.

        The status is set, not derived: cycle_execution_disposition would call a
        run whose required tasks are canceled `failed`, and the design requires
        completed_with_failures with a reason the operator can act on.
        """
        for task in tasks:
            if task.status == "pending":
                self._teams.set_task_status(task.id, "canceled")
        active = self._teams.get_active_plan_revision(run.id, cycle_id)
        if active is not None:
            self._teams.set_plan_revision_status(active.id, "abandoned")
        self._teams.set_cycle_status(cycle_id, "completed_with_failures")
        self._teams.set_run_status(
            run.id,
            "completed_with_failures",
            error_message="collaboration_plan_approval_incomplete",
        )
        await self._publish(
            {"type": "team.run.completed", "team_run_id": run.id}
        )
```

`set_cycle_status` (`teams.py:1264`) accepts `completed_with_failures` — verified, it is in the set that stamps `finished_at`. `Database` exposes `execute`, `fetchone` and `fetchall` (`db.py:459-471`), which is the API the persistence in Task 4 uses. Read `_settle_failed` (`team_runtime.py:3919`) for how this codebase already settles a run and match its shape.

- [ ] **Step 6: Wire it into `start()`**

Between the `run_mode` check and the `running` transition (`team_runtime.py:1641`-`1668`), after `workers` is computed:

```python
            if run.plan_negotiation_enabled and cycle_id is not None:
                if not await self._negotiate_plan(run, leader, workers, cycle_id):
                    return self._teams.get_team_run(run.id)
                run = self._teams.get_team_run(run.id)
```

The `cycle_id is not None` guard is deliberate: the cycle-less planning branch bypasses the operation ledger, so negotiation there would not survive a restart.

- [ ] **Step 7: Run the tests and watch them pass**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py tests/test_team_repair_stages.py -q -p no:randomly`
Expected: all pass. This file takes several minutes; let it finish.

- [ ] **Step 8: Lint and commit**

```bash
python -m ruff check src/personal_agent_gateway/team_runtime.py src/personal_agent_gateway/team_model_operations.py src/personal_agent_gateway/team_repair_stages.py tests/test_team_runtime.py
git add src/personal_agent_gateway/team_runtime.py src/personal_agent_gateway/team_model_operations.py src/personal_agent_gateway/team_repair_stages.py tests/test_team_runtime.py tests/test_team_repair_stages.py
git commit -m "feat(team-runs): hold execution until the plan's owners approve it"
```

---

## Task 6: Restart, and the status that must not be re-derived

Two things the spec demands proof of, neither of which Task 5's tests cover.

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py` if a defect is found
- Test: `tests/test_team_runtime.py`

**Interfaces:**
- Consumes: `_negotiate_plan` from Task 5.

- [ ] **Step 1: Write the failing tests**

```python
async def test_the_cap_survives_a_restart(tmp_path) -> None:
    """The two loop defects already fixed here both resumed from stored state
    that the cap check trusted. Interrupt mid-negotiation and confirm the budget
    is not refreshed."""
    setup = make_negotiation_runtime(tmp_path, plan_negotiation=True)
    setup.worker_clients[0].responses = [_object("T-01", "gap")]
    setup.worker_clients[1].responses = [_approve()]

    await setup.runtime.start(setup.run.id, setup.cycle.id)

    # A second runtime over the same database, as a restart would produce.
    resumed = setup.new_runtime()
    setup.worker_clients[0].responses = [_object("T-01", "gap")] * 5
    setup.worker_clients[1].responses = [_approve()] * 5

    run = await resumed.resume(setup.run.id, setup.cycle.id)

    revisions = setup.teams.list_plan_revisions(setup.run.id)
    assert len(revisions) <= 3
    assert run.status == "completed_with_failures"


async def test_an_already_reviewed_agent_is_not_asked_again_after_a_restart(tmp_path) -> None:
    """Re-asking spends a model call and can flip a recorded approval."""
    setup = make_negotiation_runtime(tmp_path, plan_negotiation=True)
    setup.worker_clients[0].responses = [_approve()]
    setup.worker_clients[1].responses = []  # dies before reviewing

    with contextlib.suppress(Exception):
        await setup.runtime.start(setup.run.id, setup.cycle.id)

    calls_before = setup.worker_clients[0].call_count
    resumed = setup.new_runtime()
    setup.worker_clients[1].responses = [_approve()]

    await resumed.resume(setup.run.id, setup.cycle.id)

    assert setup.worker_clients[0].call_count == calls_before


async def test_a_failed_negotiation_stays_completed_with_failures(tmp_path) -> None:
    """The derived rule says a run whose required tasks are canceled is `failed`.
    The explicit status must not be overwritten by a later re-derivation."""
    setup = make_negotiation_runtime(tmp_path, plan_negotiation=True)
    setup.worker_clients[0].responses = [_object("T-01", "gap")] * 3
    setup.worker_clients[1].responses = [_approve()] * 3

    await setup.runtime.start(setup.run.id, setup.cycle.id)
    resumed = setup.new_runtime()
    await resumed.resume(setup.run.id, setup.cycle.id)

    run = setup.teams.get_team_run(setup.run.id)
    assert run.status == "completed_with_failures"
    assert run.error_message == "collaboration_plan_approval_incomplete"
```

`setup.new_runtime()` builds a second `TeamRuntime` over the same service and database. Add it to the fixture from Task 5. `call_count` requires the scripted client to count invocations — extend the fixture's client, and if the existing scripted client in that file already counts calls, use its attribute name instead of adding a second one.

- [ ] **Step 2: Run them and watch them fail or pass, and report which**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly -k "survives_a_restart or already_reviewed_agent or stays_completed_with_failures"`

These three tests are the point of this task and they may legitimately pass on the first run if Task 5's implementation already handles resume. **Report exactly which passed and which failed.** Do not adjust a test so it fails; if all three pass, say so and record in your report that the resume behaviour was already correct.

- [ ] **Step 3: Fix whatever failed**

If `resume` re-enters negotiation without honouring stored revisions, the fix belongs in `_negotiate_plan`: it must read the existing `awaiting_approval` revision through `get_active_plan_revision` and continue it rather than creating a new one. Only create a revision when there is no active one.

If an already-reviewed agent is asked again, the fix is to skip approvers already present in `plan_reviews(revision.id)`.

If the terminal status is overwritten, trace which caller re-derives it and guard that path — and say in your report which caller it was, because the spec names this as the risk to prove.

- [ ] **Step 4: Run them and watch them pass**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "test(team-runs): pin negotiation behaviour across a restart"
```

---

## Task 7: API and the screen

**Files:**
- Modify: `src/personal_agent_gateway/api/team_runs.py` — the create-run request model and handler; the `/detail` payload at line 496-538
- Modify: `frontend/src/api/client.js:615` area — forward the new field
- Create: `frontend/src/components/organisms/TeamRunDetail/PlanNegotiation.jsx`
- Modify: `frontend/src/components/organisms/TeamRunDetail/index.jsx`
- Test: `tests/test_api_team_runs.py`, `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`, `frontend/src/api/client.test.js`

**Interfaces:**
- Consumes: `list_plan_revisions`, `plan_review_objections` from Task 4.
- Produces: `POST /api/team-runs` accepts `plan_negotiation: bool = False`; `/detail` gains `plan_revisions: [{revision, status, required_approver_agent_ids, reviews: {agent_id: decision}, objections: {agent_id: [...]}}]`; the mapper forwards it as `planRevisions`.

- [ ] **Step 1: Write the failing backend test**

```python
def test_detail_reports_why_nothing_ran(client) -> None:
    """When negotiation ends a run, the objections are the only explanation the
    operator gets, so /detail must carry them in full."""
    run = _create_run(client, plan_negotiation=True)
    ...  # drive or seed one superseded revision with an objection

    detail = client.get(f"/api/team-runs/{run['id']}/detail").json()

    (revision,) = detail["plan_revisions"]
    assert revision["status"] == "superseded"
    assert revision["objections"] != {}
```

Read `tests/test_api_team_runs.py` for how it creates a run and seeds team state; follow the same approach rather than calling the service directly if the file's other tests go through the API.

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=src python -m pytest tests/test_api_team_runs.py -q -p no:randomly -k "why_nothing_ran"`
Expected: FAIL — `KeyError: 'plan_revisions'`.

- [ ] **Step 3: Add the field to create and to `/detail`**

In the create request model add `plan_negotiation: bool = False` and pass it to `create_team_run`. In the `/detail` return dict, beside `"contests"`:

```python
        "plan_revisions": [
            {
                "revision": revision.revision,
                "status": revision.status,
                "required_approver_agent_ids": list(
                    revision.required_approver_agent_ids
                ),
                "reviews": service.plan_reviews(revision.id),
                "objections": service.plan_review_objections(revision.id),
            }
            for revision in service.list_plan_revisions(team_run_id)
        ],
```

`tests/test_api_team_runs.py:2559` asserts the exact `build_evidence_summary` dict; this adds a sibling key at the top level, so check whether any test asserts the exact top-level key set of `/detail` and extend it rather than removing the field.

- [ ] **Step 4: Forward it through the mapper**

`frontend/src/api/client.js` rebuilds the response field by field — a top-level field it does not name is invisible to the UI. Beside `contests`:

```js
        planRevisions: body?.plan_revisions || [],
```

Add to `frontend/src/api/client.test.js`'s existing `teamRunDetail` expectation, which compares the whole mapped object.

- [ ] **Step 5: Write the failing frontend test**

```jsx
  it("explains that a plan was never approved", async () => {
    render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "G", status: "completed_with_failures", run_mode: "plan_and_execute" },
          agents: [{ id: "w1", name: "Worker One", role: "member", status: "pending", current_task_id: null }],
          messages: [], tasks: [],
          planRevisions: [{
            revision: 1, status: "abandoned",
            required_approver_agent_ids: ["w1"],
            reviews: { w1: "object" },
            objections: { w1: [{ kind: "gap", task_ref: "T-01", detail: "마이그레이션 담당 없음" }] }
          }]
        }}
      />
    );

    expect(screen.getByText(/합의 실패/)).toBeInTheDocument();
    expect(screen.getByText(/마이그레이션 담당 없음/)).toBeInTheDocument();
  });
```

Run: `npm --prefix frontend test -- TeamRunDetail`
Expected: FAIL — neither string renders.

- [ ] **Step 6: Render it**

`PlanNegotiation.jsx` shows, per revision: `개정 N`, the status in Korean (`awaiting_approval` → `승인 대기`, `approved` → `승인됨`, `superseded` → `대체됨`, `abandoned` → `합의 실패`), who must approve, who has, and **every objection's `detail` verbatim** — this is the only place the operator learns why nothing ran, so do not truncate or summarise it. Render nothing when `planRevisions` is empty, so a legacy run's screen is unchanged.

Mount it in `index.jsx` near `<ContestPanel />`. Keep the two separate: a contest is the user's objection to the plan, this is the workers'.

- [ ] **Step 7: Run both suites**

Run: `PYTHONPATH=src python -m pytest tests/test_api_team_runs.py -q -p no:randomly`
Run: `npm --prefix frontend test`
Expected: backend all pass; frontend 41 files with 3 more tests than the 401 baseline, 0 failures.

- [ ] **Step 8: Commit**

```bash
git add src/personal_agent_gateway/api/team_runs.py tests/test_api_team_runs.py frontend/src
git commit -m "feat(team-runs): show which revision the workers refused, and why"
```

---

## Task 8: Verify the whole change

- [ ] **Step 1: Full backend suite**

Run: `PYTHONPATH=src python -m pytest -q -p no:randomly`
Expected: **0 failed.** The baseline is `1575 passed / 4 skipped` plus this plan's new tests. Report the exact numbers and any failure in full — the baseline is green, so a failure is yours.

- [ ] **Step 2: Lint**

Run: `python -m ruff check src/personal_agent_gateway/ tests/`
Expected: `All checks passed!`

- [ ] **Step 3: Answer the three questions the spec left open**

Run a negotiation-failed run and record what actually happens, rather than reasoning about it:

```bash
PYTHONPATH=src python -c "
import json, sqlite3
from pathlib import Path
from personal_agent_gateway.teams import _team_task_from_row
from personal_agent_gateway.team_build_evidence import task_build_evidence, run_build_evidence
c = sqlite3.connect('data/app.sqlite'); c.row_factory = sqlite3.Row
# pick the run your test or live check produced, then:
rows = c.execute('select * from team_tasks where team_run_id=?', (RUN_ID,)).fetchall()
tasks = [_team_task_from_row(r) for r in rows]
ev = [task_build_evidence(t, Path('.')) for t in tasks]
print(json.dumps(run_build_evidence(ev), ensure_ascii=False))
"
```

Record in your report:
1. What `run_build_evidence` reports for a run whose tasks are all `canceled` — especially whether `unverified_task_count` and `missing_file_count` read sensibly.
2. What `_package_results` produces for a run with no executed task.
3. What the task list and phase stepper show on that run's screen.

If any of the three is misleading to an operator, say so plainly and propose the smallest fix; do not fix it inside this task without saying you did.

- [ ] **Step 4: Live check**

```bash
npm run stop && npm start
```

Create a run with `plan_negotiation: true` and confirm the screen shows the revision and, if the workers objected, their objections. `/api/team-runs/{id}/detail` answers `401 OTP login required` from the CLI, so verify through the browser or by rebuilding the payload from `data/app.sqlite` through the same `list_plan_revisions` call the endpoint makes. Say plainly whichever you did, and say what you could not arrange.

- [ ] **Step 5: Record and finish**

Append what you observed to the spec's verification section, commit that file alone, then use `superpowers:finishing-a-development-branch`.

---

## Deliberately not in this plan

- **Independent exploration, peer review, final approval.** Each is its own spec under the ADR's Stage 1.
- **Parallel execution, radio-lite, passive watchers.** Stages 3 and 4.
- **The ADR's `collaboration_mode` enum.** Negotiation is not exclusive with radio-lite, so a single enum would encode an exclusivity that does not exist. Stage 2 introduces it when there is a mode to display.
- **A user path into the negotiation.** The operator sees the outcome, not a seat at the table. Contest remains the user's channel.
- **Judging replan quality.** Whether the leader actually incorporates objections is what Stage 0's fixture measures; this plan only guarantees the objection text reaches it.
