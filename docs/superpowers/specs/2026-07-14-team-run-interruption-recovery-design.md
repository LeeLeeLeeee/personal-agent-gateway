# Team Run 중단 감지와 사용자 재개 설계

작성일: 2026-07-14

## 배경

Team Run의 실행 상태는 SQLite에 저장되지만 실제 `asyncio.Task`는 프로세스 메모리의
`TeamRunRegistry`에만 존재한다. 게이트웨이가 종료되거나 재시작되면 레지스트리는 비워지지만
`team_runs.status`의 `planning`/`running`/`summarizing`, 실행 중 Agent, `in_progress` Task는
그대로 남는다. 새 프로세스는 이 상태를 실제 실행으로 오인해 `start`를 거부하고, UI는 실행 중인
것처럼 표시한다.

이 설계는 중단된 실행을 자동으로 재개하지 않는다. 중단 사실을 명시적으로 기록하고 사용자가
상태를 확인한 뒤 `Resume`을 눌렀을 때만 재개한다.

## 결정

새 Team Run 상태 `interrupted`를 추가한다. 시작 시점에 실제 레지스트리 작업이 없는 활성 상태
Run을 `interrupted`로 정규화하고, 사용자가 명시적 Resume API/UI로만 다시 실행한다.

현재 제품은 하나의 로컬 게이트웨이 프로세스가 하나의 SQLite DB를 사용한다. 따라서 이번 범위는
DB lease나 heartbeat를 추가하지 않고, 프로세스 레지스트리와 상태 조건으로 중복 실행을 막는다.

## 목표

- 재시작 후 실제 작업이 없는 Run이 `planning` 또는 `running`으로 남지 않는다.
- 중단된 Run은 사용자 동의 없이 모델이나 도구 실행을 시작하지 않는다.
- 완료된 Task와 산출물은 보존하고 중단 당시 실행 중이던 Task만 재대기한다.
- Resume 중복 요청은 거부한다.
- 사용자 Cancel과 프로세스 중단을 각각 `canceled`, `interrupted`로 구분한다.

## 비목표

- 여러 게이트웨이 인스턴스가 하나의 DB를 공유하는 분산 실행
- Task가 수행한 외부 부작용의 자동 롤백 또는 정확히 한 번 실행 보장
- 완료·실패 Task의 재실행
- 자동 Resume
- 기존 Add work의 종료 Run 재개 의미 변경

## 상태 모델

`TeamRunStatus`에 `interrupted`를 추가한다.

| 상태 | 의미 | Start | Resume | Add work | Cancel |
| --- | --- | --- | --- | --- | --- |
| `draft` | 시작 전 | 허용 | 거부 | 거부 | 허용 |
| `planning`/`running`/`summarizing` | 실제 레지스트리 작업 실행 중 | 거부 | 거부 | 기존 규칙 | 허용 |
| `interrupted` | 실행이 사라져 사용자 판단 대기 | 거부 | 허용 | 거부 | 허용 |
| 종료 상태 | 실행 완료·실패·취소 | 거부 | 거부 | 기존 Add work 규칙 | 상태 유지 |

`interrupted`는 활성 상태도 종료 상태도 아닌 재개 가능 상태다. `finished_at`은 설정하지 않는다.
기존 `started_at`은 최초 실행 시각으로 보존한다.

## 중단 정규화

`TeamRunService.interrupt_run`은 한 SQLite 트랜잭션에서 다음 변경을 수행한다.

1. 대상 Run이 `planning`, `running`, `summarizing` 또는 종료 시점에 캡처된 실행 Run인지 확인한다.
2. Run 상태를 `interrupted`로 바꾸고 `finished_at`을 비운다.
3. `in_progress` 또는 종료 처리 중 `canceled`가 된 해당 Task를 `pending`으로 되돌린다.
4. 해당 Task의 `started_at`, `finished_at`, `error_message`를 비우되 기존 완료 Task의 결과는 유지한다.
5. `running` 또는 종료 처리 중 `canceled`가 된 Agent를 `pending`으로 바꾸고
   `current_task_id`, `finished_at`을 비운다.
6. Agent의 `upstream_session_id`, Run workspace, 완료 Task와 `agent_output` 메시지는 보존한다.
7. `system_interrupted` 메시지에 이전 Run 상태와 재대기 Task ID를 기록한다.

정규화는 같은 Run에 반복 적용해도 추가 상태 손상이 없도록 idempotent하게 만든다.

## 감지 흐름

### 비정상 종료 후 시작

`create_app`이 서비스와 빈 `TeamRunRegistry`를 만든 직후, 요청을 받기 전에 모든 활성 상태 Run을
조회한다. 새 프로세스에는 이전 프로세스의 Task가 존재할 수 없으므로 이 Run들을 `interrupted`로
정규화한다.

### 정상 종료

애플리케이션 shutdown 시 레지스트리는 현재 Run ID를 캡처하고 취소 이유를 `shutdown`으로 기록한
뒤 Task를 취소하고 기다린다. `TeamRuntime`의 기존 취소 정착이 일시적으로 `canceled`를 만들 수
있으므로 실행 wrapper가 `shutdown` 이유를 확인해 캡처된 Run만 최종적으로 `interrupted`로
정규화한다.

