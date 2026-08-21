import json
import re

import pytest

from personal_agent_gateway.team_plan_negotiation import (
    OBJECTION_KINDS,
    PLAN_NEGOTIATION_MAX_REVISIONS,
    PlanReviewError,
    next_revision,
    parse_plan_review,
    task_label,
    verdict_for,
    discarded_task_ids,
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


def test_an_unverified_premise_objection_is_accepted():
    """The four original kinds all ask whether the plan can be carried out. None
    of them can say the plan is about to build on a claim nobody checked, which
    is what a measured sweep found the reviewer unable to report: it approved
    every plan while the answers went on to describe behaviour the code does not
    have, because the goal had asserted it and no task verified it."""
    review = parse_plan_review(
        json.dumps(
            {
                "decision": "object",
                "objections": [
                    {
                        "kind": "unverified_premise",
                        "task_ref": "T-01",
                        "detail": "목표가 단정한 동작을 확인하는 태스크가 없다",
                    }
                ],
            }
        ),
        _LABELS,
    )

    (objection,) = review.objections
    assert objection.kind == "unverified_premise"


def test_the_review_prompt_offers_the_premise_objection():
    """A kind the parser accepts but the prompt never mentions is a kind no
    reviewer will ever send."""
    from personal_agent_gateway.team_runtime import PLAN_REVIEW_PROMPT

    for kind in OBJECTION_KINDS:
        assert kind in PLAN_REVIEW_PROMPT, kind


def test_every_objection_kind_is_allowed_by_the_schema_the_prompt_shows():
    """Describing a kind in prose is not offering it.

    The prompt ends with the exact JSON the reviewer must return, and that
    JSON enumerates the allowed values of "kind". A kind described above but
    missing from that enum reads as "not one of my options", so the reviewer
    never sends it -- which is what happened to unverified_premise. Checking
    only that the string appears somewhere in the prompt cannot catch it,
    because the prose mention satisfies that.
    """
    from personal_agent_gateway.team_runtime import PLAN_REVIEW_PROMPT

    (enum,) = re.findall(r'"kind":"([a-z_|]+)"', PLAN_REVIEW_PROMPT)
    offered = set(enum.split("|"))
    assert offered == set(OBJECTION_KINDS), sorted(set(OBJECTION_KINDS) - offered)


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


@pytest.mark.parametrize(
    "payload",
    [
        {"decision": ["approve"], "objections": []},
        {"decision": {"x": 1}, "objections": []},
        {"decision": "object", "objections": [
            {"kind": ["overlap"], "task_ref": "T-01", "detail": "d"}]},
        {"decision": "object", "objections": [
            {"kind": {"a": 1}, "task_ref": "T-01", "detail": "d"}]},
    ],
)
def test_an_unhashable_field_is_a_parse_error_not_a_crash(payload):
    """`x in some_set` hashes x, so a list or dict here used to escape as
    TypeError. The repair path catches PlanReviewError, not TypeError, so that
    turned a retryable response into a dead run."""
    with pytest.raises(PlanReviewError):
        parse_plan_review(json.dumps(payload), _LABELS)


@pytest.mark.parametrize("top_level", [[], "approve", 1, None])
def test_a_non_object_top_level_value_is_rejected(top_level):
    with pytest.raises(PlanReviewError):
        parse_plan_review(json.dumps(top_level), _LABELS)


def test_only_a_discarded_revisions_own_tasks_are_dropped():
    assert discarded_task_ids(
        [("superseded", ["t1", "t2"]), ("approved", ["t3"])]
    ) == frozenset({"t1", "t2"})


def test_a_task_a_surviving_revision_also_lists_is_kept():
    """A leader that reproposes the same task must not have it dropped, or an
    approved plan loses a task it actually owns."""
    assert discarded_task_ids(
        [("superseded", ["t1", "t2"]), ("approved", ["t2", "t3"])]
    ) == frozenset({"t1"})


def test_work_that_sits_on_no_revision_is_never_discarded():
    """add_work adds tasks to the cycle after the plan is settled. They belong
    to no revision, so nothing here can drop them -- add_work's own failure
    handling depends on them still counting."""
    assert discarded_task_ids([("approved", ["t1"])]) == frozenset()
    assert discarded_task_ids([]) == frozenset()


def test_an_abandoned_revision_discards_like_a_superseded_one():
    assert discarded_task_ids([("abandoned", ["t1"])]) == frozenset({"t1"})


@pytest.mark.parametrize("status", ["awaiting_approval", "approved"])
def test_a_revision_still_in_play_never_discards(status):
    assert discarded_task_ids([(status, ["t1"])]) == frozenset()
