# Change Requests

## CR-01 · Run context와 Cycle objective 분리

- Linked evidence: `B-01`, `B-02`, `F-01`, `O-01/B`
- Purpose: TRIGGERED 생성 시 중복 goal 입력을 제거하면서 AUTO 반복 기준을 보존한다.
- Target scope: Team Run schema/model/create API, AUTO request 생성, Cycle Runtime prompt context, TeamPicker와 관련 tests.
- Non-goals: dispatcher 동시성, Persona/Rule/Space snapshot 변경.
- Observable behavior to preserve: 기존 Run 데이터 조회, AUTO 반복 횟수·간격, 수동·Hook idempotency와 previous summary lineage.
- User decisions required before implementation: TRIGGERED Run에 선택적인 장기 context를 허용할지 여부.
- Completion criteria: AUTO는 base objective 없이는 생성되지 않고, TRIGGERED는 Run goal 없이 생성되며, 모든 실행 프롬프트에 현재 Cycle objective가 명시된다.
- Verification: API validation/migration tests, AUTO/manual/Hook Cycle tests, Runtime prompt unit tests, TeamPicker interaction tests.

## CR-02 · Team Runs list를 latest Cycle 중심 read model로 변경

- Linked evidence: `B-03`, `F-02`, `O-02/B`
- Purpose: Cycle 누적 후에도 Run 한 개당 카드 하나로 현재 상태와 다음 행동을 빠르게 판단하게 한다.
- Target scope: enriched list query/API payload, existing Team name snapshot mapping, TeamRunCard, GatewayApp list filter와 관련 tests.
- Non-goals: TeamRunDetail 전체 재설계, Cycle별 독립 목록 화면, archive 기능.
- Observable behavior to preserve: 최신순 pagination, Run 선택·삭제, Persona roster 표시.
- User decisions required before implementation: 없음. 안정 제목은 frozen Team name + short Run ID를 사용한다.
- Completion criteria: 카드는 기존 frozen Team 이름(legacy fallback 포함), latest Cycle objective/status, latest Cycle Task progress, Cycle count와 policy status를 표시하며 Cycle별 row를 만들지 않는다.
- Verification: Team 이름 snapshot/fallback mapping tests, enriched query tests, no-Cycle/active/failed/AUTO-waiting card tests, 필터 interaction tests.
