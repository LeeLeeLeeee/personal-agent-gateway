# Team Run 병렬 디스패처 설계

- 상태: 구현 준비 완료
- 작성일: 2026-08-08
- 대상: Personal Agent Gateway Team Cycle 실행 계층

## 1. 배경

현재 `TeamCycleDispatcher`는 모든 Cycle 요청을 하나의 `asyncio.Queue`에 넣고 단일 `_run_loop`에서 순차 실행한다. 이 때문에 서로 관계없는 Team Run도 앞선 Run의 Cycle이 끝날 때까지 기다린다.

필요한 실행 규칙은 다음과 같다.

- 서로 다른 Team Run의 Cycle은 설정된 한도 안에서 병렬 실행한다.
- 동일 Team Run에 속한 Cycle은 기존처럼 한 번에 하나씩 FIFO로 실행한다.
- 취소, 긴급 정지, 프로세스 재시작 복구, provider 대기 상태의 기존 의미를 유지한다.
- SQLite 스키마와 프론트엔드는 변경하지 않는다.

## 2. 목표와 제외 범위

### 목표

1. 서로 다른 Team Run을 최소 2개 동시에 실행할 수 있다.
2. 동일 Team Run에서는 Cycle이 겹치지 않는다.
3. 전체 병렬 실행 수를 환경 설정으로 제한한다.
4. 종료 시 dispatcher가 만든 모든 worker task를 회수한다.
5. worker 하나의 오류가 다른 Run의 실행을 중단시키지 않는다.
6. 동시성 값을 `1`로 설정하면 기존 직렬 실행 동작으로 되돌아간다.

### 제외 범위

- 한 Team Run 내부 persona 실행의 병렬화
- 여러 PAG 프로세스 또는 여러 호스트에 걸친 분산 실행
- 외부 메시지 브로커 도입
- provider 복구 작업까지 포함하는 엄격한 전역 동시성 제한
- Run 간 우선순위, 가중치, 사용자별 quota
- 프론트엔드 실행 상태 UI 변경

## 3. 현재 코드에서 유지할 불변조건

현재 DB 계층이 동일 Run 직렬화에 필요한 조건을 이미 제공한다.

- `team_cycle_requests`의 부분 unique index는 Team Run별 `dispatching` 요청을 하나로 제한한다.
- `claim_next(team_run_id)`는 `BEGIN IMMEDIATE` 트랜잭션에서 기존 `dispatching` 요청을 확인하고 다음 queued 요청을 FIFO로 claim한다.
- `list_runnable_team_run_ids()`는 현재 실행 중인 요청이 없는 Run만 반환한다.
- `TeamRunRegistry`는 task를 `team_run_id`별로 저장하므로 서로 다른 Run의 task를 동시에 추적할 수 있다.
- `TeamRunOrchestrator`의 취소 단위는 Team Run이다.

따라서 병렬성의 최종 안전장치는 인메모리 상태가 아니라 DB claim과 unique index로 유지한다. DB migration은 필요하지 않다.

## 4. 결정: 고정 크기 dispatcher worker pool

단일 소비 loop를 공유 queue를 소비하는 고정 크기 worker pool로 교체한다.

```text
enqueue_run(run_id)
        |
        v
+-----------------------+
| asyncio.Queue[run_id] |
+-----------------------+
     |       |       |
     v       v       v
 worker-0 worker-1 worker-N     전체 동시성 상한
     |       |       |
     +-------+-------+
             |
             v
 claim_next(run_id)             동일 Run은 DB에서 1개만 claim
             |
             v
 TeamRunOrchestrator.run_cycle
```

예를 들어 동시성 한도가 2일 때 A1, A2가 같은 Run A이고 B1이 Run B라면 A1과 B1은 동시에 실행할 수 있다. A2는 A1이 settle된 후에만 claim된다.

### 선택 이유

- worker 수가 곧 신규 Cycle의 최대 동시 실행 수이므로 제한이 명확하다.
- 대기 요청마다 task를 만들지 않아 task 수가 무제한으로 증가하지 않는다.
- 기존 `enqueue_run`, `run_one`, settle 후 재enqueue 흐름을 대부분 유지할 수 있다.
- 동시성 `1`은 기존 동작과 동일한 안전한 rollback 설정이다.

## 5. 상세 실행 모델

### 5.1 시작

