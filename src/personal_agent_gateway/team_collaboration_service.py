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
        """모든 라벨을 먼저 해석하고, 그 다음에만 저장한다.

        `append_message`는 호출마다 자기 연결을 열고 즉시 커밋한다 (다음
        태스크가 다루는 이유로, 여기서 바깥 트랜잭션으로 감싸면 그 연결과
        교착한다). 그래서 검사와 저장을 한 루프에 섞으면 배치 중간의 라벨
        하나가 잘못됐을 때 앞선 쪽지는 이미 영구히 저장된 채로 예외가
        올라가고, 보낸 쪽은 "실패했다"는 말과 "사실 일부는 갔다"는 현실이
        어긋난 채로 남는다 -- 재시도하면 그 쪽지는 중복 저장된다. 두 단계로
        나누면 하나라도 해석에 실패할 때 아무것도 쓰이지 않는다.
        """
        labels = self.labels_for_run(team_run_id)
        recipients: list[str] = []
        for mention in mentions:
            recipient = labels.get(mention.to)
            if recipient is None:
                raise UnknownRecipient(f"unknown mention recipient label: {mention.to!r}")
            if recipient == sender_agent_id:
                raise UnknownRecipient(
                    f"a note cannot be addressed to yourself: {mention.to!r}"
                )
            recipients.append(recipient)

        stored: list[str] = []
        for mention, recipient in zip(mentions, recipients):
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
