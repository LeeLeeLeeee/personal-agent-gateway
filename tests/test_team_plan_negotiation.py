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


def test_a_bare_string_of_ids_is_refused():
    """str satisfies Sequence[str], so "ab" would iterate as two approvers named
    "a" and "b" -- an approval nobody gave."""
    with pytest.raises(TypeError):
        verdict_for("ab", {"a": "approve", "b": "approve"})


def test_an_empty_required_set_is_not_silently_approved():
    """No approvers means the caller computed the set wrongly. Returning
    'approved' here would execute an unreviewed plan."""
    with pytest.raises(ValueError):
        verdict_for([], {})


def test_a_review_from_someone_who_was_not_asked_is_ignored():
    assert verdict_for(["a"], {"a": "approve", "stranger": "object"}) == "approved"


@pytest.mark.parametrize("value", ["aprove", "Approve", "objected", "", None])
def test_a_review_value_that_is_not_the_exact_sentinel_is_not_consent(value):
    assert verdict_for(["a", "b"], {"a": "approve", "b": value}) == "waiting"


@pytest.mark.parametrize(
    ("current", "expected"),
    [(0, 1), (1, 2), (2, 3), (3, None)],
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
