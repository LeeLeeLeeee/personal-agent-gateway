# Persona Agent Teams — 후속 과제 (백엔드 Tasks 1–8 이후)

- 작성일: 2026-07-08
- 출처: `feat/persona-agent-teams` 백엔드 구현(→ `main` `bcc96b5`로 머지)의 태스크별 리뷰 + 전체 브랜치 리뷰(opus)에서 도출한 **비차단(non-blocking) 후속 과제**.
- 관련: `docs/superpowers/plans/2026-07-08-persona-agent-teams.md`(원 플랜), `docs/specs/2026-07-08-observability-audit-log-spec.md`
- 상태: 아래 항목은 모두 **머지 시점에 의도적으로 연기**된 것. 단일 사용자·로컬 MVP 맥락에서 데이터 무결성/보안을 위협하지 않음이 확인됨.

## 범위 메모

- 구현 완료: 백엔드 Task 1–8 (스키마 → PersonaService → Persona API → TeamRunService → Team Run API → 리더 플래닝 → 워커 실행 → 팀 SSE). 147 테스트 통과.
- **UI Task 9–11(Vite React)**: 별도 연기. Codex의 `feat/local-agent-registry` 브랜치가 같은 프론트 파일(`frontend/src/api/client.js`, `Sidebar`, `GatewayApp`)을 수정 중이라, **registry 브랜치가 `main`에 머지된 후** 진행. 원 플랜의 UI 섹션은 이미 React 기준으로 개정됨. 참고 디자인: claude.ai/design 프로젝트 `3f896375-…`의 `Agent Teams.dc.html`.

## 후속 과제

### FU-1. 팀 상태머신 하드닝 (묶음 권장)

두 항목을 하나의 "team status-machine hardening" 작업으로 함께 처리 권장.

- **워커 실패 시 하위 상태 정리** — `src/personal_agent_gateway/team_runtime.py`
  - 현재: `plan_and_execute` 워커 실행 중 `model.complete` 예외 시 `run`/`leader`만 `failed`로 정리되고, 실행 중이던 worker agent는 `running`, 해당 task는 `in_progress`로 영구히 남음(run 상태가 authoritative라 비차단).
  - 수정: 예외 핸들러에서 진행 중이던 worker/task도 함께 `failed`(또는 적절한 상태)로 정리.
- **task 배정 attribution 저장** — `src/personal_agent_gateway/team_runtime.py`, `teams.py`
  - 현재: task 배정 시 `team_tasks.owner_agent_id`, `team_agents.current_task_id`를 저장하지 않음(둘 다 `NULL`). attribution은 `team_messages.sender_agent_id` + `metadata.task_id`로만 간접 추적.
  - 수정: 배정 단계에서 `owner_agent_id`/`current_task_id` 저장. `set_task_status`에 owner 인자 추가 또는 별도 assign 스텝.
  - **연결**: observability-audit-log 스펙의 "persona별 audit attribution"과 직결. 병렬 실행 도입 시 load-bearing.

### FU-2. `create_team_run` 트랜잭션화

- 파일: `src/personal_agent_gateway/teams.py`
- 현재: `Database.execute`가 문장마다 새 커넥션을 열고 커밋(`db.py`) → 공유 트랜잭션 없음. 잘못된 `leader_persona_id`/member id는 `team_runs` insert **이후** `KeyError` → orphan `draft` row(경우에 따라 leader agent까지) 잔존. API는 404를 반환하나 유령 run이 `list_team_runs`에 남음.
- 참고: 기존 `jobs.py`도 동일 패턴이라 신규 회귀는 아님. 단일 사용자에겐 사소한 clutter.
- 수정: 생성 전체를 하나의 트랜잭션으로 묶거나, run insert 전에 모든 persona id를 선검증.

### FU-3. `TeamRuntime.start` 백그라운드화

- 파일: `src/personal_agent_gateway/team_runtime.py`, `src/personal_agent_gateway/api/team_runs.py`
- 현재: `start()`가 `POST /api/team-runs/{id}/start` 핸들러에서 **인라인 await** 실행 → 실제 Codex 실행 시 에이전트당 수 분(기본 timeout 600s) 요청 블록. SSE는 흐르지만 caller가 완료까지 대기하고, `POST /cancel`은 실행 중인 run을 중단하지 못함(비-running run의 상태만 전환).
- 또한 run이 실제로 `running` 상태로 진입하지 않고 `planning → summarizing → completed`로 점프(`running`/`_ACTIVE_RUN_STATUSES`가 run엔 미사용).
- 수정: `JobService`/`JobWorker`처럼 백그라운드 태스크/큐로 이전하여 `/start`는 즉시 반환하고 `/cancel`이 유효하도록. **UI 단계와 함께** 처리 권장.

### FU-4. 멀티 백엔드 지원

- 파일: `src/personal_agent_gateway/app.py` (`_team_model_factory`)
- 현재: persona의 `default_backend`/agent `backend`와 무관하게 항상 `CodexModelClient` 생성(원 플랜 "create CodexModelClient for now" 대로 의도된 것).
- 수정: 멀티 백엔드 도입 시 `persona.default_backend`(예: `openai`, 그리고 registry refactor로 추가될 `claude`)를 반영. **`feat/local-agent-registry`의 AgentRegistry/RuntimeFactory와 정합** 필요.

### FU-5. `plan_and_execute` 이벤트버스 커버리지 테스트

- 파일: `tests/test_team_runtime.py`
- 현재: 실제 `EventBus`를 넘겨 검증하는 테스트는 `planning_only` 경로만 존재. `plan_and_execute`의 `team.task.updated`/`team.message.created` emit은 정적 코드로만 확인되고 이벤트버스 어서션이 없음.
- 수정: `event_bus`를 넘기는 `plan_and_execute` 이벤트 커버리지 테스트 추가.
- 참고(경미): 이벤트 payload가 원 플랜 Task 8 스케치보다 얇음(`title`/`status`/`kind` 생략). 계획된 UI가 `team.*` 이벤트 수신 시 상세를 refetch하므로 무해.

### FU-6. 사소(정리성)

- 파일: `src/personal_agent_gateway/teams.py`
- `TeamAgent`/`TeamTask`의 `started_at`/`finished_at = None` 기본값은 dead(항상 full row로 생성됨).
- `order by created_at asc, rowid asc`의 `rowid` tiebreaker는 `jobs.py` idiom을 넘는 추가(투기적·무해).

## 우선순위 제안

1. **FU-1** (attribution) — observability 스펙과 직결, 병렬 실행 전 필요.
2. **FU-3** (백그라운드화) — UI 단계에서 반드시 함께. `/cancel` 실효성.
3. **FU-4** (멀티 백엔드) — registry 브랜치 머지 후 정합.
4. FU-2 / FU-5 / FU-6 — 여유 시.
