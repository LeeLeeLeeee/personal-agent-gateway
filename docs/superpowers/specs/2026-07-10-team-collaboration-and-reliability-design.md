# Team Run: 동적 협업 메시징 + 실행 신뢰성 설계

작성일: 2026-07-10

## 배경

현재 Team Run(`team_runtime.py`)은 리더가 목표를 flat task 목록으로 분해하고, 워커가 각 task를 **순차·독립적으로** 실행한 뒤 결과를 저장한다. 이는 협업 오케스트레이션이라기보다 배치 실행에 가깝다. 또한 실행 신뢰성에 다음 결함이 있다.

- **동기 블로킹**: `start`가 HTTP 요청 안에서 전체 run을 await한다(`api/team_runs.py:60`). 프런트도 완료까지 대기한다.
- **가짜 취소**: cancel은 DB status만 바꾸고 실행 중 코루틴·서브프로세스는 계속 돈다.
- **전체 실패 전파**: 워커 1개 예외가 최상위 `except`에 잡혀 run 전체가 failed가 된다.
- **backend 반쪽**: `Persona.default_backend → TeamAgent.backend`가 저장되지만 `_team_model_factory`가 `CodexModelClient`를 하드코딩해 무시한다. 팀런은 사실상 codex 전용이다.

이 설계는 두 워크스트림을 함께 다룬다.

- **협업 모델**: 리더 중재 hub-and-spoke 기반 **동적 에이전트 메시징**(라운드 예산으로 상한).
- **신뢰성**: 백그라운드 실행 + 실제 취소 + 부분 실패 격리.
- 부수적으로 **backend-aware 실행**(codex/claude 혼합 팀)을 포함한다.

## 스코프

**포함**
- 라운드 기반 동적 메시징(워커→리더→대상, yield-and-resume)
- 라운드 예산(기본 8) + 에이전트별 재호출 캡(기본 3)
- 리더 최종 종합(하드코딩 summary 대체)
- 백그라운드 실행 + `TeamRunRegistry` 기반 실제 취소
- 부분 실패 격리 + `completed_with_failures` 종료 상태
- backend-aware 팀 실행 팩토리(codex/claude)

**제외 (명시적 비목표)**
- 워커 병렬 실행(⑤) — 순차 유지
- workspace 격리(⑥) — 공용 `config.workspace_root` 유지(순차라 파일 충돌 없음)
- 토큰/reasoning 스트리밍(⑨) — 굵은 lifecycle 이벤트 유지
- 서버 재시작 시 좀비 run 정리 — 별도 작업
- `review_only` 실구현 — 기존 동작(플래닝 후 종료) 유지

## 제약: codex는 one-shot

`CodexModelClient`/`ClaudeModelClient`는 서브프로세스를 1회 실행하고 최종 텍스트만 반환한다. 워커가 실행 도중 런타임에 콜백할 수 없다. 따라서 동적 메시징은 **라운드(yield-and-resume)** 로 모델링한다.

```
워커 실행 → 응답 말미에 구조화된 요청 포함 가능
  → 런타임이 파싱 → 리더 중재로 라우팅 → 답 생성
  → 원 워커를 upstream_session_id로 resume, 답 주입
  → 요청 없으면 그 응답이 task 최종 결과
```

두 클라이언트 모두 `upstream_session_id` resume과 `CancelledError → process.kill()`을 이미 지원하므로, 메시징 라운드·취소·resume은 backend 무관하게 동일하게 동작한다.

## 아키텍처 & 컴포넌트 경계

