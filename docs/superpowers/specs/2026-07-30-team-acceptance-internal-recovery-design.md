# Team Run acceptance 내부 복구 설계

작성일: 2026-07-30

## 배경

현재 Team Run은 Worker가 결과를 제출한 뒤 acceptance 검수에 실패하면 Lead의 판단을 거치지
않고 Task와 Cycle을 즉시 실패 처리한다. Worker가 Lead의 산출물 계약을 어겼거나, 반대로
Lead가 처음 만든 계약이 실제 작업과 맞지 않는 경우에도 사용자가 실패를 직접 확인하고 수동으로
재시도해야 한다.

Team Run `782e5a9697e54ac28d5ab7164f744b9b`에서 이 문제가 실제로 발생했다.

- Lead가 만든 계약은 `required_outputs: []`였다.
- Worker는 `docs/knowledge/d3-core-concepts-patterns-review-draft.md`를 생성하고 deliverable로
  선언했다.
- 출처 검증은 통과했지만 `undeclared_deliverable`이 먼저 반환되어 필수 Task와 Cycle이
  실패했다.
- 생성된 문서는 workspace에 남았지만 Artifact나 Library 초안으로 발행되지 않았다.

이 오류는 사용자 선택이 필요한 문제가 아니라 Lead와 Worker가 내부에서 해결할 수 있는 계약
불일치다.

## 결정

Acceptance 검수에서 수정 가능한 실패가 발생하면 Task를 즉시 종료하지 않는다. Lead가 실패
원인과 Worker 결과를 검토하고 다음 행동을 결정한다.

1. 기존 acceptance를 유지하고 Worker에게 결과 수정을 요청한다.
2. acceptance가 잘못됐다고 판단하면 계약을 변경하고 Worker에게 새 계약으로 재제출을
   요청한다.
3. 사용자만 결정할 수 있는 선택이면 기존 사용자 결정 요청 흐름으로 전환한다.
4. 내부 수정으로 해결할 수 없으면 최종 실패로 종료한다.

내부 검토 중 Task는 `in_progress`, Run과 Cycle은 `running`을 유지한다. 별도의
`reviewing` 상태는 추가하지 않는다. 수정 이력은 Task 상세의 활동 기록에서만 볼 수 있고,
Overview에는 실패나 사용자 조치 필요 상태로 노출하지 않는다.

## 목표

- 수정 가능한 acceptance 실패를 Lead와 Worker가 사용자 개입 없이 해결한다.
- Lead가 목표와 고정 규칙에 맞지 않는 acceptance 계약을 내부에서 변경할 수 있다.
- 계약 변경과 재제출은 감사 가능한 기록으로 남는다.
- 내부 복구 중에는 Run이 계속 실행 중으로 표시된다.
- 무한 반복을 막고 내부 복구가 실패한 경우에만 사용자에게 최종 실패를 노출한다.

## 비목표

- 모델 실행 오류, 프로세스 종료, 타임아웃의 자동 복구
- `artifact_publication_failed` 같은 발행 인프라 오류 재시도
- SPACE 정책이나 Cycle의 frozen rules 변경
- 사용자의 의미 있는 제품·콘텐츠 결정을 Lead가 대신 선택
- 기존 수동 Task retry API 제거
- Worker가 만든 모든 임시 파일을 일반적으로 추적하거나 정리

## 내부 복구 대상

다음 acceptance 사유는 Lead 검토 대상으로 분류한다.

| reason code | Lead가 판단할 내용 |
| --- | --- |
| `undeclared_deliverable` | 파일을 계약에 추가할지, Worker에게 제출 취소와 정리를 요청할지 |
| `required_output_missing` | Worker에게 누락 파일을 만들게 할지, 잘못된 output 계약을 변경할지 |
| `unsafe_deliverable` | 안전한 상대 경로로 다시 제출하게 할지 |
| `required_verification_failed` | 검증을 다시 수행하게 할지, 잘못된 verification 계약을 변경할지 |
| `task_not_completed` | Worker가 수정 가능한 blocked/failed 결과인지 |
| `invalid_task_outcome` | 정해진 결과 형식으로 다시 제출하게 할지 |

`input_snapshot_modified`, `artifact_publication_failed`, 예기치 않은 Python 예외처럼 데이터
무결성이나 인프라에 해당하는 실패는 이번 내부 조정 범위에 넣지 않는다. 기존 실패 경로로
처리한다.

## 실행 흐름

```text
Worker 제출
  → acceptance 평가
  → 통과: 기존 발행·완료 흐름
  → 수정 가능한 실패:
      Task/Run/Cycle 상태 유지
      → Lead 내부 검토
      → retry_worker
          기존 계약 + 수정 지시로 같은 Worker session 재호출
      → revise_acceptance
          새 계약 검증·저장
          새 계약 + 수정 지시로 같은 Worker session 재호출
      → ask_user
          기존 사용자 결정 요청 흐름으로 전환
      → fail
          Task 최종 실패
      → Worker 재제출
      → acceptance 재평가
  → 최대 2회 소진:
      Task와 Cycle 최종 실패
```

