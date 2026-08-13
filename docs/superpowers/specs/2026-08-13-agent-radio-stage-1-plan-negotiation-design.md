# Stage 1 — Worker가 계획에 동의해야 실행이 시작된다

지금 Leader의 계획은 아무도 검토하지 않는다. Worker는 자기 task를 받아 수행할 뿐이고, 중복·누락·dependency 충돌은 실행이 끝난 뒤 결과로 드러난다. 이 단계는 실행 전에 담당자들이 계획을 한 번 보게 만든다.

부모 결정: [ADR 2026-08-13](../../adr/2026-08-13-agent-radio-team-collaboration.md) ·
설계 전문: [2026-08-12 설계 계획](../../todo/2026-08-12-agent-radio-team-collaboration-design-plan.md) ·
평가 도구: [Stage 0 spec](2026-08-13-agent-radio-stage-0-evaluation-fixture-design.md)

## 이번 범위

ADR의 Stage 1은 독립 탐색 · plan 협상 · peer review · 최종 승인 네 가지다. **이 spec은 plan 협상만 다룬다.** 나머지 셋은 각자 spec을 받는다.

이렇게 자른 이유는 세 가지다. 첫째, ADR의 Stage 1 gate 중 "critical defect detection 개선"에 가장 짧게 닿는 지점이 계획 검토다 — 중복·누락·dependency 충돌을 잡는 자리가 여기다. 둘째, 최종 승인과 plan 협상은 둘 다 run terminal status를 건드리므로 한 spec에 넣으면 실패 경로가 서로 엉킨다. 셋째, Stage 1에는 병렬 실행이 없다(그건 Stage 3다). 그래서 "독립 탐색"은 순차로 돌아 **모델 호출만 N배가 되고 wall-clock 이득은 0**이다. 논문에서 그 단계가 값을 낸 이유가 동시성이었으므로, 병렬이 생기는 Stage 3에서 다시 판단하는 편이 낫다.

## 부모 설계에 없던 것 — revision 상한

설계 문서는 "objection이 있으면 revision을 올리고 이전 approval을 무효화한다"고만 적었고 **상한이 없다.** objection → 새 revision → 또 objection이 반복되면 Run은 모델 호출을 계속 태우며 영원히 계획 단계에 머문다.

이 저장소에서 같은 모양의 결함이 두 번 나왔다: 파싱 불가 수용 리뷰가 종료되지 않는 루프, 그리고 수용 복구 시도가 상한을 우회하는 경로. 둘 다 "상한은 있었지만 한 경로에서만 검사했다"였다.

**`PLAN_NEGOTIATION_MAX_REVISIONS = 3`.** revision 3이 승인되지 않으면 협상을 끝낸다. 상한 검사는 revision을 만드는 **단 하나의 함수**에 둔다. 재개 경로가 저장된 다음 단계를 믿고 상한을 건너뛰는 것이 앞선 두 결함의 실제 원인이었다.

## 어디에 붙나

`_plan`은 이미 task를 만든다. `_parse_task_plan` 뒤에서 `create_task`를 돌리고 `plan_note` message를 남긴 다음 plan dict 목록을 반환한다(`team_runtime.py:1755-1775`). 따라서 "승인 후에 task를 만든다"는 `_plan`을 쪼개는 일이고, 이번 범위에 맞지 않는다.

**대신 만들어진 task를 검토한다.** `start()`에서 `_plan` 직후, `set_run_status(run.id, "running")` 앞에 협상 단계가 들어간다(`team_runtime.py:1641`과 `:1668` 사이). task는 `pending`으로 존재하지만 아직 실행되지 않은 상태이므로, 검토 대상이 DB에 이미 있다는 점이 오히려 유리하다 — 검토자는 실제 acceptance 계약과 dependency를 보고 판단한다.

```
_plan (task 생성)  →  협상 (신규)  →  _execute_and_synthesize (변경 없음)
                         │
                         ├─ 만장일치 승인 → 그대로 실행
                         └─ objection → 이전 task canceled, Leader 재계획 (revision+1)
```

**새 run status를 만들지 않는다.** 협상 중 Run은 `planning`에 머문다. `TeamRunStatus`에 이미 12개 값이 있고, 협상은 "계획이 확정되지 않은 상태"라는 기존 의미에 정확히 들어맞는다.

## 데이터

마이그레이션 31(최신은 30, `_migration_30_operation_failure_shape`).

### `team_plan_revisions`

