# Change Requests

## CR-01 · Hook 실행을 Intake와 Emergency Stop에 통합

- Linked evidence: `B-01`, `B-02`, `F-01`, `O-01/B`
- Purpose: Emergency Stop 후 자동·수동 Hook 실행이 새로 시작되지 않게 한다.
- Target scope: `HookLoop`, Hook `run-now`, `EmergencyStopService`, composition, tests.
- Non-goals: 범용 executor registry, multi-process coordination, provider별 취소 구현.
- Observable behavior to preserve: 정상 intake에서 polling, dedup, queued Cycle 순서,
  restart recovery가 유지된다.
- User decisions required before implementation: Stop 중 발견한 이벤트를 cursor 미진행으로
  재수집할지, queued Hook Run으로 보존할지 선택한다.
- Completion criteria: Intake closed 상태에서 polling과 `run-now`가 실행을 만들지 않고,
  active Hook 처리 결과가 명시적 interrupted/canceled 상태로 수렴한다.
- Verification: Emergency Stop 통합 테스트에 Agent Hook과 Team Hook을 각각 추가하고
  resume 후 정확히 한 번 처리되는지 검증한다.

## CR-02 · Hook lifecycle을 readiness에 노출

- Linked evidence: `B-03`, `F-02`, `O-02/B`
- Purpose: Hook 내부 loop 사망을 Operations와 health에서 관측한다.
- Target scope: `HealthService`, app composition, health/operations payload와 테스트.
- Non-goals: 외부 메일 계정 접속 실패를 무조건 전체 503으로 만들기.
- Observable behavior to preserve: DB, Worker, Scheduler, CLI, Intake의 기존 component
  이름과 readiness 판정이 유지된다.
- User decisions required before implementation: Hook component 실패가 503인지 degraded
  detail인지 결정한다.
- Completion criteria: HookLoop와 HookRunner의 `alive`와 제한된 `last_error`가 노출되고,
  선택한 정책대로 readiness가 계산된다.
- Verification: 각 Hook task 사망 fixture와 provider polling 오류 fixture를 분리한다.

## CR-03 · 위험 기반 Hook audit 추가

- Linked evidence: `B-04`, `F-03`, `O-03/B`
- Purpose: 외부 연결과 자동 실행 상태 변경을 actor/correlation ID와 함께 추적한다.
- Target scope: Hook create, enable/disable, delete, run-now endpoint와 audit tests.
- Non-goals: secret, connection payload, prompt 본문 기록; 모든 CRUD 자동 middleware.
- Observable behavior to preserve: API 응답과 Hook secret 비노출 계약을 유지한다.
- User decisions required before implementation: Persona/Team/Rules 변경까지 같은 단계에서
  포함할지 결정한다.
- Completion criteria: 성공·거부된 중요 Hook mutation에 resource ID, action, status만
  남고 secret과 prompt는 audit metadata에 없다.
- Verification: `tests/test_api_hooks.py`에서 correlation 조회와 exact secret 부재를 검증한다.
