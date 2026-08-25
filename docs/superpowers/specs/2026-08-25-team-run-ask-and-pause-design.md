# 팀런에 묻기: 정지와 답변 설계

## 무엇을 만드는가

진행 중인 팀런에 **물어보기** 경로를 만든다. 사용자가 질문을 던지면 팀이 안전한 자리에서 멈추고, 리드가 워크스페이스를 직접 읽고 사람 말로 답한다. 멈춘 동안 몇 번이든 더 물을 수 있고, **재개**를 누르면 하던 일을 이어서 한다.

**질문은 일감을 만들지 않는다.** 이것이 이 조각의 본론이다.

## 왜 필요한가

지금은 팀런에 무슨 말을 걸든 일감이 나온다. 그냥 궁금해서 물어봐도 리드가 계획을 세우고 워커 전원에게 분배해버린다.

이유는 프롬프트 계약에 있다. 리드가 낼 수 있는 답이 정해져 있고, 거기에 "그냥 대답한다"가 없다.

| 경로 | 위치 | 리드가 낼 수 있는 답 |
| --- | --- | --- |
| 새 팀런 계획 | `team_runtime.py:109` `PLANNING_PROMPT` | 일감 배열, 또는 `ask_user` |
| 진행 중 말 걸기 | `team_runtime.py:301` `ADD_WORK_PROMPT` | 일감 배열 **뿐** |
| 계획에 이의 | `team_runtime.py:333` `CONTEST_PROMPT` | 판정(amend/partial/reject/ask_back) |

`ADD_WORK_PROMPT`는 첫 줄부터 "Break the request into concrete tasks"이고 `ask_user`조차 없다. 질문을 던져도 일감이 나오는 것은 오작동이 아니라 계약대로의 결과다.

`CONTEST_PROMPT`가 가장 가까워 보이지만 그것은 **이의 제기**지 질문이 아니다. 계획을 반박할 근거가 있어야 쓸 수 있고, 판정 결과로 일감이 생기거나 계획이 뒤집힌다. "이게 왜 이렇게 돼 있지"를 담을 그릇이 아니다.

## 범위

**만드는 것**

- 물어보기 버튼과 그 경로 (새 팀런 / 진행 중 둘 다)
- 배치 경계 정지와 재개
- 리드 질문 응답 프롬프트
- 질문·답변을 대화 기록에 남기고 화면에서 일감과 구분해 보여주기

**만들지 않는 것**

- 즉시 정지(진행 중인 프로바이더 호출 취소). 아래 「왜 여기서만 멈출 수 있는가」 참조
- 리드가 알아서 "이건 질문이네"라고 판단하는 것. 사용자가 명시적으로 고른다
- 정지 중 일감 추가·이의 제기. 정지 중에는 **질문과 재개만** 한다
- 질문을 위한 사이클이나 일감 생성
- **계획 단계의 정지 지점.** 계획 중 정지를 요청하면 계획이 끝난 뒤에 멈춘다

## 정지

### 어디서 멈추는가

`_execute_batches`의 배치 재충전 지점 바로 앞(`team_runtime.py:3041`, `if not batch:`).

### 왜 여기서만 멈출 수 있는가

루프 구조가 이렇다.

```
while True:
    ...복구 전처리...
    if not batch:                      # 3041
        ready_tasks = ...
        if not ready_tasks: return
        _start_batch(...)              # 최대 3개를 한꺼번에 띄운다
    started = batch.pop(0)             # 3050
    outcome = await started.call
    ...그 하나를 정산...
```

`batch`는 **비었을 때만** 다시 채워진다. 최대 3개(`team_lifecycle.py:18` `MAX_CONCURRENT_WORKERS`)를 띄우고 하나씩 빼서 정산하다가, 다 비면 그때 새 배치를 시작한다. 따라서 **`not batch`인 순간은 떠 있는 프로바이더 호출이 하나도 없는 시점**이다.

다른 자리에서 멈추면 재개가 불가능해진다.