| 필드 | 의미 |
| --- | --- |
| `id`, `team_run_id`, `cycle_id` | 식별자와 소속 |
| `revision` | run+cycle 내 1부터 단조 증가 |
| `status` | `awaiting_approval` · `approved` · `superseded` · `abandoned` |
| `task_ids_json` | 이 revision이 제안한 task ID 목록 |
| `required_approver_agent_ids_json` | 생성 시점에 고정된 승인자 집합 |
| `created_at`, `decided_at` | |

`abandoned`는 상한에 걸려 끝난 마지막 revision이다. `superseded`는 objection으로 대체된 것이다. 둘을 구분하는 이유는 "왜 실행되지 않았나"의 답이 다르기 때문이다.

### `team_plan_approvals`

| 필드 | 의미 |
| --- | --- |
| `id`, `plan_revision_id`, `agent_id` | |
| `decision` | `approve` · `object` |
| `objections_json` | objection 목록 (approve면 빈 배열) |
| `created_at` | |

`(plan_revision_id, agent_id)`에 unique index를 둔다.

**JSON 한 컬럼이 아니라 별도 테이블인 이유:** 배치형 사용자 결정 ADR은 질문을 JSON으로 묶었고 그 판단은 옳았다 — 부분 승인 요구가 없었기 때문이다. 여기는 반대다. 협상은 본질적으로 부분 상태(누구는 승인, 누구는 아직)를 지나가고, 재개 시 "이 agent의 리뷰를 이미 받았나"를 원자적으로 판단해야 한다. unique index가 그 판단을 DB에 맡긴다.

### `team_runs` 컬럼 추가

`plan_negotiation_enabled` (integer, 기본 0). 켜지 않은 Run은 코드 경로가 그대로다.

**ADR의 `collaboration_mode` enum(`legacy`/`radio_lite`/`passive`)을 지금 만들지 않는다.** 협상은 radio-lite와 배타적이지 않다 — Stage 2는 Stage 1 위에 쌓인다. 한 컬럼에 네 번째 값으로 넣으면 없는 배타성을 코드에 새기게 된다. 표시할 mode가 실제로 생기는 Stage 2에서 enum을 만든다.

## 모델 호출과 operation ledger

Worker 리뷰는 모델 호출이므로 ledger에 들어가야 재개가 동작한다.

- **신규 stage 한 쌍:** `plan_review`, `plan_review_repair`. `OperationStage`는 닫힌 Literal이고(`team_model_operations.py:15`), `REPAIR_STAGE`에 항목을 추가하지 않으면 `test_every_stage_has_a_repair_target`이 실패한다. 두 곳을 함께 고친다.
- **Leader의 재계획은 새 stage를 만들지 않는다.** 기존 `cycle_planning`을 ordinal = revision 번호로 재사용한다. operation key가 `{cycle}:{task}:{stage}:{ordinal}`이므로 revision이 ordinal에 그대로 들어간다. `cycle_add_work`가 `cycle_planning_repair`를 공유하는 것과 같은 방식이다.
- Worker 리뷰의 operation key는 task가 아니라 agent 단위다. task 자리에는 검토 대상 revision을 넣는다 — 같은 agent가 revision 2를 리뷰한 것과 revision 3을 리뷰한 것은 다른 operation이어야 한다.
- 협상은 **ledger를 쓰는 planning 경로**에만 붙는다(`_invoke_plan_with_repair`). `_plan` 안에는 `model.complete`를 직접 부르고 즉석 재시도하는 분기도 있는데(`team_runtime.py:1746-1754`), 그 경로는 cycle 없는 Run용이고 현재 모든 Run이 continuous여서 죽은 경로다. 거기에 협상을 붙이지 않고, 붙일 수 없음을 테스트로 고정한다.

## Worker 리뷰 계약

검토자에게는 goal, 전체 task 목록(제목·설명·owner·acceptance·dependency), 그리고 자신이 owner인 task가 무엇인지 준다.

**task는 `T-<plan_ordinal>` 라벨로 제시한다** — `T-01`, `T-02` 처럼. task ID(UUID)를 프롬프트에 넣지 않는다. 모델이 UUID를 정확히 되돌려주기를 기대하는 것은 환각을 부르는 요구이고, 라벨은 짧고 검증도 쉽다. `plan_ordinal`은 이미 task에 있는 컬럼이므로 새 식별자를 만들지 않는다. 서버가 라벨을 실제 task ID로 되돌려 매핑한다.

```json
{"decision": "approve",
 "objections": []}
```

