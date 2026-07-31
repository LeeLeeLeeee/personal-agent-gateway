# Team Model Operation Ledger Design

작성일: 2026-07-31

## 상태

- 결정: 별도 모델 실행 원장 채택
- 기준 커밋: PAG `c578e12`
- 선행 조건: LMG provider snapshot/readiness 브랜치 `ab58942`
- 대체 대상: 실패한 PAG Task 5 runtime continuation/receipt 구현

## 목표

Team Run의 각 원격 모델 호출을 호출 전에 영속화하고, 호출 결과와 도메인 효과를
원자적으로 연결한다.

이를 통해 다음을 보장한다.

- provider가 모델 실행을 시작하지 않았다고 확인한 오류만 자동 재시도한다.
- 실행 여부가 불명확한 timeout이나 프로세스 종료는 자동 재생하지 않는다.
- 완료된 Worker 호출 뒤 Lead 검토가 중단되어도 Worker를 다시 호출하지 않는다.
- 재시작 후 operation 상태만으로 안전한 다음 행동을 결정한다.
- cycle execution metadata를 runtime continuation, generation, receipt 저장소로 사용하지 않는다.

## 배경

기존 Task 5 구현은 cycle execution metadata에 runtime continuation과 세대, receipt를
추가했다. 독립 리뷰를 반복하면서 다음 상태 소유권 문제가 드러났다.

- 모델 응답이 자신이 시작된 세대가 아니라 응답 후 최신 세대에 연결될 수 있었다.
- Lead 검토와 Worker 실행의 실제 호출 주체가 섞일 수 있었다.
- task, message, acceptance, user decision과 checkpoint 변경을 원자적으로 묶기 어려웠다.
- receipt replay가 실제 effect가 만든 결과를 증명하지 못했다.
- 다른 metadata writer가 checkpoint를 과거 상태로 되돌릴 수 있었다.

실패 브랜치는 Task 4 기준에서 프로덕션 코드 약 2,500줄과 테스트 약 2,600줄 이상을
추가했지만 최종 리뷰에서 stale response와 decision replay 문제가 남았다. 이 설계는 해당
구현을 이어서 수정하지 않고 Task 4 승인 커밋에서 다시 시작한다.

## 핵심 결정

모델 호출 자체를 1급 영속 엔터티로 만든다.

```text
TeamRuntime
  -> TeamModelOperationService.reserve()
  -> TeamModelInvoker.invoke()
  -> TeamModelOperationService.complete()
  -> TeamModelEffectService.apply()
  -> TeamRunService domain mutations
```

각 모델 호출은 네트워크 활동 전에 operation을 확보한다. 호출 결과는 처음 확보한
operation에만 기록할 수 있다. result 적용과 operation의 `applied` 전환은 같은 SQLite
트랜잭션에서 수행한다.

## 컴포넌트 경계

### TeamRuntime

- 다음 semantic stage를 선택한다.
- operation key를 구성할 수 있는 run, cycle, task, stage ordinal을 제공한다.
- operation 상태에 따라 invoke, local apply, wait, interrupt 중 하나를 선택한다.
- provider retry, operation CAS, replay receipt를 직접 구현하지 않는다.

### TeamModelOperationService

- operation reserve, begin, complete, wait, ambiguous, fail, cancel과 transaction-local
  apply CAS를 소유한다.
- operation key의 중복과 cycle의 동시 미완료 operation을 차단한다.
- stage별 source state와 actor ownership을 검증한다.
- duplicate lifecycle 전이는 version과 result digest로 차단한다.

### TeamModelInvoker

- operation을 `invoking`으로 전환한 뒤 모델을 호출한다.
- 각 원격 시도 전에 `consumer_run_id`를 생성하고 operation에 저장한다.
- safe pre-stream admission 오류만 같은 operation에서 제한적으로 재시도한다.
- 응답을 stage별 구조화 결과로 검증한 뒤 `complete()`에 넘긴다.
- raw prompt, raw response, provider stderr, credential을 원장에 저장하지 않는다.

### TeamModelEffectService

- stage별 validated result를 Team domain mutation으로 변환한다.
- operation service의 transaction-local apply CAS와 TeamRunService의 좁은 mutation을
  하나의 SQLite transaction에서 조정한다.
- duplicate apply 시 기존 `effect_ref_json`을 검증해 effect를 반복하지 않는다.

### TeamRunService

- 기존 Team run, cycle, task, agent, message, acceptance, decision 상태를 소유한다.
- operation service가 연 SQLite transaction 안에서 사용할 수 있는 stage별 도메인
  mutation을 제공한다.
