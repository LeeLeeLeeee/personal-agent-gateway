import pytest

from personal_agent_gateway.team_collaboration_service import (
    TeamCollaborationService,
    UnknownRecipient,
)
from personal_agent_gateway.team_model_operations import OperationSpec
from personal_agent_gateway.team_outcomes import Mention
from tests.test_team_runtime import make_negotiation_runtime


@pytest.fixture
def setup(tmp_path):
    built = make_negotiation_runtime(tmp_path, plan_negotiation=False)
    built.collab = TeamCollaborationService(built.db, built.teams)
    return built


def test_labels_cover_the_leader_and_every_worker(setup):
    labels = setup.collab.labels_for_run(setup.run.id)

    assert set(labels) == {"LEAD", "W-01", "W-02"}
    assert labels["W-01"] == setup.workers[0].id


def test_a_mention_is_stored_as_a_message_to_that_agent(setup):
    (message_id,) = setup.collab.record_mentions(
        setup.run.id, None, setup.workers[0].id, [Mention("W-02", "확인 필요")]
    )

    stored = next(
        m for m in setup.teams.list_messages(setup.run.id) if m.id == message_id
    )
    assert stored.sender_agent_id == setup.workers[0].id
    assert stored.recipient_agent_id == setup.workers[1].id
    assert stored.kind == "peer_mention"
    assert stored.content == "확인 필요"


def test_an_unknown_label_is_refused(setup):
    """조용히 버리면 보낸 쪽은 전달됐다고 믿고, 그 믿음은 어디에도 없다."""
    with pytest.raises(UnknownRecipient, match="unknown mention recipient label"):
        setup.collab.record_mentions(
            setup.run.id, None, setup.workers[0].id, [Mention("W-09", "x")]
        )


def test_a_mention_to_yourself_is_refused(setup):
    """자기 라벨은 해석에 성공하므로 '알 수 없다'는 말은 거짓이다."""
    with pytest.raises(UnknownRecipient, match="cannot be addressed to yourself"):
        setup.collab.record_mentions(
            setup.run.id, None, setup.workers[0].id, [Mention("W-01", "x")]
        )


def test_a_refused_batch_stores_nothing(setup):
    """검사와 저장이 한 루프에 섞이면 첫 쪽지가 영구히 남은 채로 예외가 올라간다.

    두 번째 라벨이 잘못된 배치가 첫 쪽지를 저장해버리면, 보낸 쪽은 실패를
    전달받고도 실제로는 절반이 갔다는 사실을 알 방법이 없다. 메시지 테이블에
    아무 것도 없다는 것까지 확인해야 이 회귀를 잡는다 -- 예외만 확인하면
    버그가 있어도 통과한다.
    """
    with pytest.raises(UnknownRecipient):
        setup.collab.record_mentions(
            setup.run.id,
            None,
            setup.workers[0].id,
            [Mention("W-02", "ok"), Mention("W-99", "bad")],
        )

    stored = [
        m for m in setup.teams.list_messages(setup.run.id) if m.kind == "peer_mention"
    ]
    assert stored == []


def _reserved_operation(setup, agent) -> str:
    """이 에이전트에 대해 예약된 operation의 key.

    OperationSpec의 필수 필드를 채워 reserve를 부른다. request_digest는 64자
    hex여야 한다(team_model_operations.py:633의 _validate_request_digest).
    """
    key = f"test:{agent.id}:{len(setup.collab.labels_for_run(setup.run.id))}"
    setup.operations.reserve(
        OperationSpec(
            operation_key=key,
            team_run_id=setup.run.id,
            cycle_id=setup.cycle.id,
            task_id=None,
            agent_id=agent.id,
            provider=agent.backend,
            stage="cycle_planning",
            stage_ordinal=0,
            request_digest="0" * 64,
        )
    )
    return key


def _mark_operation_applied(setup, operation_key: str) -> None:
    with setup.db.connection() as connection:
        connection.execute(
            "update team_model_operations set status = 'applied'"
            " where operation_key = ?",
            (operation_key,),
        )


def test_undelivered_excludes_only_applied_deliveries(setup):
    """전달 완료의 판정 근거는 원장이다: 그 operation이 applied인가."""
    (first,) = setup.collab.record_mentions(
        setup.run.id, None, setup.workers[0].id, [Mention("W-02", "one")]
    )
    (second,) = setup.collab.record_mentions(
        setup.run.id, None, setup.workers[0].id, [Mention("W-02", "two")]
    )
    key = _reserved_operation(setup, setup.workers[1])

    setup.collab.open_delivery(setup.run.id, setup.workers[1].id, key, [first])
    assert [n[0] for n in setup.collab.undelivered(setup.run.id, setup.workers[1].id)] == [
        first,
        second,
    ]

    _mark_operation_applied(setup, key)
    assert [n[0] for n in setup.collab.undelivered(setup.run.id, setup.workers[1].id)] == [
        second
    ]


def test_reopening_the_same_operation_returns_the_same_items(setup):
    """복구가 다시 조회하면 그 사이 온 쪽지가 섞여 프롬프트가 달라진다."""
    (first,) = setup.collab.record_mentions(
        setup.run.id, None, setup.workers[0].id, [Mention("W-02", "one")]
    )
    delivery = setup.collab.open_delivery(
        setup.run.id, setup.workers[1].id, "k-2", [first]
    )

    (late,) = setup.collab.record_mentions(
        setup.run.id, None, setup.workers[0].id, [Mention("W-02", "late")]
    )
    again = setup.collab.open_delivery(
        setup.run.id, setup.workers[1].id, "k-2", [first, late]
    )

    assert again == delivery
    assert setup.collab.delivery_message_ids("k-2") == (first,)


def test_notes_by_id_matches_the_shape_of_undelivered(setup):
    (first,) = setup.collab.record_mentions(
        setup.run.id, None, setup.workers[0].id, [Mention("W-02", "note")]
    )

    assert setup.collab.notes_by_id(setup.run.id, [first]) == (
        (first, "W-01", "note"),
    )


def test_a_delivery_whose_operation_never_applied_leaves_the_notes_pending(setup):
    """유실 0을 주장하려면 못 전한 쪽지가 여전히 미전달로 보여야 한다."""
    (first,) = setup.collab.record_mentions(
        setup.run.id, None, setup.workers[0].id, [Mention("W-02", "one")]
    )
    key = _reserved_operation(setup, setup.workers[1])
    setup.collab.open_delivery(setup.run.id, setup.workers[1].id, key, [first])

    # operation은 예약만 됐고 applied가 아니다.
    assert [n[0] for n in setup.collab.undelivered(setup.run.id, setup.workers[1].id)] == [
        first
    ]
    assert setup.collab.undelivered_count(setup.run.id) == 1


def test_a_delivery_with_no_operation_at_all_leaves_the_notes_pending(setup):
    """조인이 비면 미전달로 남아야 한다. 안 그러면 고아 배달이 쪽지를 삼킨다."""
    (first,) = setup.collab.record_mentions(
        setup.run.id, None, setup.workers[0].id, [Mention("W-02", "one")]
    )
    setup.collab.open_delivery(setup.run.id, setup.workers[1].id, "no-such-key", [first])

    assert setup.collab.undelivered_count(setup.run.id) == 1