```json
{"decision": "object",
 "objections": [
   {"kind": "overlap", "task_ref": "T-02", "detail": "T-02가 내 T-04와 같은 파일을 만든다"},
   {"kind": "dependency_conflict", "task_ref": "T-05", "detail": "T-05가 아직 없는 산출물을 전제한다"}
 ]}
```

- `kind`는 `overlap` · `gap` · `dependency_conflict` · `scope` 넷만 허용한다. 설계 문서가 정한 검토 범위(중복, 누락, dependency 충돌, 자신의 담당 범위)와 정확히 대응한다. 열린 문자열로 두면 "이 계획이 마음에 들지 않는다"가 들어온다.
- `decision: "object"`인데 `objections`가 비면 거부한다. 반대의 근거 없는 반대는 재계획에 쓸 수 없다.
- `decision: "approve"`인데 `objections`가 비어 있지 않으면 거부한다. 두 값이 서로 모순이다.
- `task_ref`는 이 revision에 실제로 있는 `T-<plan_ordinal>` 라벨이어야 한다. 없는 라벨을 가리키는 objection은 거부한다. 판정은 라벨 집합에 대한 정확 일치로 하고 부분 문자열 비교를 쓰지 않는다 — `T-1`이 `T-10`을 가리키는 것으로 세지 않기 위해서다.
- 파싱 실패는 기존 `_invoke_with_repair` 경로를 그대로 탄다. 리뷰 하나가 끝까지 파싱되지 않으면 그 agent는 이 revision을 승인하지 않은 것으로 처리한다 — 조용히 승인으로 세지 않는다.

## 승인 규칙

- `required_approver_agent_ids`는 revision 생성 시점에 terminal(`failed`/`canceled`) 아닌 worker agent로 고정한다. Leader는 자기 계획의 승인자가 아니다.
- 전원이 **같은 revision**을 `approve`해야 실행으로 전이한다.
- objection이 하나라도 있으면 그 revision은 `superseded`가 되고, 그 revision의 task는 모두 `canceled`가 되며, Leader가 objection 전문을 입력으로 받아 revision+1을 만든다.
- 승인 도중 required approver가 terminal이 되면 그 revision은 승인될 수 없다. Leader는 그 agent의 몫을 재분배한 새 revision을 제안할 수 있고, 새 approver 집합의 만장일치를 다시 받는다. **이 재제안도 상한을 소모한다.**
- worker가 한 명도 없으면 협상 없이 기존 실패 경로를 쓴다(`start()`가 이미 `plan_and_execute run has no worker agents`로 처리한다).

## 승인되지 않고 끝나는 경우

Run은 `completed_with_failures`로 끝나고 `collaboration_plan_approval_incomplete`를 남긴다. 마지막 revision은 `abandoned`, 그 task는 `canceled`다. **미승인 계획으로 실행하지 않는다.**

여기서 새로 생기는 모양이 하나 있다: **task가 전부 `canceled`이고 아무것도 실행되지 않은 Run.** 지금까지 없던 상태다. 코드를 확인한 결과 두 가지가 이미 정해져 있다.

**terminal status를 파생에 맡길 수 없다.** `cycle_execution_disposition`(`team_lifecycle.py:161-167`)은 required task의 terminal 원인에 `canceled`가 있으면 terminal을 **`failed`** 로 판정한다. 즉 파생 규칙에 맡기면 이 Run은 `completed_with_failures`가 아니라 `failed`가 된다. 부모 설계가 정한 값은 `completed_with_failures`이므로, **협상 실패 경로는 상태를 명시적으로 설정하고 파생을 거치지 않는다.** 협상은 `_execute_and_synthesize` 앞에서 끝나므로 그 경로에서 `_terminal_status`는 애초에 호출되지 않는다.

여기서 생기는 위험은 **나중에 누군가 다시 파생하면 두 값이 어긋난다**는 것이다. 구현 계획은 협상 실패로 끝난 Run에 대해 resume normalization과 cycle dispatcher가 상태를 재계산하지 않음을 실제로 확인해야 한다. 추측으로 넘기면 "완료로 표시된 Run이 재시작 뒤 failed가 되는" 결함이 된다.