1. 호출이 떠 있는 채로 루프를 벗어나면 `_execute`의 `finally`(`team_runtime.py:2804`)가 그 호출들을 취소한다.
2. 취소된 호출의 operation row는 `invoking` 상태로 남는다.
3. `_recover_open_operation`은 `invoking`·`waiting_for_provider`·`ambiguous`를 만나면 `OperationConflict`를 던진다(`team_runtime.py:1320`).

즉 자동 재개가 아니라 **프로바이더 복구(운영자 개입) 대상**이 된다. 궁금해서 물어본 대가로 복구 작업이 생기면 안 된다.

### 어떻게 빠져나오는가: 예외로 올린다

검사 지점에서 그냥 `return`하면 **런이 터진다.**

`_execute`가 반환되면 `_execute_and_synthesize`(`team_runtime.py:4489`)가 곧바로 완료 상태를 계산한다.

```python
while True:
    await self._execute(run, leader, workers, cycle_id)
    request = self._teams.get_active_decision_request(run.id, cycle_id)
    if request is not None and request.status == "collecting":
        return await self._publish_user_decision_request(run, cycle_id)   # 결정 요청만 받아준다
    ...
    status = _terminal_status(tasks, dependencies)
    if status is None:
        raise LifecycleIntegrityError(...)                                # 미완료 일감이 남아 있으면 여기
```

조기 반환을 받아주는 가드는 **결정 요청 하나뿐**이다. 정지는 미완료 일감을 남긴 채 반환하므로 `_terminal_status`가 `None`을 내고 `LifecycleIntegrityError`로 끝난다.

**그래서 정지는 예외로 올린다.** 이미 같은 자리에 정지 신호 셋이 산다 — `ProviderOperationWaiting`·`AmbiguousModelOperation`·`UnparsableLeadOutput`. `team_runtime.py:3213`의 주석이 이들을 이렇게 부른다: "Pause signals, not task failures". 새 배관이 아니라 **기존 어휘에 하나 더 넣는 것**이다.

`ProviderOperationWaiting`은 dispatcher까지 올라가 조용히 `return`한다(`team_cycle_dispatcher.py:227`). 정지도 같은 경로를 탄다.

### 예외를 재-raise 목록에 반드시 등록해야 한다

`start()`(`team_runtime.py:2184`)와 `resume()`에는 넓은 `except Exception`이 있고, 거기 걸리면 `_settle_failed`가 런을 **실패로 표시한다.**

```python
except (ProviderOperationWaiting, AmbiguousModelOperation):
    raise                                    # <- 여기 없으면
except Exception as exc:
    run = self._settle_failed(run, error, cycle_id)   # <- 정지가 실패가 된다
```

정지 예외를 이 재-raise 절에 넣지 않으면 **정지를 누를 때마다 팀런이 실패 처리된다.** `start()`와 `resume()` 양쪽 다 고쳐야 한다.

### 대가

정지를 눌러도 즉시 멈추지 않는다. 화면은 이 지연을 숨기지 않고 `정지 요청됨` → `정지됨` 두 단계로 보여준다.

기다리는 길이는 팀이 지금 무엇을 하고 있느냐에 달렸다.

| 팀의 단계 | 기다리는 길이 |
| --- | --- |
| 일감 실행 중 | 지금 떠 있는 배치(최대 3개)가 끝날 때까지 |
| **계획 중 / 계획 검토 중** | **계획과 검토가 다 끝날 때까지** |

`start()`는 계획(`_plan`) → 계획 검토(`_negotiate_plan`) → 그 다음에야 실행(`_execute_and_synthesize`)이다(`team_runtime.py:2134`, `2168`, `2176`). 검사 지점은 실행 단계에만 있으므로 **계획 중에는 멈출 자리가 없다.** 계획 개정은 최대 3회(`PLAN_NEGOTIATION_MAX_REVISIONS`)이고 매 개정마다 검토자 수만큼 호출이 돈다.