- operation lifecycle이나 원격 실행 retry를 소유하지 않는다.

### TeamProviderRecovery

- operation-aware provider waiting 진입과 readiness claim을 수행한다.
- recovery 대상 operation ID와 stable reason code를 저장한다.
- claim 이후 operation의 원래 semantic stage를 재개한다.
- Task를 일반 pending queue에 다시 넣어 완료된 Worker stage를 재실행하지 않는다.

## 데이터 모델

신규 테이블:

```text
team_model_operations
```

필수 필드:

| 필드 | 의미 |
| --- | --- |
| `id` | operation UUID |
| `operation_key` | 동일 semantic 호출을 식별하는 unique key |
| `team_run_id` | 소유 Team Run |
| `cycle_id` | 소유 cycle, 이번 설계에서는 NOT NULL |
| `task_id` | planning/synthesis는 null, task stage는 task ID |
| `agent_id` | 실제 모델 호출 주체 |
| `provider` | 호출 provider |
| `stage` | semantic stage |
| `stage_ordinal` | task retry/mediation/acceptance의 영속 순번 |
| `status` | operation lifecycle 상태 |
| `version` | CAS용 정수 버전 |
| `attempts` | 원격 admission 시도 횟수 |
| `consumer_run_id` | 현재 원격 시도 ID |
| `upstream_session_id` | 확인된 provider session |
| `request_digest` | raw prompt를 저장하지 않는 요청 결합값 |
| `result_kind` | 검증된 구조화 결과 종류 |
| `result_json` | 검증된 구조화 결과 |
| `result_digest` | 완료 결과 불변성 확인값 |
| `effect_type` | 적용된 domain effect 종류 |
| `effect_ref_json` | 생성·변경된 domain row 참조 |
| `reason_code` | stable failure/wait reason |
| `created_at` 등 | 준비·호출·완료·적용 시각 |

### Operation key

동일 semantic 호출은 항상 같은 key를 사용한다.

```text
<cycle_id>:cycle_planning:0
<cycle_id>:cycle_planning_repair:1  # initial planning source
<cycle_id>:cycle_add_work:0
<cycle_id>:cycle_planning_repair:2  # add-work source
<cycle_id>:<task_id>:worker_execution:<attempt>
<cycle_id>:<task_id>:mediation_lead:<round>
<cycle_id>:<task_id>:mediation_worker:<round>
<cycle_id>:<task_id>:acceptance_lead:<attempt>
<cycle_id>:<task_id>:acceptance_worker:<attempt>
<cycle_id>:cycle_synthesis:<answered-decision-revision>
```

`stage_ordinal`은 `rounds_used`, `acceptance_recovery_attempts`처럼 이미 영속화된 도메인
카운터에서 계산한다. 같은 key를 다시 reserve하면 새 row를 만들지 않고 기존 operation을
반환한다. planning repair와 add-work repair는 같은 stage를 사용하므로 각각 stable ordinal
1과 2를 사용해 한 cycle 안에서 충돌하지 않는다. synthesis는 최초 호출에 ordinal 0을
사용하고, 적용된 synthesis decision에 사용자 답변이 확정될 때마다 ordinal을 1씩 증가시킨다.
따라서 답변이 반영된 user-facing prompt는 이전 operation을 다시 열지 않고 새 immutable
operation에만 결합된다.

### Cycle 단일 미완료 제약

다음 상태를 미완료 operation으로 본다.

- `prepared`
- `invoking`
- `completed`
- `waiting_for_provider`
- `ambiguous`

한 cycle에는 미완료 operation을 하나만 허용한다. 현재 Team Runtime이 task를 순차 실행하는
제약과 일치한다. DB unique/CAS 조건이 runtime의 in-memory 확인보다 우선한다.

### 저장하지 않는 정보

- raw prompt
- raw model response
- provider credential과 local token
- raw stderr
- 일반 cycle metadata의 runtime continuation, generation, receipt

## 상태 모델

| 상태 | 의미 | 자동 네트워크 호출 |
| --- | --- | --- |
| `prepared` | 호출 전 영속 예약 완료 | 허용 |
| `invoking` | 원격 호출이 시작됨 | 추가 호출 금지 |
| `completed` | 검증된 결과 영속화, domain effect 미적용 | 금지 |
| `applied` | domain effect까지 원자적으로 적용됨 | 금지 |
| `waiting_for_provider` | 안전한 admission 재시도 소진 | readiness claim 후 허용 |
| `ambiguous` | 실행 시작 여부가 불명확함 | 자동 호출 금지 |
| `failed` | non-retryable 오류 또는 구조화 결과 최종 실패 | 금지 |
| `canceled` | 사용자 취소 | 금지 |

