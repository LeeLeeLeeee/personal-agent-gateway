# Change Requests

## CR-01 · Secret-bearing JSON에 private atomic write 적용

- Linked evidence: `B-02`, `F-01`, `O-01/B`
- Purpose: Auth/Hook secret 파일이 중간 상태로 손상되거나 플랫폼 기본 권한에만 의존하지
  않게 한다.
- Target scope: AuthStore, HookSecretStore, 최소 공용 file helper와 fault/permission tests.
- Non-goals: OS credential vault migration, 암호화 key management, backup 정책 재설계.
- Observable behavior to preserve: 기존 JSON schema와 경로, secret round-trip, recovery code hash.
- User decisions required before implementation: 공식 지원 플랫폼과 Windows ACL 보장 수준.
- Completion criteria: 같은 디렉터리 temp write → flush → replace가 사용되고, 지원 플랫폼별
  private permission 적용·검증 결과가 명시된다.
- Verification: 정상 round-trip, replace 전 failure에서 기존 파일 보존, 새 파일 권한,
  잘못된 JSON 복구 동작을 테스트한다.

## CR-02 · Domain 오류 persistence/event 경계에 공용 redaction 적용

- Linked evidence: `B-03`, `B-04`, `F-02`, `O-02/B`
- Purpose: provider/backend 예외의 credential fragment가 DB, SSE, API, health detail로
  전파되지 않게 한다.
- Target scope: HookService, HookRunner, SchedulerLoop, TeamRuntime과 관련 tests.
- Non-goals: 전역 exception middleware, 사용자용 오류 taxonomy 전체 재설계, 원격 log backend.
- Observable behavior to preserve: 실패 상태 전환, 오류 요약 표시, retry/resume, event type.
- User decisions required before implementation: 원문 진단 대신 사용할 correlation ID와
  운영자 전용 상세 로그 정책.
- Completion criteria: 모든 raw exception 경로가 저장/event 전에 같은 redaction 정책을
  통과하며 Hook secret이 explicit secret context로 전달된다.
- Verification: private key, 환경변수형 token, Hook secret이 포함된 fake exception을
  발생시켜 DB/API/SSE/health 어디에도 원문이 남지 않고 안전한 오류 요약은 유지되는지 테스트한다.