계획 단계에도 검사 지점을 두지 않는 이유는 이번 조각을 키우지 않기 위해서다. 계획 중 정지가 실제로 답답하면 그때 붙인다. 화면은 정지를 기다리는 동안 팀이 어느 단계인지 보여줘서, 오래 걸리는 이유가 드러나게 한다.

## 상태

**`team_runs.pause_requested_at`** (nullable) — "정지 요청됨". 이 동안 런은 아직 `running`이다.

**런/사이클 상태에 `paused` 추가** (`team_lifecycle.py:21`, `:35`) — "정지됨".

### 기존 상태를 재사용하지 않는 이유

**`waiting_for_user`는 방향이 반대다.** 그것은 팀이 사용자에게 물어서 멈춘 상태이고(`team_runtime.py:5130` `_publish_user_decision_request`), 화면에 결정 요청 대화상자가 뜨며, 사용자가 답을 제출해야 풀린다. 우리가 만드는 것은 사용자가 팀에게 묻는 상태다. 보여줄 것도, 푸는 방법도 다르다. 하나로 묶으면 둘 다 망가진다.

**`interrupted`는 뜻이 다르다.** 구조는 가장 가깝다 — `_interrupt_cycle`(`team_cycle_dispatcher.py:325`)이 사이클을 `interrupted`로 두고 `/resume`가 그것을 찾아 이어서 돌린다. "멈추고 나중에 이어서"라는 뼈대가 같다.

그래도 재사용하지 않는다. `interrupted`는 **사고로 끊긴 상태**이고 화면이 그렇게 말한다 — "Running work was returned to Pending"(`TeamRunDetail/index.jsx:1339`). 사용자가 의도적으로 멈춘 것과 한 글자로 묶이면, 화면은 둘을 구분해 말할 수 없고 "왜 끊겼지"와 "내가 멈췄지"가 같은 배너를 쓴다. 뼈대는 빌리되 이름은 나눈다.

**자동 사이클 시리즈의 `paused_*`는 층이 다르다.** `paused_failure`·`paused_user`·`paused_interrupted`(`team_cycles.py:20`)는 사이클 하나가 **끝난 뒤** `settle_cycle`에서만 걸리는 시리즈 상태다(`team_cycle_dispatcher.py:277`). 사이클이 도는 도중을 표현하지 못한다.

### 요청이 소진되는 조건

- 검사 지점에 도달해 `paused`로 전이 → 요청을 지운다
- 사이클이 먼저 끝남 → 멈출 것이 없으므로 요청을 지우고 질문 단계로 간다
- 팀런이 취소됨 → 요청을 지운다
- 팀런이 재시작으로 중단됨 → 요청을 **유지한다**. 사용자가 누른 정지는 재시작을
  건너 살아남고, 재개 후 첫 배치 경계에서 이행된다

**재시작 경로는 하나가 아니다.** 크래시 복구는 `app.py` 시작 훅의
`TeamRunService.interrupt_active_runs()` → `interrupt_run()` 이 raw SQL 로
`status='interrupted'` 만 쓰는 경로이고, `on_team_run_settled` 를 타지 않는다.
정상 종료는 dispatcher 의 `_interrupt_cycle` 이 `on_team_run_settled` 를 타는
별개 경로다. 요청이 살아남는 것은 전자에서는 **그 UPDATE 문이
`pause_requested_at` 을 언급하지 않기 때문**이고, 후자에서는 `interrupted` 가
`TERMINAL_CYCLE_STATUSES` 에 없기 때문이다.

앞의 이유는 암묵적이다 — 나중에 그 UPDATE 문에 칸을 하나 더 넣는 사람이
`pause_requested_at` 을 함께 건드리면 이 동작이 조용히 뒤집히고, 그것을 잡는
테스트가 없다. `interrupt_active_runs` 를 고치게 되면 여기를 먼저 읽어야 한다.

### 런이 돌고 있지 않을 때

`draft`, 사이클 사이, 이미 완료된 런은 정지 단계를 건너뛰고 바로 질문 단계로 간다.

## 리드의 답변

새 질문 프롬프트를 만든다. 요구하는 것은 넷이다.

