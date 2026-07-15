---
title: Personal Agent Gateway 개발 PM 유지보수성 진단
type: report
domain: personal-agent-gateway
feature: development-pm-assessment
status: active
aliases:
  - 개발 PM 개선안
  - 유지보수 성능 개선
  - 기술 부채 우선순위
  - PAG 개발 진단
tags:
  - development-pm
  - maintainability
  - performance
  - security
  - reliability
updated_at: 2026-07-15
---

# Personal Agent Gateway 개발 PM 유지보수성 진단

## 결론

현재 코드는 Chat과 Team Run의 복잡한 상태 복구를 테스트 가능한 서비스로 분리해 놓은 점이 강하다. 반면 **인증 session 검증**, **background worker lifecycle**, **제품 설명과 runtime 계약 일치**가 아직 기반 품질을 충족하지 못한다.

개발 순서는 새 기능보다 다음 세 가지가 먼저다.

1. 발급한 session만 허용하고 만료·폐기할 수 있는 인증 경계를 만든다.
2. JobWorker와 SchedulerLoop를 app lifespan에 연결하고 예외 후에도 계속 동작하게 만든다.
3. 실제로 제공하지 않는 review/concurrency 계약을 제거하거나 구현한다.

이 세 가지가 끝나기 전에는 Schedule, Job, 외부 Tunnel 기능을 “신뢰 가능한 자동화”로 확장하면 안 된다.

## 기술 기반의 강점

- Backend는 service와 model client를 주입할 수 있어 fake 기반 상태 전이 테스트가 가능하다.
- Chat과 Team Run은 메모리 task registry를 따로 두어 동일 실행의 중복 진입을 차단한다.
- Team Run은 재시작 시 stale 상태를 `interrupted`로 바꾸고 사용자 Resume을 요구한다.
- Persona, Rules, model options를 snapshot으로 남겨 실행 도중 설정 변경의 영향을 차단한다.
- workspace, artifact store, team document에 path containment 검사가 있다.
- SQLite는 foreign key와 WAL을 활성화했고 session activity에는 조회 index가 있다.
- Backend에 338개 테스트가 수집되며 frontend도 화면별 component/unit 테스트가 있다.

## 위험 우선순위

| ID | 등급 | 관찰 사실 | 영향 | 권장 방향 |
| --- | --- | --- | --- | --- |
| DEV-01 | Critical | 보호 API가 `agent_session` cookie의 존재만 확인한다. 발급값 저장, 서명 검증, 만료가 없다. | Tunnel URL을 아는 공격자가 임의 cookie로 로컬 실행 API에 접근할 수 있다. | 서버측 session store 또는 서명·만료 cookie, revoke, idle timeout, login rate limit을 구현한다. |
| DEV-02 | Critical | `JobWorker`는 `app.state`에만 저장되고 `start()`가 호출되지 않는다. `SchedulerLoop`는 생성되지 않는다. | queued job과 due schedule이 실행되지 않아 핵심 제품 계약이 깨진다. | FastAPI lifespan에서 worker/scheduler 시작·종료·복구를 원자적으로 관리한다. |
| DEV-03 | High | Team Run `review_only`가 planning 후 종료되고 `max_workers`는 순차 실행 loop에 사용되지 않는다. | 사용자 기대와 결과·시간·비용이 달라진다. | 단기에는 UI에서 숨기거나 정확히 설명하고, 장기에는 별도 review flow와 bounded concurrency를 구현한다. |
| DEV-04 | High | `JobWorker._run_loop()`가 `run_one()` 예외를 처리하지 않는다. | runner 예외 한 번으로 worker task가 종료돼 이후 모든 job이 멈출 수 있다. | job failed 전이, structured error, loop 생존, poison job 격리를 보장한다. |
| DEV-05 | High | 전역 exception handler가 예외를 기록하지 않고 500만 반환한다. audit spec도 구현되지 않았다. | 원격 사용 중 실패 원인을 재현하거나 사고 범위를 판단하기 어렵다. | request ID, structured log, durable audit, redaction을 먼저 구현한다. |
| DEV-06 | High | Full Access 운영 기준에 있는 CSRF/Origin 확인, session revoke, emergency stop, checkpoint/diff가 없다. | 사용자 실수나 탈취 session의 피해를 멈추고 복구하기 어렵다. | 기존 security operating model의 최소 MVP를 release gate로 전환한다. |
| DEV-07 | Medium | session 목록·검색은 각 JSONL 전체를 다시 읽고, history/activity/list API는 pagination이 없다. | session과 event가 늘면 sidebar, 검색, 초기 로딩 비용이 선형 이상으로 증가한다. | session metadata index, cursor pagination, retention 정책을 도입한다. |
| DEV-08 | Medium | Team SSE 한 건마다 detail 4개 API와 documents workspace scan을 다시 수행한다. | task/message가 많거나 파일이 많은 run에서 요청 폭증과 화면 지연이 생긴다. | aggregate detail endpoint, event delta 반영, document scan debounce/cache를 적용한다. |
| DEV-09 | Medium | `GatewayApp` 1,168줄에 30개 내외 상태, fetch, SSE, 명령 handler가 집중돼 있다. `app.py`도 route와 composition이 함께 있다. | 기능 추가 시 회귀 범위와 리뷰 비용이 빠르게 커진다. | 도메인별 controller hook/container와 chat router를 단계적으로 추출한다. |
| DEV-10 | Medium | API client가 non-2xx를 대부분 `null` 또는 `[]`로 바꾸고 UI는 generic toast를 표시한다. | 상태 충돌, validation, 인증 만료를 구분하지 못해 복구 UX와 디버깅이 나빠진다. | typed error envelope과 status별 UI 처리 규칙을 만든다. |
| DEV-11 | Medium | SQLite migration은 schema version 없이 컬럼 존재 검사로 누적되며 주요 list/filter 컬럼 index가 부족하다. | migration 순서 검증과 데이터 증가 대응이 어려워진다. | schema version table, 순차 migration, query-plan 기반 index를 도입한다. |
| DEV-12 | Medium | Artifact register는 의도적으로 workspace 밖의 임의 readable file도 허용한다. | Full Access Mode에서는 편리하지만 인증 경계 실패 시 노출 범위가 로컬 PC로 확대된다. | Restricted/Full Access mode를 명시하고 외부 path 등록을 mode와 audit에 연결한다. |

