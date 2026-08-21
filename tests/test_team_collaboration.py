import pytest

from personal_agent_gateway.team_collaboration import (
    MENTION_BATCH_LIMIT,
    MENTION_TEXT_LIMIT,
    agent_label,
    radio_block,
    roster_block,
)
from personal_agent_gateway import team_runtime
from personal_agent_gateway.team_runtime import PLANNING_PROMPT


def test_labels_are_stable_and_short():
    """UUID를 모델에게 되받아 적게 하면 지어낸다."""
    assert agent_label("leader", None) == "LEAD"
    assert agent_label("member", 1) == "W-01"
    assert agent_label("member", 12) == "W-12"


def test_a_worker_label_without_an_ordinal_is_a_bug_not_a_default():
    with pytest.raises(ValueError):
        agent_label("member", None)


def test_roster_block_names_every_teammate():
    block = roster_block([("LEAD", "설계 리드", ""), ("W-02", "구현 담당", "")])

    assert "LEAD" in block and "설계 리드" in block
    assert "W-02" in block and "구현 담당" in block


def test_the_roster_scopes_its_labels_away_from_owner_agent_id():
    """The prefix sits immediately above `Available team members`, which shows
    raw UUIDs and `owner_agent_id`. Without saying where a label is used, a
    leader writes `owner_agent_id: "W-01"`, `_parse_task_plan` raises, and a
    planning repair round is burned -- the same wasted-round failure class the
    roster wording was filed against."""
    block = roster_block([("LEAD", "설계 리드", ""), ("W-01", "구현 담당", "")])

    assert "owner_agent_id" in block
    assert "note" in block


def test_only_the_worker_outcome_contract_tells_a_model_to_send_mentions():
    """The prefix is prepended at every stage, not just planning, but only the
    worker sends notes -- _parse_task_plan rejects any unknown key per task, so
    a hint to use "mentions" in a leader prompt earns a repair round. Pinned
    over every prompt this module defines rather than one of them, so a prompt
    added later is covered without anyone remembering to add it here."""
    prompts = {
        name: value
        for name, value in vars(team_runtime).items()
        if name.endswith("_PROMPT") and isinstance(value, str)
    }
    # Guards the loop against a rename that would leave it iterating nothing.
    assert PLANNING_PROMPT in prompts.values()
    assert len(prompts) >= 8
    assert '"mentions"' in prompts.pop("WORKER_PROMPT")

    roster = roster_block([("LEAD", "설계 리드", ""), ("W-01", "구현 담당", "")])
    for name, prompt in prompts.items():
        assert "mentions" not in (roster + prompt).lower(), name


def test_radio_block_marks_the_content_as_untrusted_reference():
    """쪽지는 다른 모델이 쓴 글이다. 블록은 그것이 지시가 아니라고 말해야 한다."""
    block = radio_block([("W-01", "acceptance는 파일만 읽는다")])

    assert "W-01" in block
    assert "acceptance는 파일만 읽는다" in block
    assert "not instructions" in block.lower()


def test_no_notes_renders_nothing():
    """빈 블록을 붙이면 프롬프트가 매 호출 달라지고 operation 지문도 흔들린다."""
    assert radio_block([]) == ""
    assert roster_block([]) == ""


def test_a_long_note_is_truncated_and_says_so():
    block = radio_block([("W-01", "가" * (MENTION_TEXT_LIMIT + 500))])

    assert len(block) < MENTION_TEXT_LIMIT + 400
    assert "truncated" in block.lower()


def test_more_notes_than_the_batch_limit_are_capped_and_counted():
    notes = [("W-01", f"item{index}") for index in range(MENTION_BATCH_LIMIT + 5)]

    block = radio_block(notes)

    assert block.count("item") == MENTION_BATCH_LIMIT
    assert "5 more notes withheld" in block


def test_a_flood_of_notes_cannot_push_the_assignment_out():
    """상한이 없으면 긴 글로 원래 지시를 밀어낼 수 있다."""
    flood = [("W-02", "가" * 5000) for _ in range(50)]

    block = radio_block(flood)

    assert len(block) < MENTION_TEXT_LIMIT * MENTION_BATCH_LIMIT + 1000
    # 상한 없는 크기 검사만으로는 부족하다: 캡이 실제로 걸렸다는 것도
    # 직접 보여야 한다 -- 50개 중 10개만 실리고, 나머지는 개수로 남는다.
    assert block.count("from W-02:") == MENTION_BATCH_LIMIT
    assert "40 more notes withheld" in block


def test_the_roster_says_what_a_teammate_is_working_on():
    """A label and a name give a worker nobody to write to. Every note in the
    first two-worker sweep went to the lead -- five of five -- because a worker
    that knows only who exists cannot tell which teammate needs a fact it just
    found. The assignment is what turns a name into an address.
    """
    block = roster_block([
        ("LEAD", "설계 리드", ""),
        ("W-02", "구현 담당", "중복 허용 목록과 프롬프트 갱신"),
    ])

    assert "중복 허용 목록과 프롬프트 갱신" in block


def test_a_teammate_with_nothing_assigned_still_appears():
    """The lead owns no task, and a worker whose task has not been planned yet
    owns none either. Dropping them would remove the only recipient a worker
    has before the plan is split."""
    block = roster_block([("LEAD", "설계 리드", ""), ("W-01", "구현 담당", "")])

    assert "LEAD" in block and "W-01" in block


def test_the_roster_does_not_read_as_an_instruction_to_do_a_peers_work():
    """Naming a peer's assignment puts another task in this worker's prompt. A
    worker that treats it as work reaches outside its own contract, and the
    acceptance gate refuses deliverables it did not declare."""
    block = roster_block([("W-02", "구현 담당", "중복 허용 목록 갱신")])

    assert "yours" in block.lower() or "your own" in block.lower()