- 일감으로 쪼개지 말고 사람 말로 답한다
- 답하기 전에 워크스페이스 파일을 직접 읽는다
- 사실을 말할 때는 근거가 된 파일을 밝힌다
- 확인하지 못한 것은 확인하지 못했다고 말한다. 추측을 사실처럼 쓰지 않는다

뒤의 두 줄은 장식이 아니다. 같은 규칙이 이미 `WORKER_PROMPT`(`team_runtime.py:150`) 끝에 있고, 그 이유는 거기 적혀 있다 — 아무도 확인하지 않은 주장을 사실처럼 쓰면 답변과 구분되지 않으므로, 빈틈을 밝히는 것보다 나쁘다. 워커의 결과물은 acceptance 검수를 거치지만 **질문 답변에는 검수 절차가 없다.** 그래서 이 규칙이 워커에게보다 더 필요하다.

### 리드는 이미 워크스페이스를 읽을 수 있다

`_team_model_factory`(`app.py:652`)는 리드와 워커를 구분하지 않는다. 둘 다 같은 workspace root 위에서 같은 실행 컨텍스트로 돈다. `agent.workspace_path`가 비어 있으면 `config.workspace_root / team_run_id`로 떨어지므로 런 안의 모든 에이전트가 같은 폴더를 본다.

새로 줄 능력은 없다. 프롬프트만 막고 있었다.

### SPACE 읽기 제약

런의 SPACE 정책이 `read_mode: none`이면 리드도 팀 작업 폴더 밖은 못 본다. 이때 리드는 "볼 수 없는 곳"이라고 말해야 한다. 위의 "확인하지 못한 것은 확인하지 못했다고 말한다"가 이 경우를 받는다.

## 화면과 조작

진행 중인 팀런 화면에 지금 **일감 추가**와 **이의 제기**가 있다(`frontend/src/components/organisms/TeamRunDetail/index.jsx:802`). 여기에 **물어보기**를 더한다. 셋이 각각 다른 일을 한다는 것이 화면에서 바로 보이는 것이 이 변경의 요점이다.

| 버튼 | 하는 일 | 결과 |
| --- | --- | --- |
| 일감 추가 | 일을 더 시킨다 | 일감이 생긴다 |
| 이의 제기 | 계획을 반박한다 | 계획이 바뀌거나 일감이 생긴다 |
| **물어보기** | **묻는다** | **답이 온다. 일감은 안 생긴다** |

**흐름**

- 팀이 돌고 있으면: `정지 요청됨` → (돌던 배치 마무리) → `정지됨` → 질문 → 답변
- 팀이 안 돌고 있으면: 바로 질문 → 답변
- 멈춘 동안 몇 번이든 더 묻는다. 끝나면 **재개**

### `paused`를 알아야 하는 화면 코드

상태 문자열이 여러 곳에 직접 나열돼 있다. 새 상태를 추가하면 전부 손봐야 한다. `TeamRunDetail/index.jsx` 기준:

| 위치 | 지금 | 해야 할 일 |
| --- | --- | --- |
| `:959` `canResume` | `status === "interrupted"` | `paused`도 재개 가능 |
| `:961` 취소 가능 조건 | `["planning","running","summarizing","waiting_for_user"]` | `paused`·정지 요청 중에도 취소 가능 |
| `:50` 정렬 우선순위 | `["interrupted","waiting_for_user"]` | `paused`도 같은 취급 |
| `:1336` 중단 배너 | `status === "interrupted"` | `paused`는 **다른** 배너 (질문·답변과 재개 버튼) |
| `:956` 일감 추가 가능 조건 | `interrupted`·`waiting_for_user` 제외 | `paused`도 제외 (정지 중엔 질문과 재개만) |

**새 팀런에서도** 목표를 주고 시작하는 대신 물어보기로 시작할 수 있다. 리드가 답만 하고 팀런은 시작되지 않은 채 남는다. 답을 듣고 일을 시키고 싶으면 그때 시작한다.

## 서버 경로

### 새 경로