`TeamCycleDispatcher.start()`는 설정된 `team_run_concurrency`만큼 worker task를 만든다. 생성자의 `concurrency`는 keyword-only 인자로 추가하고 기본값은 `1`로 둔다. 앱은 설정값을 명시적으로 전달하며, 직접 생성하는 기존 내부 호출은 직렬 동작을 유지한다. 생성자는 `concurrency < 1`을 즉시 거부한다.

```python
for worker_id in range(self._concurrency):
    task = asyncio.create_task(self._worker_loop(worker_id))
```

dispatcher는 단일 `_task` 대신 `_workers: dict[int, asyncio.Task[None]]`를 소유한다. `start()`는 이미 worker가 존재하면 중복 생성하지 않는 idempotent 동작을 유지한다.

### 5.2 enqueue와 claim

`enqueue_run(team_run_id)`는 지금처럼 Run ID를 공유 queue에 넣는다. worker는 item을 꺼내 `run_one(team_run_id)`을 호출한다.

동일 Run ID가 queue에 여러 번 존재할 수 있다. 이 경우:

1. 먼저 claim한 worker만 요청을 `dispatching`으로 전환한다.
2. 나머지 worker의 `claim_next()`는 `None`을 반환하고 다음 item을 소비한다.
3. 실행 중인 Cycle이 settle되면 기존 callback이 Run ID를 다시 enqueue해 다음 queued Cycle을 깨운다.

초기 구현에서는 별도의 인메모리 `active_run_ids` 또는 enqueue 중복 제거 집합을 두지 않는다. settle callback의 재enqueue와 동시에 집합을 갱신하면 다음 Cycle의 wake-up을 유실할 수 있고, DB가 이미 정확성 조건을 보장하기 때문이다.

### 5.3 동일 Run의 순서

동일 Run의 순서는 `claim_next(team_run_id)`의 기존 FIFO 기준으로 보장한다. queue에 들어간 Run ID의 순서가 아니라 DB 요청 순서가 기준이다.

### 5.4 서로 다른 Run의 순서

Run 간에는 strict FIFO나 완료 순서를 보장하지 않는다. queue 도착 순서대로 worker에 배정하지만 실행 시간과 provider 응답에 따라 완료 순서는 달라진다.

한 Run이 반복적으로 재enqueue되어 다른 Run을 장기간 굶기는 현상이 실제 관측될 때만 round-robin scheduler를 후속 도입한다. 1차 구현에 별도 공정성 scheduler를 넣지 않는다.

### 5.5 provider 대기와 복구

`ProviderOperationWaiting`이 발생하면 현재 `run_one`이 반환하므로 worker slot을 즉시 반납한다. 다른 Run은 provider 작업이 재개될 때까지 기다리지 않는다.

기존 `resume()` 및 `resume_recovered_operation()` 경로는 queue를 통하지 않고 이미 시작된 Cycle을 이어서 실행한다. 1차 구현에서는 이 경로를 신규 Cycle worker 한도에 포함하지 않는다.

- 이유: 복구 작업이 worker slot을 기다리면서 영구 정체되는 상황을 피하고 기존 복구 의미를 유지한다.
- 결과: 프로세스 재시작 직후에는 `team_run_concurrency`보다 많은 orchestration task가 잠시 존재할 수 있다.
- 후속 조건: 동시 복구가 실제 CPU, 메모리 또는 provider rate limit 문제를 만들면 신규/복구 공통 admission controller를 별도 설계한다.

## 6. 설정

`AppConfig`에 다음 값을 추가한다.

| 항목 | 값 |
|---|---|
| 필드 | `team_run_concurrency: int` |
| 환경 변수 | `AGENT_TEAM_RUN_CONCURRENCY` |
| 기본값 | `2` |
| 허용 범위 | `1..16` |
| rollback 값 | `1` |

기본값 2는 실제 병렬성을 제공하면서 로컬 Codex/provider와 SQLite에 갑작스러운 부하를 크게 늘리지 않는 값이다. 16 상한은 잘못된 환경 변수로 로컬 task와 provider 호출이 폭증하는 것을 막기 위한 운영 한계다.

이 설정은 Team Run 간 동시성만 제어한다. 한 Run 내부 worker 수 또는 `team_runs.max_workers`와 합치지 않는다.

`app.py`의 dispatcher 생성 시 값을 주입한다.

```python
TeamCycleDispatcher(
    ...,
    concurrency=settings.team_run_concurrency,
)
```

## 7. lifecycle과 상태 관측

### 7.1 정상 종료

`stop(interrupt_active=True)`의 순서는 다음과 같다.