사용자가 Cancel API를 호출하면 취소 이유는 `user`다. 이 경우 기존 동작대로 `canceled`로 끝나며
정규화하지 않는다.

## Resume 흐름

신규 API는 `POST /api/team-runs/{team_run_id}/resume`이다.

1. Run 존재 여부를 확인한다.
2. 상태가 정확히 `interrupted`인지 확인한다.
3. `TeamRunRegistry`에 같은 Run이 등록되어 있으면 `409`를 반환한다.
4. `TeamRuntime.resume`을 백그라운드 Task로 만들고 즉시 레지스트리에 등록한다.
5. 런타임은 Run과 리더를 `running`으로 전환하고 `pending` Task부터 실행한다.
6. 완료 후 기존 synthesis와 종료 상태 계산을 사용한다.
7. 성공, 실패, 사용자 취소 여부와 무관하게 wrapper가 레지스트리를 정리한다.

같은 이벤트 루프에서 실행 여부 확인과 레지스트리 등록 사이에 `await`를 두지 않아 동시 클릭의
중복 등록을 막는다. 두 번째 요청은 `409`를 받는다.

`start`는 `draft` 상태에서만 허용한다. `interrupted` Run에 Start를 호출해 플래닝부터 다시
시작하는 우회 경로를 차단한다. Add work도 `interrupted` 상태에서는 `409`를 반환한다.

## UI

- `StatusBadge`에 `INTERRUPTED`를 추가한다.
- 중단 상태는 기존 Planning 단계로 잘못 표시하지 않고 단계 표시의 활성 항목을 해제한다.
- Agent Sessions 상단에 중단 안내와 `Resume` 버튼을 표시한다.
- `Resume`을 누르면 기존 확인 UI로 재대기 Task가 다시 실행될 수 있음을 알린다.
- 요청 중에는 버튼을 비활성화한다.
- 성공 응답 후 Team Run 상세와 목록을 다시 조회한다.
- 중단 상태에서는 Add work 버튼을 숨긴다.

Task Board에는 정규화된 Task가 `PENDING`으로 표시된다. 완료 Task와 연결 문서는 그대로 남는다.

## 오류 처리

- 존재하지 않는 Run Resume: `404`
- `interrupted`가 아닌 Run Resume: `409`
- 이미 레지스트리에 등록된 Run Resume: `409`
- Resume 이후 런타임 오류: 기존 규칙대로 `failed`와 `error_message` 저장
- 사용자 Cancel: `canceled`, Resume 불가
- 정규화 트랜잭션 실패: 앱 시작을 실패시켜 부분 정규화 상태로 요청을 받지 않음

## 테스트 전략

### 서비스

- 활성 Run을 `interrupted`로 전환한다.
- `in_progress` Task만 `pending`으로 되돌리고 완료 Task와 결과를 보존한다.
- 실행 Agent만 정규화하고 `upstream_session_id`를 보존한다.
- 반복 정규화가 안전하다.

### 레지스트리와 애플리케이션 수명주기

- shutdown 취소 이유가 `shutdown`으로 기록된다.
- 사용자 취소는 `user`로 유지된다.
- 시작 시 stale 활성 Run이 `interrupted`가 된다.
- 정상 종료 중 실행 Run도 최종적으로 `interrupted`가 된다.

### API와 런타임

- `interrupted` Run Resume이 즉시 반환하고 백그라운드 실행을 등록한다.
- 중복 Resume과 잘못된 상태를 `409`로 거부한다.
- 중단 Task부터 실행하고 완료 Task를 반복하지 않는다.
- 중단 상태의 Start와 Add work를 거부한다.

### 프런트엔드

- `INTERRUPTED` 배지와 Resume 버튼을 렌더링한다.
- 중단 상태에서는 Add work가 보이지 않는다.
- Resume 요청 중 버튼이 비활성화된다.
- 성공 후 상세·목록이 갱신되고 실패 시 오류를 표시한다.

### 통합 검증

1. 실행 중인 테스트 Run을 둔 채 게이트웨이를 종료한다.
2. 재기동 후 Run이 `interrupted`이고 실제 프로세스가 없는지 확인한다.
3. Resume 전에는 새 실행이 시작되지 않는지 확인한다.
4. Resume 후 완료 Task는 유지되고 중단 Task부터 진행되는지 확인한다.
5. 전체 백엔드·프런트 테스트와 프런트 빌드를 실행한다.

## 현재 Run 적용

현재 유령 상태인 `93ace5704d9b4f36987f635ade9f2da6`은 새 버전 재기동 시 시작 정규화에
의해 `interrupted`가 된다. 사용자가 상세 화면에서 Resume을 누르기 전에는 실행하지 않는다.

## 재검토 조건

다음 조건 중 하나가 생기면 DB lease/heartbeat 방식으로 확장한다.

- 둘 이상의 게이트웨이 프로세스가 같은 SQLite DB를 공유한다.
- 원격 워커가 게이트웨이 프로세스 밖에서 계속 실행된다.
- 정확히 한 번 실행 또는 외부 부작용 보상이 제품 요구사항이 된다.