| 경로 | 하는 일 |
| --- | --- |
| `POST /team-runs/{id}/pause` | 정지를 요청한다. 런이 돌고 있지 않으면 곧바로 `paused` |
| `POST /team-runs/{id}/questions` | 질문을 던지고 답을 받는다. `paused`이거나 런이 돌고 있지 않을 때만 받는다 |
| `GET /team-runs/{id}/questions` | 지금까지의 질문·답변을 읽는다 |

### 물어보기를 사이클 요청으로 만들면 안 되는 이유

`lifecycle_mode`가 `continuous`인 런 — API로 만든 모든 팀런이 그렇다(`api/team_runs.py:169`) — 은 사용자의 말을 **사이클 요청**으로 받는다. `/add-work` 엔드포인트는 continuous 런을 아예 409로 거절한다(`api/team_runs.py:1004`).

그 사이클 요청은 dispatcher를 거쳐 `orchestrator.run_cycle` → `continue_cycle` → `runtime.add_work`로 간다(`team_cycle_dispatcher.py:215`, `team_run_orchestrator.py:66`). 즉 `ADD_WORK_PROMPT`에 도달하는 실제 경로는 사이클 요청이다.

**사이클 요청은 사이클을 만든다.** 질문에는 사이클도 일감도 만들지 않기로 했으므로, 물어보기는 이 경로를 타면 안 된다. 별도 엔드포인트가 필요한 이유가 이것이다.

### 고쳐야 하는 기존 가드

`POST /team-runs/{id}/resume`는 지금 `interrupted`가 아니면 409를 던진다(`api/team_runs.py:788`).

```python
if run.status != "interrupted":
    raise HTTPException(status_code=409, detail="Only interrupted team runs can be resumed")
```

`paused`도 재개할 수 있어야 한다. 이 가드와, 그 아래 재개할 사이클을 고르는 부분(`status == "interrupted"`로 찾는다)이 함께 `paused`를 받아야 한다.

### 정지 중 사이클 요청은 `dispatching`에 묶인 채로 둔다

정지가 예외로 dispatcher까지 올라가면 그 사이클 요청은 `dispatching` 상태로 남는다(`ProviderOperationWaiting`과 같은 경로). 그동안 `claim_next`는 그 런의 다른 요청을 잡지 않는다 — 런당 `dispatching` 요청이 하나뿐이어야 하기 때문이다(`team_cycles.py:456`).

**정지 중에는 이것이 맞는 동작이다.** 멈춰서 묻고 있는 사이에 다른 사이클이 끼어들면 안 된다.

재개할 때 이 요청을 되살려야 한다. `/resume`는 런과 사이클을 다시 `running`으로 올리지만 요청 상태는 건드리지 않으므로, 재개 경로가 묶여 있던 요청을 이어받도록 명시해야 한다. 새 요청을 만들면 안 된다 — 새 요청은 새 사이클을 만들고, 그러면 정지 전에 하던 일감들이 다른 사이클에 남는다.

### 시작하지 않은 팀런에도 리드는 있다

`create_team_run`은 런을 만들 때 리드와 멤버 에이전트를 함께 넣고(`_insert_agent`) 워크스페이스도 그 자리에서 준비한다. 그래서 `draft` 상태의 런에도 답할 리드가 있고 읽을 폴더가 있다. 새 팀런을 물어보기로 시작하는 경우에 따로 준비할 것이 없다.

## 남는 기록

질문과 답을 `team_messages`(`db.py:246`)에 남긴다. `kind`가 자유 문자열이고 `metadata_json`이 있으므로 **스키마 변경이 필요 없다**. `append_message`(`teams.py:3414`)를 그대로 쓴다.

`kind`는 사용자의 질문에 `user_question`, 리드의 답에 `lead_answer`를 쓴다. 화면에서 워커 결과물(`agent_output`)이나 계획 기록(`plan_note`)과 섞이지 않게 하기 위한 구분이다.

사이클도 일감도 만들지 않는다. 따라서 수용·전달·산출물 체계는 이 기능을 모른 채로 남는다.

