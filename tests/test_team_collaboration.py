import pytest

from personal_agent_gateway.team_collaboration import (
    MENTION_BATCH_LIMIT,
    MENTION_TEXT_LIMIT,
    agent_label,
    radio_block,
    roster_block,
)


def test_labels_are_stable_and_short():
    """UUID를 모델에게 되받아 적게 하면 지어낸다."""
    assert agent_label("leader", None) == "LEAD"
    assert agent_label("member", 1) == "W-01"
    assert agent_label("member", 12) == "W-12"


def test_a_worker_label_without_an_ordinal_is_a_bug_not_a_default():
    with pytest.raises(ValueError):
        agent_label("member", None)


def test_roster_block_names_every_teammate():
    block = roster_block([("LEAD", "설계 리드"), ("W-02", "구현 담당")])

    assert "LEAD" in block and "설계 리드" in block
    assert "W-02" in block and "구현 담당" in block


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