Acceptance를 변경한 경우에도 기존 Worker 결과를 소급 승인하지 않는다. Worker가 새 계약을
받고 결과를 다시 제출해야 하며, 새 결과에 대해 acceptance를 다시 평가한다.

## Lead 검토 계약

Lead에게 다음 컨텍스트를 제공한다.

- Team Run 목표와 현재 Cycle instruction
- frozen rules와 SPACE 정책
- Task 제목·설명·소유 Worker
- 현재 acceptance
- Worker의 outcome
- acceptance 실패 status와 reason code
- 해당 시도에서 생성·수정·삭제된 workspace 경로
- 이전 내부 검토 이력과 남은 시도 횟수

Lead 응답은 다음 네 형태 중 하나인 구조화 JSON으로 제한한다.

```json
{"resolution":{"kind":"retry_worker","instruction":"구체적인 수정 지시","reason":"판단 근거"}}
```

```json
{"resolution":{"kind":"revise_acceptance","acceptance":{"required_outputs":["relative/path"],"required_verifications":["check-name"]},"instruction":"새 계약에 맞춘 재제출 지시","reason":"계약 변경 근거"}}
```

```json
{"resolution":{"kind":"ask_user","topic":"...","question":"...","why_needed":"...","options":[],"recommended_option_id":null,"blocking_scope":"task"}}
```

```json
{"resolution":{"kind":"fail","reason_code":"stable-code","summary":"내부 복구가 불가능한 이유"}}
```

파싱할 수 없는 Lead 응답은 계약을 임의로 추정하지 않는다. 남은 시도가 있으면 JSON 형식으로
한 번 다시 요청하고, 그래도 파싱할 수 없으면 최종 실패로 처리한다.

## Acceptance 변경 제약

Lead가 계약을 변경할 수 있지만 다음 제약은 유지한다.

- Team Run 목표, Cycle instruction, frozen rules에 부합해야 한다.
- SPACE의 읽기·쓰기 범위를 완화할 수 없다.
- `required_outputs`는 기존과 동일하게 bounded relative path 검증을 통과해야 한다.
- output 또는 verification 중 하나는 반드시 존재해야 한다.
- 중복 output과 verification은 허용하지 않는다.
- 변경 이유가 비어 있으면 거부한다.
- 변경된 계약으로 Worker를 재호출하지 않고 기존 결과를 즉시 승인할 수 없다.

`undeclared_deliverable`을 수정할 때 거부된 경로는 새 계약의 output으로 포함되거나 Worker가
재제출 전에 삭제해야 한다. Worker가 최종 JSON에서 경로만 숨기고 파일을 workspace에 남기는
방식은 수정 완료로 인정하지 않는다. 이 검사는 해당 실패에서 이미 신고된 경로에만 적용하여
일반적인 임시 파일 추적 기능으로 범위를 넓히지 않는다.

## 시도 횟수와 상태

- Task별 내부 acceptance 복구 한도는 2회다.
- 기존 `rounds_used`와 Agent의 `reinvocations`는 질의응답 중재용이므로 별도 계산한다.
- `team_tasks.acceptance_recovery_attempts INTEGER NOT NULL DEFAULT 0`을 추가해 재시작 후에도
  한도가 유지되게 한다.
- 한 번의 Lead 결정과 Worker 재제출을 한 시도로 계산한다.
- `ask_user`는 Worker 재제출 전이므로 내부 복구 시도를 추가로 소비하지 않는다.
- 한도 소진 또는 Lead의 `fail` 결정 때만 `finish_task(..., "failed")`를 호출한다.

내부 검토 중에는 Task가 계속 `in_progress`이므로 기존 `_terminal_status`가 Run을 조기에
실패 처리하지 않는다.

## 감사 기록

각 Lead 검토를 `team_messages`에 `acceptance_review` kind로 저장한다.

메타데이터에는 다음을 포함한다.

- `task_id`
- `attempt`
- `reason_code`
- `action`
- `reason`
- `acceptance_before`
- `acceptance_after` 또는 `null`
- 거부된 deliverable과 verification 요약
- Lead가 Worker에게 보낸 instruction

Worker 재제출 결과는 기존 `agent_output` 메시지를 새로 추가한다. `team_tasks.outcome_json`과
`acceptance_result_json`은 최신 시도를 나타내고, 이전 시도는 메시지 기록으로 추적한다.

Acceptance 변경과 시도 횟수 증가는 하나의 서비스 트랜잭션에서 처리해 계약만 바뀌고 감사
기록이 누락되는 상태를 방지한다.