| 모듈 | 변화 | 책임 |
| --- | --- | --- |
| `team_runtime.py` | 대규모 재작성 | 라운드 기반 메시징 조율. 상태 저장은 `TeamRunService`, 실행은 `ModelClient`에 위임 |
| `run_state.py` | `TeamRunRegistry` 추가 | 실행 중 팀런 task 등록/취소. `SessionRunRegistry`의 형제 |
| `teams.py` | 필드/상태/kind 확장 | budget 카운터, agent 재호출 카운터, upstream_session_id, `completed_with_failures` |
| `db.py` | 컬럼 추가(마이그레이션) | 위 필드 저장 |
| `app.py` | start 백그라운드화, 팩토리 backend-aware | `create_task`+레지스트리 등록; `_team_model_factory` 분기 |
| `api/team_runs.py` | start 논블로킹, cancel 실동작 | 레지스트리 경유 |
| `model_client.py` | 변경 없음 | resume·kill 이미 지원 |
| `frontend/.../GatewayApp/index.jsx` | `handleCreateTeamRun` 논블로킹 | 전체 완료 대기 제거, SSE로 진행 |

**경계 원칙**: `TeamRuntime`은 "어떤 에이전트를 언제·어떤 컨텍스트로 부를지"만 결정한다. 재작성은 `TeamRuntime` 내부 루프에 국한되고 세 경계(Service/Runtime/ModelClient)는 유지한다.

## 데이터 모델 변경

**`team_runs`** 신규 컬럼
- `rounds_budget INTEGER NOT NULL DEFAULT 8` — 메시징 라운드 전역 예산
- `rounds_used INTEGER NOT NULL DEFAULT 0` — 소진량(관측·재생)
- `status` 값 추가: `completed_with_failures` (text 컬럼, enum 제약 없음 — 값 추가만)

**`team_agents`** 신규 컬럼
- `reinvocations INTEGER NOT NULL DEFAULT 0` — 에이전트별 재호출 횟수(캡 3)
- `upstream_session_id TEXT` — codex/claude thread resume용

**`team_messages.kind`** 어휘(컬럼 그대로, 값 규약)
- `plan_note` — 리더 플래닝 노트
- `query` — 워커→리더 요청. metadata: `from_agent`, `task_id`, `question`
- `answer` — 리더→워커 응답. metadata: `to_agent`, `round`
- `agent_output` — task 최종 결과
- `synthesis` — 리더 최종 종합

`recipient_agent_id`가 query/answer에서 실제로 사용된다.

마이그레이션은 `db.py` 초기화에서 `ALTER TABLE ... ADD COLUMN`(존재 확인 후)으로 기존 DB에 무손실 적용한다. 기존 row는 DEFAULT로 채워진다.

`TeamRunStatus` Literal에 `completed_with_failures` 추가.

## 실행 흐름

### 1. Planning
리더를 backend-aware 팩토리로 생성 → `PLANNING_PROMPT` → flat task 배열(JSON) 반환. 관대하게 파싱, task 영속화, `plan_note` 기록, **리더 `upstream_session_id` 저장**(중재·종합 resume용).

### 2. 비실행 모드
`run_mode != "plan_and_execute"`면 기존대로 `completed`로 종료(`planning_only`, `review_only`).

### 3. Execute (순차, task별 라운드 루프)

```
for task in tasks:                       # 순차
    worker = round_robin(workers)
    resp = worker.complete(WORKER_PROMPT) # 최초=fresh; upstream_session_id 저장
    try:
        while (req := parse_needs_info(resp)):
            if run.rounds_used >= run.rounds_budget or agent.reinvocations >= AGENT_CAP:
                resp = worker.resume("추가 상담 불가. best-effort로 최종 산출하라")
                break
            persist_message(kind="query", sender=worker, recipient=leader, question=req.question)
            answer = leader.resume(MEDIATION_PROMPT(req.question, 관련_산출물))
            persist_message(kind="answer", sender=leader, recipient=worker, round=run.rounds_used+1)
            run.rounds_used += 1
            resp = worker.resume(inject=answer)
            agent.reinvocations += 1
        persist_message(kind="agent_output", sender=worker, content=resp, task_id=task.id)
        set_task_status(task, "completed", result=resp)
        set_agent_status(worker, "completed")
    except CancelledError:
        raise                             # 취소는 상위에서 처리
    except Exception as exc:
        set_task_status(task, "failed", error_message=str(exc))
        set_agent_status(worker, "failed")
        # 다음 task 계속 (부분 실패 격리)
```