허용 전이는 다음과 같다.

```text
prepared -> invoking
invoking -> completed
invoking -> waiting_for_provider
invoking -> ambiguous
invoking -> failed
completed -> applied
waiting_for_provider -> prepared
ambiguous -> prepared  # explicit Resume + exact existing session only
prepared/invoking/completed/waiting_for_provider/ambiguous -> canceled
```

모든 전이는 `status + version` CAS를 사용한다. `completed`의 result는 최초 기록 후
불변이다. 동일 result digest는 idempotent하게 기존 row를 반환하고 다른 digest는
무결성 오류로 처리한다.

## 모델 호출과 결과 적용

### 호출 전

1. Runtime이 현재 domain state에서 operation key와 actor를 결정한다.
2. `reserve()`가 `begin immediate` 안에서 run/cycle/task/agent ownership과 source status를
   확인한다.
3. 같은 key의 operation이 있으면 기존 상태를 반환한다.
4. 다른 미완료 operation이 있으면 새 호출을 거부한다.
5. `begin_attempt()`가 `consumer_run_id`, attempt, version을 호출 전에 기록한다.

모델 결과는 이때 확보한 operation ID와 version을 계속 사용한다. 응답 후 현재 cycle 상태를
다시 읽어 operation을 재결합하지 않는다.

### 완료

1. stage parser가 raw response를 기존 strict schema로 검증한다.
2. 검증된 값만 `result_json`에 저장한다.
3. 응답에서 확인된 upstream session은 agent가 아니라 operation에 먼저 저장한다.
4. `complete()`는 정확히 해당 `invoking` operation만 `completed`로 전환한다.
5. process가 이 사이에 취소·중단 상태로 바꾼 operation에는 결과를 기록하지 않는다.

### 적용

`apply()`는 한 transaction에서 다음을 수행한다.

1. operation이 `completed`이고 actor/stage/task가 일치하는지 확인한다.
2. stage별 domain effect를 적용한다.
3. 생성된 task/message/decision 또는 변경된 task ID를 `effect_ref_json`에 기록한다.
4. operation의 확인된 upstream session을 실제 actor agent에 반영한다.
5. operation을 `applied`로 전환한다.

duplicate apply는 `applied` operation의 effect reference를 검증한 뒤 기존 결과를 반환한다.
일반 message 검색이나 mutable metadata를 replay 증거로 사용하지 않는다.

## Stage별 적용

### Cycle planning과 add work

- 검증된 task plan과 Task 생성이 같은 apply transaction에 속한다.
- 첫 응답이 invalid JSON이면 해당 operation은 `failed`로 닫고 별도 repair operation을
  만든다.
- invalid 응답의 upstream session은 operation에만 보관하고 유효한 plan이 적용되기 전까지
  Lead agent session으로 승격하지 않는다.
- repair operation이 provider waiting에 들어가도 draft/queued preplanning 불변식을
  유지한다.

### Worker execution

- Worker operation result는 validated `TaskOutcome`이다.
- apply는 `agent_output`, task outcome, workspace change metadata를 원자적으로 기록한다.
- 이후 acceptance 계산과 Lead review는 별도 operation이다.
- Worker operation이 `applied`된 뒤에는 어떤 Lead 장애도 Worker 초기 호출을 다시 열지 못한다.

### Mediation

- Worker query result를 적용한 뒤 별도 `mediation_lead` operation을 만든다.
- Lead answer 적용은 round 증가와 answer message를 같은 transaction에 기록한다.
- Worker continuation은 별도 `mediation_worker` operation이다.
- operation actor가 stage의 실제 Lead 또는 Worker와 다르면 호출 전에 거부한다.

### Acceptance

- rejected Worker outcome 적용 후 별도 `acceptance_lead` operation을 만든다.
- Lead resolution 적용은 acceptance audit, attempt 증가, acceptance 변경을 같은 transaction에
  기록한다.
- `retry_worker`와 `revise_acceptance`만 별도 `acceptance_worker` operation을 만든다.
- `ask_user`는 decision request 생성과 task/agent waiting을 Lead operation apply와 함께
  처리한다.
- `fail`은 task/agent terminal 처리와 Lead operation apply를 함께 처리한다.

### Synthesis

