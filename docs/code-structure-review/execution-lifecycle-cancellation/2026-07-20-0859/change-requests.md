# Change Requests

## CR-01 · Job worker concurrency를 effective 1로 정합화

- Linked evidence: `B-01`, `F-01`, `O-01/B`
- Purpose: 설정·Settings 응답과 실제 consumer 수를 일치시킨다.
- Target scope: AppConfig validation, env loading, Settings payload, 관련 문서·테스트.
- Non-goals: worker pool, capability별 concurrency, distributed queue.
- Observable behavior to preserve: queued Job의 FIFO 실행, 승인, retry, stop/recovery.
- User decisions required before implementation: 기존 환경값이 1보다 클 때 startup fail인지
  warning+1 보정인지 결정한다.
- Completion criteria: 지원값이 1로 명시되고 2 이상 값이 조용히 무시되지 않는다.
- Verification: config/API test에서 invalid value 처리와 effective concurrency를 검증한다.

## CR-02 · Hook Run과 Team Cycle 연동 전이 coordinator 추가

- Linked evidence: `B-02`, `B-03`, `F-02`, `O-02/B`
- Purpose: Cycle link 이후 실패와 restart에서 두 상태가 서로 모순되지 않게 한다.
- Target scope: HookRunner orchestration, HookRunService/TeamRunService의 좁은 transition
  method, startup reconciliation, tests.
- Non-goals: 두 table 통합, Agent Hook 모델 변경, 자동 재시도 정책 확대.
- Observable behavior to preserve: 정상 mail projection, serial Cycle, waiting_for_user,
  settle observer, 기존 Hook Run lineage.
- User decisions required before implementation: projection 전 실패는 failed인지 interrupted인지,
  startup mismatch는 queued 복구인지 사용자 resume인지 결정한다.
- Completion criteria: link된 HookRun/Cycle의 허용 상태 조합이 명시되고 모든 실패 지점과
  restart에서 그 조합만 남는다.
- Verification: link 직후, projection 중, orchestrator 등록 전, shutdown 중 실패를
  fault injection으로 검증한다.