1. 더 이상 새 item을 처리하지 않도록 모든 dispatcher worker를 cancel한다.
2. 취소가 실행 중인 `run_one()`에 전파되면 기존 `CancelledError` 경로가 해당 active Cycle을 interrupt한다.
3. `asyncio.gather(..., return_exceptions=True)`로 interrupt 처리까지 포함한 모든 worker 종료를 기다린다.
4. worker registry를 비운다.

`interrupt_active=False`인 emergency-stop 경로에서는 worker만 회수하고, 이후 `EmergencyStopService`가 persistent request 취소와 `TeamRunRegistry.cancel_all()`을 수행하는 기존 책임 분리를 유지한다.

### 7.2 queue 정리

worker는 item마다 반드시 `queue.task_done()`을 `finally`에서 한 번 호출한다. `discard_pending()`은 대기 중인 item만 제거하며 실행 중인 worker task를 직접 취소하지 않는다.

### 7.3 health

단일 `_task` 기준의 `alive`를 worker pool 기준으로 바꾼다.

- `alive`: dispatcher가 시작되었고 예상 worker가 모두 종료되지 않은 상태
- `last_error`: worker별 최근 오류 중 하나라도 남아 있으면 노출
- worker 오류: 해당 queue item만 실패 처리하고 worker loop는 계속 실행

동시에 성공한 다른 worker가 오류 상태를 잘못 지우지 않도록 `_worker_errors: dict[int, str]`를 둔다. 각 worker는 자신이 다음 item을 정상 처리했을 때 자신의 오류만 지운다.

`CancelledError`는 오류로 기록하지 않고 다시 raise한다. 일반 예외는 현재처럼 request 실패 상태와 event 발행을 마친 뒤 worker loop 경계에서 잡아 pool을 살린다.

## 8. 취소와 장애 규칙

| 상황 | 기대 동작 |
|---|---|
| Run A 취소 | A의 registry task와 요청만 취소하고 Run B는 계속 실행 |
| worker 하나에서 예외 | 해당 item 실패 처리, worker loop 유지, 다른 worker 영향 없음 |
| provider waiting | 현재 slot 반납, 다른 Run 실행 가능 |
| 프로세스 종료 | 모든 worker cancel 및 await, orphan task 없음 |
| startup reconcile | runnable Run ID를 enqueue하고 최대 설정 수만큼 병렬 claim |
| 동일 Run 중복 enqueue | 하나만 claim, 나머지는 안전한 no-op |
| DB unique 충돌 | 기존 claim 결과를 신뢰하고 중복 Cycle을 만들지 않음 |

## 9. 변경 파일과 구현 순서

### 9.1 설정

- `src/personal_agent_gateway/config.py`
  - `team_run_concurrency` 필드와 환경 변수 파싱 추가
  - `1..16` 검증 추가
- 설정 테스트 파일
  - 기본값, 환경 변수 override, 0과 17 거부 검증

### 9.2 dispatcher

- `src/personal_agent_gateway/team_cycle_dispatcher.py`
  - 생성자에 `concurrency` 추가
  - 단일 `_task`를 `_workers`로 교체
  - `_run_loop`를 worker ID를 받는 `_worker_loop`로 변경
  - 모든 worker를 start/stop/join하도록 lifecycle 변경
  - worker별 error 상태 추가
  - `run_one()`의 claim, 실행, settle 로직은 변경하지 않음

### 9.3 조립

- `src/personal_agent_gateway/app.py`
  - 설정값을 dispatcher에 주입
  - startup reconcile과 shutdown 순서는 유지
- `src/personal_agent_gateway/api/settings.py`
  - `team_run_concurrency`와 `effective_team_run_concurrency` 노출
  - 동시성 2 이상이면 `team_execution_mode="parallel"`, 1이면 `"sequential"` 반환

### 9.4 회귀 테스트

- `tests/test_team_cycle_dispatcher.py`
- `tests/test_config.py`
- `tests/test_api_settings.py`
- 기존 emergency stop, app factory, config 관련 테스트 중 생성자 변경에 영향받는 파일

DB migration, API schema, 프론트엔드 파일은 수정하지 않는다.

## 10. 테스트 설계

동시성 테스트는 sleep 시간에 의존하지 않고 `asyncio.Event` barrier로 실행 진입과 해제를 통제한다.

### 필수 테스트

1. **서로 다른 Run 병렬 실행**
   - concurrency 2에서 Run A와 B를 enqueue한다.
   - 둘 다 runtime barrier에 진입한 뒤에만 barrier를 해제한다.
   - 한쪽 완료 전에 양쪽 started가 관측되어야 한다.

