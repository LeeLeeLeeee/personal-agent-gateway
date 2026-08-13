import json

import pytest

from personal_agent_gateway.team_plan_negotiation import (
    PLAN_NEGOTIATION_MAX_REVISIONS,
    PlanReviewError,
    next_revision,
    parse_plan_review,
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