## Critical 근거 확인

### Session 검증

- 로그인은 random `agent_session` 값을 cookie로 발급한다.
- 이후 `_require_agent_session`과 5개 API router의 `require_session`은 값이 비어 있는지만 확인한다.
- 임시 app 진단에서 cookie 없음은 `401`, 임의 non-empty cookie는 protected `/api/settings`에서 `200`이었다.

완료 조건은 “OTP를 거친 브라우저”가 아니라 **서버가 현재 유효하다고 판단한 session만 접근 가능**한 상태다.

### Background lifecycle

- `JobWorker.start/stop`과 `SchedulerLoop.start/stop` 구현은 존재한다.
- repository 전체 검색에서 app startup/shutdown 호출이 없다.
- 임시 app 진단에서 `app.state.job_worker._task is None`이고 `scheduler_loop` state도 없었다.

완료 조건은 class 존재가 아니라 gateway startup 후 queue consumer와 due schedule loop가 실제 실행되고 shutdown에서 정리되는 상태다.

## 유지보수 구조 개선

### Backend 경계

| 현재 집중점 | 목표 경계 | 첫 단계 |
| --- | --- | --- |
| `app.py`가 composition과 Chat route를 함께 소유 | `app.py`는 composition root, `api/chat_sessions.py`는 HTTP 계약 | 기존 closure가 의존하는 service 묶음을 app state로 명시하고 route를 이동한다. |
| API마다 auth dependency 중복 | 공통 session principal dependency | `SessionPrincipal`을 반환하고 모든 protected router가 하나를 사용한다. |
| 수동 migration | versioned migration runner | `schema_migrations`와 순번 파일을 추가하고 legacy fixture로 업그레이드 테스트한다. |
| service별 raw SQL 반복 | service 소유 SQL은 유지하되 query contract 문서화 | pagination과 index가 필요한 list query부터 request/response contract를 고정한다. |

대규모 repository pattern이나 ORM 교체는 현재 규모에서 우선순위가 아니다. 먼저 lifecycle·인증·계약을 고친 뒤 반복되는 경계만 추출한다.

### Frontend 경계

`GatewayApp`을 한 번에 재작성하지 않는다. 다음 단위로 이동한다.

1. `useGatewayBootstrap`: auth, status, agents, active session 초기화.
2. `useSessionController`: history/activity/SSE reconciliation과 chat 명령.
3. `useTeamRunController`: 목록, detail, documents, team SSE와 resume/retry.
4. 화면별 query hook: personas, teams, rules, jobs, schedules, artifacts.
5. 공통 `ApiError`: status, detail, retryability를 유지한다.

각 추출은 기존 component test가 같은 행동을 보장하는 작은 변경으로 진행한다.

## 성능 개선 계획

### 먼저 측정할 지표

- session 10/100/1,000개에서 `GET /api/sessions`와 search p50/p95.
- session event 100/1,000/10,000개에서 history/activity 응답 크기와 parse 시간.
- Team Run task 10/100개, document 100/1,000개에서 SSE 1건당 API 호출 수와 render 시간.
- jobs/artifacts/team runs 1,000개에서 list query 시간과 SQLite query plan.
- CLI 실행 시간과 gateway overhead를 분리한 end-to-end duration.

### 개선 순서

