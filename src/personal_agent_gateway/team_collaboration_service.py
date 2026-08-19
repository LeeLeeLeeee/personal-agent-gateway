"""쪽지를 저장하고 라벨을 agent로 해석한다."""

from collections.abc import Sequence
from datetime import datetime, timezone
from uuid import uuid4

from personal_agent_gateway.team_collaboration import agent_label
from personal_agent_gateway.team_outcomes import Mention


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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

    # 전달 완료의 판정 근거는 원장이다: 이 쪽지를 실은 배달의 operation이
    # applied이면 전달된 것이다. 배달 표에 상태를 따로 쓰면 같은 사실이 두 곳에
    # 살고, 그 쓰기가 effect 트랜잭션 안으로 들어가 락을 만든다.
    _UNDELIVERED_SQL = """
        select m.id, m.sender_agent_id, m.content
        from team_messages m
        where m.team_run_id = ?
          and m.recipient_agent_id = ?
          and m.kind = 'peer_mention'
          and not exists (
              select 1
              from team_collaboration_delivery_items i
              join team_collaboration_deliveries d on d.id = i.delivery_id
              join team_model_operations o on o.operation_key = d.operation_key
              where i.message_id = m.id and o.status = 'applied'
          )
        order by m.created_at, m.id
    """

    def undelivered(
        self, team_run_id: str, agent_id: str
    ) -> tuple[tuple[str, str, str], ...]:
        """이 에이전트가 아직 받지 못한 쪽지.

        저장하지 않고 유도한다: 적용된 배달에 묶이지 않은 것이 미전달이다.
        커서를 따로 두면 같은 사실이 두 곳에 살고, 그 둘은 조용히 어긋난다.
        """
        rows = self._db.fetchall(self._UNDELIVERED_SQL, (team_run_id, agent_id))
        return self._as_notes(team_run_id, rows)

    def _as_notes(self, team_run_id: str, rows) -> tuple[tuple[str, str, str], ...]:
        by_id = {
            agent: label for label, agent in self.labels_for_run(team_run_id).items()
        }
        return tuple(
            (row["id"], by_id.get(row["sender_agent_id"], "?"), row["content"])
            for row in rows
        )

    def notes_by_id(
        self, team_run_id: str, message_ids: Sequence[str]
    ) -> tuple[tuple[str, str, str], ...]:
        if not message_ids:
            return ()
        placeholders = ",".join("?" for _ in message_ids)
        rows = self._db.fetchall(
            "select id, sender_agent_id, content from team_messages"
            f" where id in ({placeholders}) order by created_at, id",
            tuple(message_ids),
        )
        return self._as_notes(team_run_id, rows)

    def open_delivery(
        self,
        team_run_id: str,
        agent_id: str,
        operation_key: str,
        message_ids: Sequence[str],
    ) -> str:
        """이 호출에 실을 쪽지를 확정한다.

        같은 operation_key로 다시 부르면 기존 items를 유지한다. 복구가 새로
        조회하면 그 사이 도착한 쪽지가 섞여 프롬프트가 달라지고, 프롬프트는
        operation의 request digest에 들어가므로 원장이 복구를 거부한다.

        `reserve`가 자기 트랜잭션을 여므로(team_model_operations.py:158) spec이
        말한 "예약과 같은 트랜잭션"은 불가능하다. 예약 **전에** 확정하는 것으로
        의도적으로 완화한다: 확정 뒤 예약 전에 죽으면 배달은 prepared로 남고
        다음 시도가 같은 items를 재사용한다.
        """
        existing = self._db.fetchone(
            "select id from team_collaboration_deliveries where operation_key = ?",
            (operation_key,),
        )
        if existing is not None:
            return existing["id"]
        delivery_id = uuid4().hex
        with self._db.connection() as connection:
            connection.execute(
                "insert into team_collaboration_deliveries"
                " (id, team_run_id, agent_id, operation_key, status, created_at,"
                " settled_at) values (?, ?, ?, ?, 'prepared', ?, null)",
                (delivery_id, team_run_id, agent_id, operation_key, _now()),
            )
            for message_id in message_ids:
                connection.execute(
                    "insert into team_collaboration_delivery_items"
                    " (delivery_id, message_id) values (?, ?)",
                    (delivery_id, message_id),
                )
        return delivery_id

    def delivery_for(self, operation_key: str) -> str | None:
        """그 키로 열린 배달의 id. 쪽지가 0개인 배달도 존재하는 배달이다.

        호출자가 쪽지 수로 판단하면, 쪽지 0개로 한 번 확정된 호출이 재진입할 때
        새로 조회해 다른 접두사를 만들고, 원장이 바뀐 지문을 거부해 런이 죽는다.
        """
        row = self._db.fetchone(
            "select id from team_collaboration_deliveries where operation_key = ?",
            (operation_key,),
        )
        return row["id"] if row else None

    def delivery_message_ids(self, operation_key: str) -> tuple[str, ...]:
        rows = self._db.fetchall(
            "select i.message_id from team_collaboration_delivery_items i"
            " join team_collaboration_deliveries d on d.id = i.delivery_id"
            " join team_messages m on m.id = i.message_id"
            " where d.operation_key = ? order by m.created_at, m.id",
            (operation_key,),
        )
        return tuple(row["message_id"] for row in rows)

    def undelivered_count(self, team_run_id: str) -> int:
        """런 전체의 미전달 쪽지 수. undelivered와 같은 판정 근거를 쓴다."""
        row = self._db.fetchone(
            "select count(*) as total from team_messages m"
            " where m.team_run_id = ? and m.kind = 'peer_mention'"
            " and not exists ("
            "   select 1 from team_collaboration_delivery_items i"
            "   join team_collaboration_deliveries d on d.id = i.delivery_id"
            "   join team_model_operations o on o.operation_key = d.operation_key"
            "   where i.message_id = m.id and o.status = 'applied')",
            (team_run_id,),
        )
        return int(row["total"]) if row else 0
