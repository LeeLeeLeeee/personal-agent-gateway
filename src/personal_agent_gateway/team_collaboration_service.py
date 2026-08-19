"""쪽지를 저장하고 라벨을 agent로 해석한다."""

from collections.abc import Sequence

from personal_agent_gateway.team_collaboration import agent_label
from personal_agent_gateway.team_outcomes import Mention


class UnknownRecipient(ValueError):
    """라벨이 이 런의 다른 에이전트를 가리키지 않는다.

    조용히 버리면 보낸 쪽은 쪽지가 전달됐다고 믿고, 그 실패는 어디에도 남지
    않는다. 자기 자신에게 보낸 라벨도 여기서 함께 막는다 -- 저장해도 다음
    모델 호출 때 자기 프롬프트에 자기 글이 되돌아올 뿐 아무 효과가 없다.
    """


class TeamCollaborationService:
    def __init__(self, db, teams) -> None:
        self._db = db
        self.teams = teams

    def labels_for_run(self, team_run_id: str) -> dict[str, str]:
        """라벨 → agent_id. 워커 순번은 list_agents의 순서를 따른다."""
        labels: dict[str, str] = {}
        ordinal = 0
        for agent in self.teams.list_agents(team_run_id):
            if agent.role == "leader":
                labels[agent_label("leader", None)] = agent.id
                continue
            ordinal += 1
            labels[agent_label("member", ordinal)] = agent.id
        return labels

    def record_mentions(
        self,
        team_run_id: str,
        cycle_id: str | None,
        sender_agent_id: str,
        mentions: Sequence[Mention],
    ) -> tuple[str, ...]:
        """각 쪽지를 검사하고 메시지로 저장한다.

        라벨이 해석되지 않거나 자기 자신을 가리키면 그 자리에서 멈춘다:
        앞서 저장된 쪽지는 이미 배달된 것이고, 남은 쪽지를 건너뛰는 것은
        보낸 쪽에게 "일부만 갔다"는 사실을 알릴 방법이 없는 것보다 낫다.
        """
        labels = self.labels_for_run(team_run_id)
        stored: list[str] = []
        for mention in mentions:
            recipient = labels.get(mention.to)
            if recipient is None or recipient == sender_agent_id:
                raise UnknownRecipient(f"unknown mention recipient: {mention.to!r}")
            message = self.teams.append_message(
                team_run_id,
                sender_agent_id,
                recipient,
                "peer_mention",
                mention.text,
                {"to_label": mention.to},
                cycle_id=cycle_id,
            )
            stored.append(message.id)
        return tuple(stored)