- synthesis operation은 모든 required task가 terminal인 경우에만 reserve할 수 있다.
- 최초 synthesis는 ordinal 0을 사용한다. synthesis decision이 답변된 뒤 재개하면 답변된
  decision revision 수에 따라 ordinal 1, 2, ...의 새 immutable operation을 사용한다.
- 아직 답변되지 않은 decision은 ordinal을 전진시키지 않는다.
- summary 저장, run/cycle 완료, result packaging 진입을 operation apply 이후에 수행한다.
- artifact/result packaging 실패는 기존 별도 인프라 오류 기록을 유지한다.

## Provider retry와 waiting

### Safe pre-stream admission

자동 재시도 대상은 LMG가 provider run 미시작을 확인한 다음 코드뿐이다.

- `provider_not_ready`
- `provider_unavailable`
- `capacity_exceeded`

총 시도 횟수는 3회다. delay는 0.5초, 1.5초다. 각 시도 전에 operation의 attempt와
`consumer_run_id`를 저장한다.

소진 시:

- operation -> `waiting_for_provider`
- run/cycle -> `waiting_for_provider`
- task stage이면 task -> `waiting_for_provider`
- calling agent -> `waiting`
- cycle request는 `dispatching` 유지

provider recovery metadata에는 operation ID, provider, stable reason, attempts, first/next/warning
timestamp만 저장한다.

### Operation-aware claim

claim은 operation을 기준으로 원래 semantic source 상태를 복원한다.

- planning/add work: draft run, queued cycle, task 없음
- Worker stage: running run/cycle, in-progress task, Worker running
- Lead mediation/acceptance: running run/cycle, in-progress task, Lead 호출 stage 유지
- synthesis: summarizing run, running cycle

claim은 task를 일반 pending queue로 되돌리지 않는다. Runtime은 먼저 미완료 operation을
재개하고 그 operation이 `applied`된 뒤에만 다음 semantic stage를 선택한다.

## Ambiguous timeout과 프로세스 종료

다음은 자동 재시도하지 않는다.

- response open 이후 timeout
- read timeout과 read error
- stream terminal 누락
- provider run 시작 여부를 확인할 수 없는 request failure
- process 재시작 시 남아 있는 `invoking` operation

처리:

1. operation -> `ambiguous`
2. run/cycle -> `interrupted`
3. task와 agent는 기존 interruption 정규화를 사용하되 operation은 보존
4. 자동 cycle loop는 해당 operation을 실행하지 않음

명시적 Resume:

1. operation의 `upstream_session_id`가 있으면 해당 session의 identity를 strict 검증한다.
2. 없으면 provider, team run, `consumer_run_id`가 모두 일치하는 LMG session을 strict 조회한다.
3. 정확히 하나일 때만 operation에 upstream session을 저장하고 `prepared`로 되돌린다.
4. Runtime은 새 session이 아니라 그 operation에 저장된 session으로만 같은 semantic stage를 재개한다.
5. 세션이 없거나 여러 개거나 조회가 실패하면 계속 `ambiguous`/`interrupted`로 유지한다.
6. 이 전이는 사용자 Resume 요청에서만 허용하며 자동 dispatcher는 수행하지 않는다.

이 설계는 외부 도구 부작용의 exactly-once를 약속하지 않는다. 대신 결과가 불명확한 모델 호출을
자동으로 새 실행하지 않는 것을 보장한다.

## 재시작 reconciliation

시작 시 cycle별 미완료 operation을 확인한다.

| Operation 상태 | 시작 시 처리 |
| --- | --- |
| `prepared` | 안전하게 orchestrator가 호출 가능 |
| `invoking` | `ambiguous` + run/cycle `interrupted` |
| `completed` | 모델 재호출 없이 local apply 예약 |
| `waiting_for_provider` | waiting 보존, recovery tick 대상 |
| `ambiguous` | interrupted 보존, 자동 실행 금지 |
| `applied` | 다음 semantic stage 선택 가능 |

Runtime은 정상 semantic stage를 고르기 전에 하나의 stage-aware dispatcher로 미완료
operation을 먼저 처리한다. `completed`는 request를 재구성하거나 모델 client를 만들지 않고
local apply하고, `prepared`는 persisted key/ordinal/session에 맞는 deterministic message를
재구성해 같은 operation을 호출한다. planning/add-work와 Worker structured repair는 raw
invalid response를 재삽입하지 않고 각각 기존 source prompt와 고정 repair instruction만
사용한다. `invoking`, `waiting_for_provider`, `ambiguous`는 자동 호출을 거부한다. DB의
operation CAS가 duplicate scheduling의 최종 방어선이다.

## 취소

