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
