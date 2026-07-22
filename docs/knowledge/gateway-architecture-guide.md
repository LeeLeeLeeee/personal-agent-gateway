---
title: Personal Agent Gateway 아키텍처 가이드
type: knowledge
domain: personal-agent-gateway
feature: architecture-overview
status: active
aliases:
  - PAG 아키텍처
  - Gateway 구성 원칙
tags:
  - architecture
  - lifecycle
  - persistence
  - frontend
updated_at: 2026-07-22
---

# Personal Agent Gateway 아키텍처 가이드

이 문서는 기능이 어떤 책임 경계로 나뉘며 왜 이 구성이 로컬 agent 실행에 효율적인지 설명한다. 화면별 동작은 [기능 가이드](gateway-feature-guide.md)를 참고한다.

## 다섯 구성 요소

```text
1. React UI
   상태를 조회하고 실행·승인·답변을 입력한다.

2. FastAPI Gateway
   OTP session, same-origin, intake 상태를 확인하고 API를 service로 연결한다.

3. Execution Services
   Chat Runtime, Job Worker, Hook Runner, Team Runtime이 각 lifecycle을 소유한다.

4. Policy Services
   Agent·Persona·Rules·Spaces가 누가 어떤 모델로 어디까지 작업할지 결정한다.

5. Local State
   SQLite, JSONL transcript, artifact와 team workspace가 상태와 결과를 보존한다.
```

## 실행 소유권

Chat, Job, Hook, Team Run을 하나의 거대한 queue에 넣지 않는다.

| 실행 경로 | 소유자 | 분리하는 이유 |
| --- | --- | --- |
| Chat | `AgentRuntime` | 대화 session 연속성, streaming과 shell approval을 한 runtime이 책임진다. |
| Job | `JobWorker`와 capability runner | 구조화된 입력, 승인, retry와 artifact 생성을 일관되게 관리한다. |
| Hook | `HookRunner` | 외부 source cursor, dedup과 대상 실행 연결을 책임진다. |
| Team Cycle | `TeamCycleDispatcher`, `TeamRunOrchestrator`, `TeamRuntime` | 장기 Run, Cycle queue, 역할별 Task와 복구 상태를 관리한다. |

각 실행 경로는 분리되어 중복 실행을 막지만 인증, Agent Registry, Persona, Rules, Spaces, SQLite, Artifact Store와 Event Bus는 공통으로 사용한다.

## Backend 구성

FastAPI process 하나가 진입점과 background lifecycle을 소유하고 내부는 역할에 따라 분리된다.

```text
FastAPI App / Lifespan
├── API Routers
│   └── auth, chat, jobs, schedules, hooks, teams, operations ...
├── Interactive Runtime
│   └── AgentRuntime -> CodexModelClient / ClaudeModelClient
├── Automation Control Plane
│   ├── JobWorker -> capability runners
│   ├── SchedulerLoop -> Job queue
│   └── HookLoop -> HookRunner -> Persona / Team Cycle
├── Team Orchestration
│   └── TeamCycleLoop -> Dispatcher -> Orchestrator -> TeamRuntime
├── Policies
│   └── Agent Registry, Personas, Rules, Spaces, Approval, Intake
└── Persistence
    └── SQLite, TranscriptStore, ArtifactStore, Team Workspace, Backup
```

서버 시작 시 lifespan이 중단 상태를 정규화하고 Team Cycle, Job, Schedule, Hook 순서로 background component를 기동한다. 종료 시에는 역순으로 중지해 생산자가 consumer 종료 후 새 작업을 남기는 상황을 줄인다.

### 주요 모듈

| 파일 | 역할 |
| --- | --- |
| `app.py` | FastAPI app 조립, background lifecycle, route와 정적 파일 서빙 |
| `agents.py`, `model_client.py` | Codex·Claude 탐지, 모델 검증과 CLI adapter |
| `runtime.py`, `runtime_factory.py` | Chat message 처리와 session별 runtime 생성 |
| `transcript.py` | session JSONL 저장·검색·전환 |
| `auth.py`, `auth_store.py` | OTP, recovery code와 setup token |
| `db.py`, `migrations.py` | SQLite connection과 schema migration |
| `personas.py`, `teams.py` | Persona, Team, Run, Agent, Task와 snapshot 상태 |
| `team_cycle_*`, `team_runtime.py` | Cycle queue, 복구, Leader 계획과 Member 실행 |
| `team_delivery.py` | worktree 결과 commit, repository 반영과 conflict 해결 |
| `hooks.py`, `hook_runner.py`, `sources/` | 외부 source polling, dedup과 target 실행 |
| `space_policies.py`, `rule_sets.py` | 접근 범위와 실행 규칙의 계층·snapshot 관리 |
| `jobs.py`, `job_worker.py`, `runners/` | Job 상태 전이, queue와 capability 실행 |
| `artifacts.py`, `backup.py`, `audit.py` | 결과 파일, backup과 변경 추적 |
| `api/` | domain별 얇은 HTTP router와 payload 변환 |

