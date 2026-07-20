# Change Requests

## CR-01 · SSE stream epoch와 bounded dedup 도입

- Linked evidence: `B-01`, `B-02`, `F-01`, `O-01/B`
- Purpose: server restart 후 재사용된 숫자 ID의 새 event를 client가 버리지 않게 한다.
- Target scope: EventBus payload, SSE tests, `useSessionController` dedup state.
- Non-goals: durable broker, DB event log, cross-tab delivery.
- Observable behavior to preserve: Last-Event-ID replay, heartbeat, Team/Hook/Chat dispatch,
  duplicate notification 방지.
- User decisions required before implementation: bounded window 크기와 epoch 없는 legacy
  event 처리 방식.
- Completion criteria: boot마다 새 `stream_id`가 제공되고 client는 composite key를
  제한된 개수만 보관한다.
- Verification: 같은 ID/같은 epoch는 한 번, 같은 ID/새 epoch는 새 event로 처리하며
  window가 상한을 넘지 않는 테스트를 추가한다.

## CR-02 · Team/Hook collection을 background event와 정합화

- Linked evidence: `B-03`, `B-04`, `F-02`, `O-02/B`
- Purpose: detail, list card, hook row가 같은 terminal/status를 표시하게 한다.
- Target scope: `useTeamRunController`, GatewayApp Hook handler, event payload 활용과 tests.
- Non-goals: 전역 cache library, 모든 event마다 전체 bootstrap, optimistic mutation 재설계.
- Observable behavior to preserve: selected detail delta, authoritative terminal refetch,
  Hook toast/badge, Browser Notification.
- User decisions required before implementation: row delta 우선인지 throttled collection
  refetch 우선인지 선택한다.
- Completion criteria: background Team terminal/input과 Hook update 뒤 목록 row가 새 상태,
  last_polled_at, last_error를 표시한다.
- Verification: detail에서 목록으로 돌아가는 테스트와 Hook 목록을 열어 둔 테스트에서
  수동 reload 없이 새 상태를 검증한다.