## 컴포넌트 변경

| 영역 | 변경 |
| --- | --- |
| `team_runtime.py` | acceptance 실패 뒤 Lead 검토와 Worker resume을 수행하는 내부 복구 루프 |
| `team_acceptance.py` | 기존 판정 유지. reason code를 복구 가능 여부 분류에 사용 |
| `teams.py` | 복구 시도 증가, acceptance 변경, 감사 메시지 저장을 원자적으로 처리 |
| DB migration | `team_tasks.acceptance_recovery_attempts` 추가 |
| Team Run API payload | Task의 복구 시도 횟수 제공 |
| `TeamRunDetail` | Task별 `acceptance_review` 활동 이력 표시 |

Acceptance 규칙 자체를 느슨하게 바꾸지 않는다. 검수기는 계속 미선언 산출물과 누락된 검증을
거부하고, Team Runtime이 그 실패를 Lead에게 되돌리는 책임을 맡는다.

## UI

- Overview와 Task Board는 내부 복구 중인 Task를 기존 `IN PROGRESS`로 표시한다.
- 전역 오류 배너나 사용자 조치 필요 카운트는 증가시키지 않는다.
- Task 상세에 `INTERNAL REVIEW` 섹션을 추가한다.
- 각 항목에는 시도 번호, 실패 사유, Lead 행동, 계약 변경 여부를 표시한다.
- 긴 instruction과 계약 전후 값은 펼쳐보기로 제공한다.
- 내부 복구가 성공하면 Task는 평소처럼 `COMPLETED`가 된다.
- 한도 소진이나 Lead의 최종 실패 후에만 기존 실패 UI를 표시한다.

## 오류 처리

- Lead 응답 파싱 실패: 구조화 JSON으로 1회 재요청 후 최종 실패
- 잘못된 acceptance 변경: 저장하지 않고 남은 한도 안에서 Lead에게 유효한 계약을 다시 요청
- Worker resume 실패: 기존 모델 실행 실패로 최종 실패
- 내부 검토 도중 취소: 기존 `CancelledError` 경로로 Run과 Task 취소
- 내부 검토 도중 프로세스 중단: Task가 `in_progress`로 남고 기존 interruption 정규화와 Resume
  흐름을 사용하며, 저장된 시도 횟수부터 계속한다.
- 사용자 결정 필요: 기존 `defer_task_for_user_decision`을 재사용한다.
- 수정 후 같은 reason code 재발: 다음 내부 복구 시도로 진행하고 한도 소진 시 최종 실패

## 관련 테스트만 수행하는 검증 전략

전수 테스트는 기본 실행하지 않는다. 변경 모듈과 직접 연결된 테스트만 실행한다.

### Runtime

- `undeclared_deliverable` → Lead `retry_worker` → Worker 재제출 → 완료
- `undeclared_deliverable` → Lead `revise_acceptance` → 새 계약으로 재제출 → 완료
- `required_output_missing`과 `required_verification_failed`의 내부 복구
- 복구 중 Task/Run/Cycle이 실패 상태로 전이하지 않음
- 두 번 실패 후 세 번째 재제출 없이 최종 실패
- Lead `ask_user`가 기존 사용자 결정 흐름으로 연결됨
- Lead `fail`이 즉시 최종 실패로 연결됨
- 인프라 reason code는 Lead 복구 루프에 들어가지 않음

### Service와 migration

- acceptance 변경, 시도 증가, 감사 메시지가 한 트랜잭션으로 저장됨
- 잘못된 output 경로와 빈 계약을 거부함
- 기존 DB의 Task는 복구 시도 0으로 마이그레이션됨
- 중단 후 Resume에서도 기존 시도 횟수를 유지함

### Workspace

- 거부된 deliverable을 새 계약에 포함하면 통과함
- 계약에 포함하지 않은 거부 경로를 삭제하면 통과함
- 신고만 제거하고 파일을 남기면 재검수에서 거부함

### Frontend

- 내부 검토 중 Overview에 오류가 표시되지 않음
- Task 상세에서 `INTERNAL REVIEW` 이력이 표시됨
- 최종 실패 시에만 기존 실패 표시가 나타남

## 성공 조건

- `782e5a96`과 같은 계약 불일치가 발생해도 첫 acceptance 실패로 Cycle이 종료되지 않는다.
- Lead가 수정 또는 계약 변경을 선택하고 같은 Worker가 새 결과를 제출한다.
- 통과하면 다음 Task가 자동으로 실행된다.
- 사용자는 실행 중 오류 알림을 받지 않지만 Task 상세에서 내부 처리 이력을 확인할 수 있다.
- 내부 복구 2회로 해결하지 못한 경우에만 기존 실패 UI와 오류가 표시된다.