## Frontend 구성

Frontend는 `frontend/`의 Vite React 앱이다.

```text
frontend/src/
├── api/          # /api/* client
├── hooks/        # session, bootstrap, Team Run controller
├── lib/          # time, timeline, cron, artifact 변환
└── components/
    ├── atoms/
    ├── molecules/
    ├── organisms/
    ├── templates/
    ├── containers/
    └── providers/
```

- `atoms`: Button, Field, StatusBadge 같은 단일 UI 단위
- `molecules`: Composer, AuthCard, TeamRunCard 같은 작은 조합
- `organisms`: 화면 단위 View와 복합 상호작용
- `templates`: AppShell, AuthTemplate 같은 레이아웃 골격
- `containers`: GatewayApp처럼 API, SSE와 전역 화면 상태를 묶는 레이어
- `hooks`: session과 Team Run의 비동기 상태 전이를 UI 렌더링에서 분리

개발 중에는 Vite proxy가 `/api/*`를 FastAPI로 전달한다. build 후에는 FastAPI가 React 정적 파일과 API를 같은 origin에서 제공한다.

## 상태와 파일의 책임

| 저장소 | 책임 |
| --- | --- |
| SQLite | Job, Schedule, Hook, Team Run, Cycle, Task, 정책과 audit 같은 구조화 상태 |
| JSONL transcript | Chat 대화와 CLI event처럼 순서가 중요한 append 중심 기록 |
| Artifact files | 결과 파일 본문. SQLite에는 type, 크기, 경로 같은 검색 metadata 저장 |
| Team workspace documents | 사람이 읽는 보고서와 결정 기록. 실행 상태 자체는 DB가 소유 |
| Backup directory | DB, 인증 설정과 로컬 상태의 복구 가능한 snapshot |

## 효율성을 위한 설계

| 설계 | 동작 방식 | 효과 |
| --- | --- | --- |
| **Local-first** | agent 로그인, workspace, transcript, DB와 artifact를 사용자 PC에 둔다. | 별도 모델 credential 서버와 원격 작업 저장소가 필요 없다. |
| **Single origin** | build된 React UI를 FastAPI가 API와 함께 제공한다. | CORS·배포가 단순하고 OTP cookie를 같은 origin에서 사용한다. |
| **명확한 실행 소유권** | Chat, Job, Hook, Team Runtime이 각자의 lifecycle만 변경한다. | 중복 실행과 상태 충돌을 줄인다. |
| **Snapshot 기반 실행** | Persona, Rules, Space 정책을 Run 또는 Cycle 시작 시 고정한다. | 실행 도중 설정이 바뀌어도 당시 조건과 결과를 설명할 수 있다. |
| **Cycle 기반 장기 협업** | Team Run은 유지하고 목표와 Task는 Cycle별로 분리한다. | 팀을 다시 만들지 않고 반복 작업과 이전 문맥을 이어간다. |
| **격리된 쓰기와 전달** | 격리 workspace 또는 Git worktree에서 작업하고 Delivery 단계에서 반영한다. | 대상 repository의 오염을 줄이고 적용 시점을 사용자가 통제한다. |
| **복구 가능한 loop** | lifespan이 worker, scheduler, hook, team cycle을 관리하고 재시작 시 reconcile한다. | 프로세스 종료 후에도 대기 작업과 중단 상태를 보존한다. |
| **Human in the loop** | Leader가 꼭 필요한 질문만 모아 `waiting_for_user`에서 요청한다. | 질문 폭주를 줄이고 독립 Task는 계속 진행한다. |
| **실시간 delta 갱신** | Event Bus와 SSE가 변경 event만 전달한다. | 전체 재조회와 과거 알림 재생을 줄인다. |

## 관련 설계 문서

- [서비스 도메인 맵](2026-07-15-service-domain-map.md)
- [Runtime 도메인 관계 맵](2026-07-16-runtime-domain-relationship-map.md)
- [자동화 lifecycle ADR](../adr/2026-07-15-automation-lifecycle.md)
- [Team Run 사용자 결정 ADR](../adr/2026-07-16-team-run-batched-user-decisions.md)
- [Hook·Team·메일 workspace ADR](../adr/2026-07-16-hook-team-mail-workspace.md)