사용자 Cancel은 미완료 operation을 `canceled`로 만들고 기존 Team 취소 흐름을 실행한다.

- canceled operation의 늦은 응답은 completion CAS에서 거부한다.
- canceled run은 Resume할 수 없다.
- process shutdown은 사용자 Cancel과 구분해 기존 interruption 정책을 유지한다.

## API와 사용자 표시

이번 설계는 신규 public API를 추가하지 않는다.

- 기존 Team detail의 provider waiting payload는 operation-backed metadata에서 sanitize한다.
- raw operation result와 request digest는 API에 노출하지 않는다.
- Overview와 Task Board의 provider waiting UI는 후속 Task 6/7에서 구현한다.
- ambiguous operation은 기존 `interrupted` UX를 사용한다.

## 보안과 관측성

로그 허용 필드:

- operation ID
- stage
- provider
- attempt
- stable reason code
- transition
- recovery trigger
- snapshot status와 age

로그와 API에서 제외:

- local token과 provider credential
- raw prompt/response
- raw stderr
- unredacted gateway payload

operation result는 기존 strict parser를 통과한 구조화 값만 저장한다.

## 범위

포함:

- operation migration과 서비스
- cycle-backed Team model invocation
- safe pre-stream retry
- ambiguous timeout/session reconciliation
- planning, Worker, mediation, acceptance, synthesis operation
- provider waiting과 dispatcher 연결
- restart reconciliation

제외:

- non-cycle Team invocation의 provider recovery
- 여러 PAG 프로세스가 하나의 DB를 공유하는 분산 lease
- 외부 도구 부작용의 exactly-once
- provider 자동 교체
- frontend 변경
- Task 6 polling/manual resume API와 Task 7 UI

## 검증 전략

전수 테스트는 기본 실행하지 않는다. 다음 관련 테스트만 사용한다.

### Operation service

- 같은 key reserve가 같은 operation을 반환한다.
- 한 cycle에 두 미완료 operation을 만들 수 없다.
- status/version CAS가 stale completion과 stale apply를 거부한다.
- completed result가 불변이다.
- duplicate apply가 domain effect를 반복하지 않는다.
- canceled operation의 늦은 응답을 거부한다.

### Restart matrix

- prepared는 호출 가능하다.
- invoking은 ambiguous/interrupted가 된다.
- completed는 모델 호출 없이 적용된다.
- waiting은 보존된다.
- ambiguous는 자동 실행되지 않는다.
- applied는 다음 stage로 진행한다.

### Runtime

- Worker 완료 후 Lead provider waiting에서 Worker 모델 호출은 정확히 1회다.
- Worker 완료 후 Lead ambiguous timeout에서도 Worker를 자동 재호출하지 않는다.
- Lead mediation과 acceptance의 actor/session이 Worker에게 귀속되지 않는다.
- invalid planning repair failure가 request를 settle하지 않는다.
- completed operation local apply 뒤 같은 effect가 중복되지 않는다.

### Remote admission

- safe pre-stream admission 오류만 총 3회 시도한다.
- response-open/read/terminal/timeout 오류는 1회 후 ambiguous다.
- 각 시도 전에 consumer run ID가 operation에 기록된다.

### Provider recovery와 dispatcher

- waiting 진입이 cycle request를 `dispatching`으로 유지한다.
- concurrent claim이 같은 operation을 한 번만 반환한다.
- Lead-stage claim이 Worker task를 pending으로 되돌리지 않는다.
- restart가 waiting cycle을 failed/interrupted로 변환하지 않는다.
- 사용자 Resume의 strict session match만 ambiguous operation을 prepared로 되돌린다.
- 자동 dispatcher와 내부 resume은 ambiguous operation을 claim하지 않는다.

### App factory

- Team client만 operation-aware retry/invocation wrapper를 사용한다.
- non-Team session client 동작은 변하지 않는다.

## 성공 조건

- 모든 원격 모델 호출은 네트워크 전에 operation을 가진다.
- 완료된 Worker 호출 뒤 Lead 장애가 Worker 자동 재실행으로 이어지지 않는다.
- ambiguous 호출은 자동 재생되지 않는다.
- safe pre-stream provider 오류만 bounded retry와 waiting recovery를 사용한다.
- process restart가 invoking을 ambiguous로, completed를 local apply로 구분한다.
- domain effect와 operation applied 전환이 원자적이다.
- cycle metadata에는 runtime continuation, generation, receipt가 없다.
- 관련 backend 테스트와 독립 코드 리뷰가 통과한 뒤에만 polling/manual-resume/UI 후속 작업으로 진행한다.