**superseded task를 `skipped`로 표시하면 안 된다.** `_required_terminal_cause`(`team_lifecycle.py:195-202`)는 `skipped` task에 prerequisite가 없으면 `LifecycleIntegrityError("Skipped task has no terminal prerequisite")`를 던진다. 계획 초안의 task는 대부분 dependency가 없으므로 `skipped`는 예외를 부른다. `canceled`를 쓴다. `_TASK_TRANSITIONS`(`team_lifecycle.py:63`)가 `pending → canceled`를 허용하므로 전이는 합법이다.

남은 확인 대상은 구현 계획이 각각 실제 동작을 적어야 한다.

- `run_build_evidence` 롤업 — `task_count`가 0이 아니라 canceled task 수가 된다. 약속-신고 비교가 canceled task에 대해 무엇을 말하는지.
- `_package_results` / synthesis — 실행 결과가 없는 Run을 어떻게 정리하는지.
- UI의 task 목록과 phase 스테퍼.

## 사용자에게 보이는 것

- 새 message kind 둘: `plan_proposed`(Leader), `plan_reviewed`(Worker, 판정과 objection 요약). 기존 감사 timeline에 그대로 쌓인다.
- `/detail`에 현재 revision: 번호, 상태, 승인해야 하는 agent, 승인한 agent, objection 전문.
- Run이 `collaboration_plan_approval_incomplete`로 끝나면 마지막 objection들을 그대로 보여준다. **이것이 사용자가 "왜 아무것도 실행되지 않았나"를 알 수 있는 유일한 표면이므로 요약하지 않는다.**

사용자 contest와 섞지 않는다. contest는 사용자 의도를 Leader 계획에 반영하는 기존 Cycle request이고, 이건 담당자들끼리의 검토다. 두 표면을 한 panel에 합치면 누가 무엇을 반대했는지가 흐려진다.

## 이번 범위가 아닌 것

- 독립 탐색, peer review, 최종 승인 — 각자 spec.
- 병렬 실행, radio-lite, passive watcher.
- ADR의 `collaboration_mode` enum.
- 사용자가 협상에 개입하는 경로. 사용자는 결과(왜 실행되지 않았나)만 본다.
- 부분 승인, 승인 위임, 승인 시한.
- 자동 재계획 품질 개선. Leader가 objection을 얼마나 잘 반영하는지는 Stage 0의 평가 대상이지 이 spec의 보장이 아니다.

## 검증

- 협상을 끄면 기존 Run의 planning → running 전이와 task 실행이 이전과 동일하다.
- 켠 Run에서 worker 전원이 승인하면 승인 전에는 어떤 task도 `in_progress`가 되지 않고, 승인 후 정상 실행된다.
- objection 하나면 그 revision의 task가 전부 `canceled`가 되고 revision+1이 생기며, Leader 프롬프트에 objection 전문이 들어간다.
- revision 3이 승인되지 않으면 Run이 `completed_with_failures` + `collaboration_plan_approval_incomplete`로 끝나고, 미승인 계획의 task는 하나도 실행되지 않는다.
- 협상 실패로 끝난 Run을 재개하거나 dispatcher가 다시 훑어도 상태가 `failed`로 바뀌지 않는다 — 파생 규칙은 required task가 canceled면 `failed`라고 말하므로, 명시 설정이 덮이지 않는지 실제로 확인한다.
- superseded task를 `skipped`로 표시하지 않는다(`LifecycleIntegrityError`를 던진다).
- **상한 검사가 재개 경로에서도 동작한다.** 협상 중간에 프로세스를 끊고 재개해도 revision이 3을 넘지 않는다.
- required approver가 승인 도중 terminal이 되면 그 revision은 승인되지 않고, 재제안이 상한을 소모한다.
- 리뷰가 끝까지 파싱되지 않는 agent는 승인하지 않은 것으로 처리된다.
- `decision`/`objections` 모순 조합, 허용되지 않는 `kind`, 계획에 없는 `task_ref`가 각각 거부된다.
- 같은 agent의 같은 revision 리뷰를 두 번 기록하려 하면 unique index가 막는다.
- 프롬프트에 task ID(UUID)가 들어가지 않고, `T-1`이 `T-10`의 objection으로 오인되지 않는다.
- `plan_review`가 `REPAIR_STAGE`에 있고 완전성 테스트가 통과한다.
- 재개 시 이미 리뷰를 받은 agent에게 모델을 다시 호출하지 않는다.
- task가 전부 canceled인 Run에서 `_terminal_status`, build evidence 롤업, `_package_results`, UI가 각각 무엇을 하는지 확인하고 기록한다.
- 백엔드 스위트가 초록을 유지한다(2026-08-13 기준 1575 passed / 0 failed).
