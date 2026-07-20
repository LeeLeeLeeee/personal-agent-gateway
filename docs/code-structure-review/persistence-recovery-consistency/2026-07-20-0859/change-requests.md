# Change Requests

## CR-01 · Backup 복구 profile과 저장소 recoverability 명시

- Linked evidence: `B-01`, `B-02`, `F-01`, `O-01/B`
- Purpose: DB backup 성공과 전체 Gateway 복구 가능성을 구분한다.
- Target scope: Backup manifest schema, dry-run payload, Operations UI 문구와 테스트.
- Non-goals: 파일 본문 복사, live DB 교체, 자동 full restore.
- Observable behavior to preserve: SQLite online backup, checksum, schema/integrity 검사,
  별도 target restore가 그대로 동작한다.
- User decisions required before implementation: profile 명칭과 저장소별 `required`,
  `metadata-only`, `excluded` 표시 기준.
- Completion criteria: manifest와 UI가 `database-only`를 명시하고 Auth, Session,
  Artifact, Workspace, Hook secret의 recoverability를 각각 보여 준다.
- Verification: 기존 round-trip test에 manifest schema와 누락 저장소 경고를 추가한다.

## CR-02 · Hook credential 참조 무결성 dry-run 추가

- Linked evidence: `B-03`, `F-02`, `O-02/B`
- Purpose: 복원 DB의 enabled Hook이 참조하는 secret 파일 누락을 실행 전에 찾는다.
- Target scope: BackupService 입력, Hook connection reference inventory, dry-run validation.
- Non-goals: secret 값·hash·경로 본문 저장, 암호화 secret bundle.
- Observable behavior to preserve: backup에는 credential 본문이 포함되지 않는다.
- User decisions required before implementation: secret 누락을 invalid로 볼지 warning으로
  볼지 결정한다.
- Completion criteria: 모든 Hook ref의 존재 여부가 manifest에 값 없이 기록되고,
  dry-run이 DB ref와 inventory 불일치를 반환한다.
- Verification: secret 존재/누락 fixture와 manifest exact-secret 부재 검사를 추가한다.