1. list/history/activity에 cursor와 기본 limit을 추가한다.
2. transcript마다 title, updated_at, count, status를 별도 metadata로 유지해 session list가 JSONL 전체를 읽지 않게 한다.
3. `teamRunDetail`을 run/agents/tasks/messages/doc summary의 단일 read model endpoint로 제공한다.
4. SSE event payload로 바뀐 task/agent만 client state에 반영하고 full refetch는 terminal/reconnect 때만 수행한다.
5. 실제 query plan으로 `team_run_id`, `status`, `created_at`, `next_run_at` index를 추가한다.
6. retention/archiving으로 activity와 job event가 무한히 증가하지 않게 한다.

## 신뢰성 및 운영 개선

| 영역 | 최소 구현 | 검증 |
| --- | --- | --- |
| App lifespan | DB init → interrupted recovery → worker/scheduler start → request serve → graceful stop | startup/shutdown integration test |
| Job recovery | startup 때 `running`은 failed/interrupted, 기존 `queued`는 재enqueue | restart fixture에서 중복/유실 없음 |
| Schedule claim | due schedule을 한 번만 claim하고 다음 시각 갱신 | 동일 tick/restart에서 duplicate job 없음 |
| Health | process, DB write/read, worker alive, scheduler alive, CLI availability 구분 | `/health/live`, `/health/ready` contract test |
| Logging | request/run/job/session/team correlation ID, redaction, stack trace local-only | secret fixture가 log에 남지 않음 |
| Emergency stop | session/team/job queue와 실행 process를 한 명령으로 중단 | 장기 실행 fake들을 모두 cancel |
| Backup | SQLite consistent backup + auth/session/artifact manifest, restore dry-run | 임시 data root round-trip |

## 테스트와 품질 게이트

현재 테스트 수는 강점이지만 “실제 app에서 background component가 기동되는가” 같은 composition test가 빠져 있었다. 다음 gate를 추가한다.

- 임의 session cookie가 모든 protected endpoint에서 `401`인지.
- login session 만료, idle timeout, logout, revoke 후 `401`인지.
- TestClient lifespan 안에서 worker/scheduler가 alive이고 밖에서는 stopped인지.
- queued job이 terminal 상태로 전이하고 runner exception 뒤 다음 job도 실행되는지.
- due schedule이 한 번만 job을 생성하는지.
- UI에 노출하는 run mode와 worker 수가 runtime contract test와 일치하는지.
- 100개 session/1,000개 event fixture의 성능 budget test.
- frontend API error가 400/401/409/500을 서로 다르게 표시하는지.
- CI에서 backend tests, frontend tests, Ruff, production build를 필수화한다.

## 권장 기술 의사결정

### 지금 결정

- 인증 session source of truth를 SQLite로 둘지 signed cookie + revocation table로 둘지.
- Job/Schedule이 single-process 전용인지, 재시작 후 queued job을 자동 재개할지.
- `review_only`와 `max_workers`를 단기 제거할지 즉시 구현할지.
- Restricted/Full Access Mode를 제품 설정으로 분리할지.

### 나중에 결정

- ORM 도입, 외부 queue, hosted telemetry, multi-instance worker는 현재 필요하지 않다.
- 실제 load 측정 없이 React state library나 DB 교체를 먼저 하지 않는다.

## 개발 PM 완료 정의

- 제품이 표시하는 기능은 app composition test에서 실제 실행된다.
- protected API는 유효하고 만료되지 않은 principal 없이는 접근할 수 없다.
- background component 장애가 다른 작업을 멈추지 않으며 사용자에게 원인이 남는다.
- restart 후 Chat, Team, Job, Schedule 각각의 상태가 정의된 recovery 정책으로 수렴한다.
- 사용량 증가 경로에는 pagination, index, retention 또는 명시적 상한이 있다.
- 새 도메인 기능은 source of truth, lifecycle, error surface, test, 운영 문서를 함께 제공한다.

## 관련 문서

- [서비스 도메인 지도](../knowledge/2026-07-15-service-domain-map.md)
- [기획 PM 사용성·기능 기회](2026-07-15-product-pm-usability-opportunities.md)
- [통합 개선 로드맵](../todo/2026-07-15-service-improvement-roadmap.md)
- [Full Access Mode Security Operating Model](../knowledge/2026-07-08-full-access-security-operating-model.md)
- [Observability and Audit Log Spec](../specs/2026-07-08-observability-audit-log-spec.md)

## 근거

- `src/personal_agent_gateway/api/auth.py`
- `src/personal_agent_gateway/app.py`
- `src/personal_agent_gateway/job_worker.py`
- `src/personal_agent_gateway/scheduler_loop.py`
- `src/personal_agent_gateway/team_runtime.py`
- `src/personal_agent_gateway/transcript.py`
- `src/personal_agent_gateway/db.py`
- `src/personal_agent_gateway/events.py`
- `frontend/src/components/containers/GatewayApp/index.jsx`
- `frontend/src/api/client.js`
- `python -m pytest --collect-only -q` → 338 tests collected
