import pytest

from personal_agent_gateway.team_collaboration_service import (
    TeamCollaborationService,
    UnknownRecipient,
)
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