2. **동시성 상한**
   - concurrency 2에서 Run A, B, C를 enqueue한다.
   - A와 B가 막힌 동안 C는 runtime에 진입하지 않아야 한다.
   - 하나를 해제하면 C가 진입해야 한다.

3. **동일 Run 직렬과 FIFO**
   - 같은 Run에 요청 2개를 enqueue한다.
   - 첫 요청이 막힌 동안 두 번째 Cycle이 만들어지지 않아야 한다.
   - 첫 요청 settle 후 두 번째 요청이 시작되고 요청 순서를 유지해야 한다.

4. **중복 wake-up 안전성**
   - 동일 Run ID를 여러 번 enqueue한다.
   - active Cycle은 하나이며 다음 요청은 settle callback 후 정확히 한 번 실행되어야 한다.

5. **호환 모드**
   - concurrency 1에서는 서로 다른 Run도 기존처럼 한 번에 하나만 실행되어야 한다.

6. **Run 단위 취소**
   - A와 B를 동시에 실행한 뒤 A만 취소한다.
   - B는 완료되고 A만 canceled/failed 규칙에 따라 정리되어야 한다.

7. **worker 장애 격리**
   - 한 item이 예외를 내도 worker pool이 살아 있고 다음 Run이 실행되어야 한다.
   - health에 오류가 노출되고 해당 worker의 다음 정상 처리 후 해제되어야 한다.

8. **종료와 긴급 정지**
   - 정상 stop 후 모든 worker task가 done 상태여야 한다.
   - emergency stop 후 queue와 registry에 orphan 실행이 없어야 한다.

9. **startup 복구**
   - 복수의 runnable Run을 reconcile한 뒤 설정 상한까지 동시에 시작해야 한다.

10. **provider waiting**
    - Run A가 provider waiting으로 전환되면 slot을 반납하고 Run B가 실행되어야 한다.

11. **설정 API**
    - 설정값 2일 때 API가 유효 동시성 2와 `team_execution_mode="parallel"`을 반환해야 한다.
    - 설정값 1일 때 API가 `team_execution_mode="sequential"`을 반환해야 한다.

### 회귀 검증

- 기존 dispatcher, recovery, emergency stop, team run orchestrator 테스트 전체 통과
- `queue_position`이 여전히 Run 내부 순서를 뜻하는지 확인
- Cycle started/completed/failed event가 요청당 한 번씩 발행되는지 확인

## 11. 완료 조건

다음을 모두 만족하면 구현 완료로 본다.

- `AGENT_TEAM_RUN_CONCURRENCY=2`에서 서로 다른 두 Team Run의 실행 구간이 실제로 겹친다.
- 같은 Team Run의 두 Cycle 실행 구간은 겹치지 않는다.
- 세 번째 Run은 동시성 2의 slot이 빌 때까지 시작하지 않는다.
- Run 하나를 취소하거나 실패시켜도 다른 Run은 정상 완료한다.
- 종료 후 dispatcher가 생성한 pending task가 없다.
- 동시성 1에서 기존 직렬 동작을 재현한다.
- DB migration과 프론트엔드 변경이 없다.
- 관련 테스트와 전체 backend test suite가 통과한다.

## 12. 대안과 기각 이유

### queue item마다 task 생성 후 semaphore 적용

동시성 제한은 가능하지만 대기 요청 수만큼 task가 만들어지고 stop 시 모든 task를 추적해야 한다. 고정 worker pool보다 lifecycle이 복잡하므로 사용하지 않는다.

### Team Run마다 전용 dispatcher 생성

Run 생성·종료 때마다 dispatcher lifecycle과 queue를 관리해야 하며 유휴 Run이 많을수록 객체와 task가 누적된다. 현재 단일 프로세스 구조에는 불필요하다.

### 외부 queue 또는 멀티프로세스 worker

여러 호스트의 확장에는 적합하지만 현재 SQLite 기반 로컬 gateway 범위를 넘어선다. 프로세스 간 claim, event 전달, 취소 전파까지 다시 설계해야 하므로 제외한다.

### 인메모리 Run ID 중복 제거

불필요한 `claim_next()` 호출은 줄지만 settle 시점의 다음 wake-up을 유실하지 않는 별도 상태 머신이 필요하다. DB claim이 이미 정확성을 보장하므로 측정된 병목이 생기기 전에는 추가하지 않는다.