### 4. 종합
전 task 종료 후 리더 resume으로 최종 `synthesis` 1회 → `run.summary`.

### 5. 종료 상태
- 전부 성공 → `completed`
- 일부 실패 → `completed_with_failures`
- 전부 실패 또는 플래닝 실패 → `failed`
- 취소 → `canceled`

### 중재 방식(결정)
런타임이 대상 task의 raw 결과를 직접 주입하는 대신 **리더 LLM이 답을 생성**하는 hub-and-spoke를 쓴다. 리더가 종합·취사·거절할 수 있고 비용은 `rounds_budget`로 상한. **정산 규칙**: honored query 1회 = 1 round(리더 answer 호출 + 워커 resume 호출 포함), 워커 resume마다 그 에이전트 `reinvocations`+1.

### needs_info 프로토콜
워커 프롬프트에 지시: 다른 팀원 정보가 필요하면 응답 **말미에** 아래 펜스 블록으로만 요청한다.

```json
{"needs_info": {"topic": "...", "question": "..."}}
```

런타임은 응답 말미의 json 펜스 블록만 파싱한다. 없거나 파싱 실패면 요청 없음으로 간주하고 그 응답을 최종 결과로 처리한다.

## 신뢰성

### 백그라운드 실행
- `POST /team-runs/{id}/start`: 전체 실행을 await하지 않는다. `TeamRunRegistry`에 `asyncio.create_task(team_runtime.start(id))`로 등록 후 **즉시 반환**(현재 run 상태). 진행은 SSE(`team.*`).
- 동일 run이 이미 실행 중이면 `409`. 종료 상태 run의 재시작도 거부.
- 프런트 `handleCreateTeamRun`: create → start 호출까지만 하고 전체 완료를 기다리지 않는다. 이후 SSE로 상세 갱신.

### 취소(실동작)
- `TeamRunRegistry`(run_state.py): `attach_task / is_running / cancel / finish`. `SessionRunRegistry` 패턴 재사용.
- `cancel(id)` → `task.cancel()` → `CancelledError`가 `team_runtime.start` 안 `model.complete()` await로 전파 → 클라이언트가 subprocess `kill()`.
- `team_runtime`에 `except asyncio.CancelledError` 명시 분기를 `except Exception` **앞**에 둔다: run=`canceled`, 실행 중 agent/task=`canceled`, 노트 기록, `team.run.canceled` 발행 후 재-raise. (CancelledError는 BaseException이라 기존 `except Exception`엔 안 잡히지만 명시적으로 처리한다.)
- cancel 엔드포인트: 실행 중이면 레지스트리로 취소, 아니면(draft 등 비종료) DB status만 canceled.

### 부분 실패 격리
실행 루프의 task별 try/except로 반영. 한 task 실패가 background task를 깨뜨리지 않고, 최종 상태는 task 상태들로 계산한다.

## backend-aware 실행

`_team_model_factory`가 `agent.backend`로 분기한다(`runtime_factory` 패턴 재사용).

```python
def team_model_factory(agent):
    if agent.backend == "claude":
        return ClaudeModelClient(
            binary=config.claude_binary,
            model=agent.model,
            workspace_root=config.workspace_root,
            effort="high",
            permission_mode=config.claude_permission_mode,
            upstream_session_id=agent.upstream_session_id or None,
        )
    return CodexModelClient(
        binary=config.codex_binary,
        model=agent.model,
        workspace_root=config.workspace_root,
        sandbox=config.codex_sandbox,
        approval_policy=config.codex_approval_policy,
        effort="high",
        timeout_seconds=config.codex_timeout_seconds,
        upstream_session_id=agent.upstream_session_id or None,
    )
```