## 어긋났을 때

| 상황 | 어떻게 되는가 |
| --- | --- |
| 정지를 눌렀는데 그 사이 팀이 끝남 | 요청을 지우고 질문 단계로 간다 |
| 리드 답변 호출이 실패 | 멈춘 상태를 유지하고 "답을 받지 못했습니다"만 띄운다. 다시 묻거나 재개할 수 있다 |
| 정지를 기다리는 중 취소 | 기존 취소가 이긴다. 질문은 버린다 |
| 서버 재시작 | `paused`는 저장돼 있으므로 살아남는다. `pause_requested_at`만 걸려 있었다면 **그것도 살아남아** 재개 후 첫 배치 경계에서 이행된다 — 사용자가 누른 정지를 재시작이 삼키지 않는다. 화면은 그동안 `정지 요청됨`으로 그 사실을 보여준다 |

**답변 실패가 팀런을 망가뜨리면 안 된다.** 질문은 팀런 바깥에 있는 일이므로 실패해도 일감·사이클·수용 상태를 건드리지 않는다.

## 확인할 것

1. **질문을 던져도 일감이 하나도 생기지 않는다** — 이 조각의 본론이므로 가장 중요한 검사
2. 정지를 요청하면 돌던 배치가 끝난 뒤에 멈추고, 멈춘 시점에 떠 있는 호출이 없다
3. 멈춘 팀런을 재개하면 남은 일감이 처음부터가 아니라 이어서 돈다
4. 멈춘 채로 여러 번 물어도 매번 답이 온다
5. 팀이 돌고 있지 않을 때 물으면 정지 단계 없이 바로 답이 온다
6. 사이클이 먼저 끝난 경우에도 답은 온다
7. 답변 호출이 실패해도 팀런 상태가 망가지지 않는다
8. **정지해도 팀런이 실패로 표시되지 않는다** — 재-raise 목록 누락을 잡는 검사
9. **정지해도 `LifecycleIntegrityError`가 나지 않는다** — 미완료 일감이 남은 채로 멈추는 것이 정상임을 고정하는 검사
10. 재개 후 정지 전과 **같은 사이클**에서 이어진다 (새 사이클이 생기지 않는다)
11. 계획 중에 정지를 요청하면 계획이 끝난 뒤에 멈춘다

## 결정 기록

2026-08-25 설계 대화에서 정한 것들.

| 정한 것 | 왜 |
| --- | --- |
| 질문인지 아닌지는 **사용자가 명시적으로** 고른다 | 리드가 판단하게 하면 오판 여지가 남고, 오판의 결과가 바로 지금의 문제다 |
| 리드가 **워크스페이스를 읽고** 답한다 | 맥락만으로 답하면 근거 없는 답이 된다. 능력은 이미 있다 |
| 사이클·일감 없이 **메시지로만** 남긴다 | 질문은 산출물이 아니다. 전달·수용 체계에 얹을 이유가 없다 |
| 진행 중이면 **정지하고** 묻는다 | 답하는 동안 워크스페이스가 계속 바뀌면 답이 도착할 때 이미 낡는다 |
| 정지는 **배치 경계**에서 (즉시 아님) | 즉시 정지는 재개 불가능한 operation을 남긴다 |
| 답변 후 **멈춘 채로 더 물을 수 있다** | 한 번의 답으로 끝나는 질문이 드물다 |
| 정지 신호를 **예외**로 올린다 | 그냥 반환하면 `_terminal_status`가 미완료 일감을 보고 `LifecycleIntegrityError`를 낸다. 예외는 기존 정지 신호 셋과 같은 어휘다 |
| `interrupted`를 **재사용하지 않는다** | 뼈대는 같지만 뜻이 다르다. 사고로 끊긴 것과 사용자가 멈춘 것이 같은 배너를 쓰면 안 된다 |
| 정지 중에는 **질문과 재개만** | 일감 추가·이의 제기까지 정지 중에 열면 이번 조각이 두 배가 된다. 써보고 불편하면 그때 붙인다 |
