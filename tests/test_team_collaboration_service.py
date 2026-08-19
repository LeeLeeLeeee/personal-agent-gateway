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
    with pytest.raises(UnknownRecipient):
        setup.collab.record_mentions(
            setup.run.id, None, setup.workers[0].id, [Mention("W-09", "x")]
        )


def test_a_mention_to_yourself_is_refused(setup):
    with pytest.raises(UnknownRecipient):
        setup.collab.record_mentions(
            setup.run.id, None, setup.workers[0].id, [Mention("W-01", "x")]
        )
