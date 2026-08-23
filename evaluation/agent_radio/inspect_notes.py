"""What the notes actually carried in one run, for a human to judge.

Three questions in the order they matter, because they are not the same
question and only the third one is about value:

1. Did a note go worker-to-worker at all? (the channel worked)
2. Was it delivered -- did it reach a later prompt? (a stored note nobody
   read is not communication)
3. Does the recipient's own result show it acted on what the note said?
   (this is the only one that means the note was worth sending, and it is
   the one a human has to read and decide)

Usage: python inspect_notes.py <path to app.sqlite> [<run id>]
"""

import json
import sqlite3
import sys


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    connection = sqlite3.connect(argv[0])
    connection.row_factory = sqlite3.Row
    run_filter = ("and m.team_run_id = ?", (argv[1],)) if len(argv) > 1 else ("", ())

    notes = connection.execute(
        f"""
        select m.id, m.content, m.metadata_json,
               s.name as sender, s.role as sender_role,
               r.name as recipient, r.role as recipient_role,
               exists (
                   select 1 from team_collaboration_delivery_items i
                   join team_collaboration_deliveries d on d.id = i.delivery_id
                   join team_model_operations o
                        on o.operation_key = d.operation_key
                   where i.message_id = m.id and o.status = 'applied'
               ) as delivered
        from team_messages m
        join team_agents s on s.id = m.sender_agent_id
        left join team_agents r on r.id = m.recipient_agent_id
        where m.kind = 'peer_mention' {run_filter[0]}
        order by m.created_at, m.id
        """,
        run_filter[1],
    ).fetchall()

    if not notes:
        print("쪽지 0건 — 채널이 아예 쓰이지 않았다.")
        print("(모드가 radio_lite 인지, 워커가 2명 이상인지 먼저 확인하라.)")
        return 0

    peer_to_peer = 0
    for note in notes:
        route = f"{note['sender_role']} -> {note['recipient_role'] or '?'}"
        if note["sender_role"] == "member" and note["recipient_role"] == "member":
            peer_to_peer += 1
        metadata = json.loads(note["metadata_json"] or "{}")
        # A consult carries a query_id (it answers a question) or a to_label
        # from a needs_info; a plain hand-off note carries neither.
        kind = "자문" if "query_id" in metadata or "topic" in metadata else "전달"
        print(
            f"[{kind}] {note['sender']} -> {note['recipient'] or '?'} "
            f"({route}, 배달={'예' if note['delivered'] else '아니오'})"
        )
        print(f"    {note['content']}")

    print()
    print(f"쪽지 {len(notes)}건 중 워커간 {peer_to_peer}건, "
          f"배달 {sum(n['delivered'] for n in notes)}건")

    print()
    print("받은 쪽 결과 (쪽지 내용이 여기에 반영됐는지 직접 읽어라):")
    for task in connection.execute(
        """
        select t.title, t.status, a.name as owner, t.result
        from team_tasks t left join team_agents a on a.id = t.owner_agent_id
        order by t.created_at
        """
    ):
        print(f"  [{task['owner']}] {task['title']} ({task['status']})")
        if task["result"]:
            print(f"    {task['result'][:400]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
