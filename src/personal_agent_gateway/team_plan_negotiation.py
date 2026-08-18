"""What negotiation decides, with nothing to mock.

The cap and the unanimity rule are the two places this feature is correct or
wrong, so they live here rather than inside the runtime: a test can enumerate
every combination without a database, a model client, or an event loop.
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
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
    if isinstance(required_approver_ids, str):
        raise TypeError("required_approver_ids must be a collection of ids, not a string")
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

    Callers must obtain every revision number from here; both loop defects
    previously fixed in this repo were a cap that one path checked and another
    did not, so this function also refuses to hand out a budget for a stored
    revision that is already past the cap rather than assuming it cannot
    happen.
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


# The first four ask whether the plan can be carried out. `unverified_premise`
# asks something the others cannot: whether the plan is about to build on a claim
# nobody checked. Added after a measured sweep where the reviewer approved every
# plan and the resulting answers confidently described behaviour the code does
# not have -- the goal had asserted it, no task verified it, and none of overlap,
# gap, dependency_conflict or scope can say so.
OBJECTION_KINDS = frozenset(
    {"overlap", "gap", "dependency_conflict", "scope", "unverified_premise"}
)


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
    if not isinstance(decision, str) or decision not in {"approve", "object"}:
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
    if not isinstance(kind, str) or kind not in OBJECTION_KINDS:
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


DISCARDED_REVISION_STATUSES = frozenset({"superseded", "abandoned"})


def discarded_task_ids(
    revisions: Iterable[tuple[str, Iterable[str]]],
) -> frozenset[str]:
    """Task ids that only a discarded plan revision ever proposed.

    A superseded revision leaves its tasks behind as canceled rows, and a
    canceled *required* task reads as terminal ``failed`` -- so a negotiation
    that worked reported the run as failed, because the plan nobody agreed to
    was still being counted.

    Two exclusions are load bearing. A task a surviving revision also lists is
    not discarded, and work added to the cycle afterwards sits on no revision
    at all, so both keep deciding the outcome.

    Takes ``(status, task_ids)`` pairs rather than rows or dataclasses because
    three layers apply this rule to three different shapes -- the runtime to
    ``TeamTask`` objects, the effect service to SQL rows, and the read model to
    per-task reports. The rule lives here once; each layer feeds it its own
    shape.
    """
    discarded: set[str] = set()
    live: set[str] = set()
    for status, task_ids in revisions:
        target = discarded if status in DISCARDED_REVISION_STATUSES else live
        target.update(task_ids)
    return frozenset(discarded - live)