- 신규 config: `claude_permission_mode`(env `AGENT_CLAUDE_PERMISSION_MODE`, 세션 claude 기본과 동일한 값). 무인 실행이므로 값 선택이 안전에 영향을 준다.
- **안전 메모(deferred ⑩)**: 팀 워커는 승인 게이트 없이 무인 실행된다. 이 설계는 이를 바꾸지 않으며, 별도 작업으로 승인/안전 모드를 다룬다.
- 리더/워커별로 매 라운드 팩토리를 호출하되 `upstream_session_id`로 같은 thread를 resume해 컨텍스트를 잇는다.

## 에러 처리 & 엣지 케이스

- 플래닝 비-JSON/빈 응답 → 관대 파싱 실패 시 "JSON 배열만" 1회 재요청, 그래도 실패면 run=`failed`+에러 노트(크래시 없음).
- `plan_and_execute`인데 워커 0명 → `failed`+명확 메시지(기존 유지).
- 워커 모델 에러(비정상 종료/타임아웃) → task=`failed`, agent=`failed`, `error_message` 저장, 다음 task 계속.
- malformed needs_info 블록 → 요청 없음으로 간주, 최종 결과로.
- needs_info가 실패/부재 대상 지목 → 중재 프롬프트에 가용 산출물만 포함, 리더가 "정보 없음"으로 답 → 워커 best-effort.
- 예산/캡 소진 → 거절 + best-effort resume.
- claude backend인데 `claude_binary` 부재 → `RuntimeError("Claude binary not found")` → run failed로 표출.
- 동일 run 동시 start → 409.
- 플래닝 중 취소 → run=`canceled`, 리더=`canceled`.

## 테스트 전략 (TDD)

`TeamRuntime`은 `model_factory`가 주입 가능하다. 스크립트된 fake `ModelClient`(호출 순서대로 정해진 응답 반환, needs_info 블록 포함)로 결정적으로 검증한다.

**team_runtime 단위**
- happy path `plan_and_execute`(플래닝→워커→synthesis→`completed`)
- needs_info 1회 → 리더 중재 → 워커 resume → 최종(`rounds_used`+1, `reinvocations`+1, query/answer 메시지 영속화)
- 예산 소진 → best-effort 거절 경로
- 에이전트 캡(3) 도달 → 거절
- 부분 실패: 워커 1개 예외 → 그 task=failed, 나머지 완료 → `completed_with_failures`
- 전부 실패 → `failed`
- 플래닝 비-JSON → 재요청 1회 → 실패 시 `failed`
- 취소: fake client가 event 대기로 장기실행 흉내 → `task.cancel()` → run=`canceled`, in-progress task=`canceled`, CancelledError가 `except Exception`에 안 먹히는지

**factory/그 외**
- backend-aware 팩토리: `agent.backend=="claude"` → `ClaudeModelClient` 생성(단위)
- 종료 상태 계산 로직 단위
- API: start 즉시 반환(블로킹 X), 실행 중 cancel 전이, 이중 start→409
- DB 마이그레이션: 신규 컬럼·기존 row 기본값
- 프런트: `handleCreateTeamRun`가 전체 완료를 안 기다림(mock), `completed_with_failures` 배지 렌더

CLAUDE.md 원칙대로 테스트를 먼저 작성한 뒤 구현한다.

## 구현 순서

신뢰성 토대를 먼저 깔고 협업을 얹는다.

1. DB 마이그레이션 + `teams.py` 필드/상태/kind 확장 (+ 단위 테스트)
2. `TeamRunRegistry` + 백그라운드 start + 실제 취소 (+ API·단위 테스트)
3. 부분 실패 격리 + `completed_with_failures` 종료 상태 계산
4. backend-aware `_team_model_factory` + config `claude_permission_mode`
5. 라운드 기반 동적 메시징 루프(needs_info 파싱, 리더 중재, resume, 예산/캡)
6. 리더 최종 종합(synthesis)
7. 프런트 `handleCreateTeamRun` 논블로킹 + `completed_with_failures` 렌더
