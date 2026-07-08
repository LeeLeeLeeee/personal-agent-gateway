# Persona-Based Agent Teams Spec

- 작성일: 2026-07-08
- 대상: `personal-agent-gateway`
- 범위: persona library, team run, leader/worker agent sessions, task board, team activity stream
- 관련 문서:
  - `docs/knowledge/2026-07-08-full-access-security-operating-model.md`
  - `docs/specs/2026-07-07-live-activity-viewer-chat-redesign-spec.md`
  - `docs/specs/2026-07-06-personal-agent-gateway-capabilities-technical-spec.md`

## 1. 배경 / 문제

현재 personal-agent-gateway는 단일 local agent session을 브라우저에서 제어하고, Codex 실행 activity를 SSE로 보여주는 구조다. 다음 단계에서는 사용자가 하나의 목표를 여러 AI agent에게 나눠 맡기고, 각 agent가 고유한 역할 관점으로 판단하며 작업하는 "팀 단위 AI" 경험이 필요하다.

핵심은 단순 병렬 worker가 아니다. 각 agent는 사용자가 지정한 persona를 session 시작 시점에 주입받고, 이후 응답/판단/작업 방식이 그 persona 기준으로 고정되어야 한다.

## 2. 목표 / 성공 기준

- 사용자는 재사용 가능한 persona를 만들고 수정할 수 있다.
- 사용자는 Team Run 생성 시 goal, leader persona, member personas, run mode, concurrency를 지정할 수 있다.
- Team Run 시작 시 각 agent session은 persona snapshot을 받는다.
- Leader agent는 goal을 task로 분해하고 member에게 할당한다.
- Worker agent는 자기 persona 기준으로 task를 수행하고 결과를 보고한다.
- UI는 Team Run 목록, 생성 화면, Persona Library, Team Run Detail을 기존 personal-agent-gateway UI 흐름 안에 자연스럽게 표시한다.
- Team activity는 기존 SSE live activity stream과 같은 관찰 모델을 사용한다.
- 초기 스펙에서는 per-action approve/deny를 제외한다.
- Full Access 운영 모델에서는 persona별 권한 차단보다 persona별 audit attribution을 우선한다.

## 3. 비목표

- ClawTeam을 runtime dependency로 도입하지 않는다.
- tmux 의존 구조를 만들지 않는다. Windows에서 동작해야 한다.
- multi-user organization, invite, role permission system을 만들지 않는다.
- agent 간 자유로운 무제한 대화를 먼저 만들지 않는다. MVP에서는 gateway가 task/message를 중계한다.
- 자동 merge, 자동 production deploy, git push 자동화를 MVP에 포함하지 않는다.
- approve/deny UI를 Team Run의 필수 흐름으로 넣지 않는다.

## 4. 핵심 개념

### Persona

사용자가 정의하는 agent의 역할, 전문성, 판단 기준, 제약 조건이다.

Persona는 말투 프리셋이 아니라 작업 판단 기준이다.

예시:

- Tech Lead: 작업 분해, 의존성 관리, 최종 통합 판단
- Frontend Designer: UI/UX, 레이아웃, 반응형, 시각적 완성도 검토
- Backend Engineer: API, 데이터 모델, 실행 안정성
- QA Tester: 테스트, 회귀 위험, 실패 케이스 검증
- Release Manager: 빌드, 배포, 릴리즈 체크리스트

### Agent Session

Persona가 주입된 실행 단위다.

Team Run이 시작될 때 각 agent session은 persona snapshot을 받는다. 실행 중 원본 persona가 수정되더라도 이미 실행 중인 session의 persona는 바뀌면 안 된다.

### Team Run

하나의 사용자 goal을 여러 agent session이 함께 수행하는 실행 단위다.

흐름:

```text
User goal
  -> leader persona 선택
  -> member personas 선택
  -> leader agent session 생성
  -> worker agent sessions 생성
  -> leader가 task 분해
  -> worker가 persona 기준으로 task 수행
  -> leader가 결과 요약/통합
```

### Team Task

Team Run 안에서 agent에게 할당되는 작업이다.

상태:

```text
pending -> in_progress -> completed
                     \-> blocked
                     \-> failed
```

### Team Message / Activity

Agent 간 메시지, task 상태 변화, Codex output, final summary를 시간순으로 기록한다.

Activity는 기존 `/api/events` SSE stream을 통해 live UI에 반영한다.

## 5. Product Model

### Persona fields

```text
id
name
role
description
responsibilities_json
constraints_json
default_backend
default_model
created_at
updated_at
```

### Team Run fields

```text
id
goal
status: draft | planning | running | summarizing | completed | failed | canceled
run_mode: planning_only | plan_and_execute | review_only
leader_agent_id
max_workers
workspace_root
summary
error_message
created_at
started_at
finished_at
updated_at
```

### Team Agent fields

```text
id
team_run_id
name
role
persona_id
persona_snapshot_json
backend
model
status: pending | running | waiting | completed | failed | canceled
workspace_path
current_task_id
started_at
finished_at
created_at
updated_at
```

### Team Task fields

```text
id
team_run_id
title
description
owner_agent_id
status: pending | in_progress | blocked | completed | failed
result
error_message
created_at
updated_at
started_at
finished_at
```

### Team Message fields

```text
id
team_run_id
sender_agent_id
recipient_agent_id
kind: note | task_update | agent_output | final_summary | error
content
metadata_json
created_at
```

## 6. API

### Persona API

```text
GET    /api/personas
POST   /api/personas
GET    /api/personas/{id}
PATCH  /api/personas/{id}
DELETE /api/personas/{id}
```

`POST /api/personas`

```json
{
  "name": "Frontend Designer",
  "role": "UI/UX and frontend review",
  "description": "Reviews interface clarity, layout, responsive behavior, and visual consistency.",
  "responsibilities": ["UI structure", "responsive layout", "visual consistency"],
  "constraints": ["Do not change backend APIs unless assigned"],
  "default_backend": "codex",
  "default_model": "default"
}
```

### Team Run API

```text
GET  /api/team-runs
POST /api/team-runs
GET  /api/team-runs/{id}
POST /api/team-runs/{id}/start
POST /api/team-runs/{id}/cancel
GET  /api/team-runs/{id}/agents
GET  /api/team-runs/{id}/tasks
GET  /api/team-runs/{id}/messages
```

`POST /api/team-runs`

```json
{
  "goal": "Design persona-based Agent Teams for personal-agent-gateway.",
  "leader_persona_id": "tech-lead",
  "member_persona_ids": ["backend-engineer", "frontend-designer", "qa-tester"],
  "run_mode": "plan_and_execute",
  "max_workers": 3
}
```

## 7. Event Contract

Team 기능은 기존 `EventBus`를 확장한다.

Event examples:

```json
{"type":"team.run.created","team_run_id":"...","goal":"..."}
{"type":"team.agent.started","team_run_id":"...","agent_id":"...","persona":"Tech Lead"}
{"type":"team.task.created","team_run_id":"...","task_id":"...","title":"Define data model"}
{"type":"team.task.updated","team_run_id":"...","task_id":"...","status":"in_progress"}
{"type":"team.message.created","team_run_id":"...","sender":"Frontend Designer","kind":"task_update"}
{"type":"team.run.completed","team_run_id":"...","summary":"..."}
```

Codex raw events from worker agents may continue to use `codex.event`, but should include `team_run_id`, `agent_id`, and `task_id` when emitted from Team Run execution.

## 8. Persona Prompt Contract

Agent session 시작 시 system prompt는 persona snapshot과 team context를 포함한다.

Template:

```text
You are an agent in a personal-agent-gateway Team Run.

Persona:
- Name: {name}
- Role: {role}
- Description: {description}
- Responsibilities:
{responsibilities}
- Constraints:
{constraints}

Team context:
- Team run id: {team_run_id}
- Goal: {goal}
- Leader: {leader_name}
- Teammates: {teammates}
- Assigned task: {task_title}

Protocol:
- Work from your persona's judgment criteria.
- Stay within your assigned task unless the leader asks otherwise.
- Report concise status, result, changed files, and verification evidence.
- Do not ask for per-command approval in this mode.
```

## 9. UI / UX Integration

가장 중요한 조건은 Agent Teams가 현재 personal-agent-gateway의 UI/UX에 자연스럽게 녹는 것이다.

새 기능은 별도 앱처럼 보이면 안 된다. 기존 chat/session/jobs/activity/artifacts 중심 control console의 상위 실행 개념으로 붙어야 한다.

### Agent Teams Home

- 최근 Team Run 목록
- running/completed/failed 구분
- New Team Run 진입
- 각 run에 goal, status, leader, members, task progress 표시
- 기존 session/job list와 시각적 리듬을 맞춘다.

### New Team Run

- Goal 입력
- Leader persona 선택
- Member personas 선택
- Run mode 선택:
  - Planning only
  - Plan + execute
  - Review only
- Max workers/concurrency
- Workspace 표시
- 기존 job proposal 또는 capability 실행 패널과 조작감이 크게 다르지 않아야 한다.

### Persona Library

- Persona 목록
- Persona 생성/수정
- 필드:
  - Name
  - Role
  - Description
  - Responsibilities
  - Constraints
  - Optional backend/model
- 설정/프리셋 관리처럼 느껴져야 한다.

### Team Run Detail

- Goal, status, elapsed time, leader, run mode
- Agent lanes:
  - persona name
  - role
  - status
  - current task
- Task board:
  - pending / in_progress / blocked / completed / failed
- Live activity stream:
  - agent별 상태 변화와 메시지
- Result/summary area:
  - agent별 보고
  - final leader summary
  - verification evidence
- 기존 activity viewer, job detail, artifact preview 패턴과 연결한다.

## 10. Security / Full Access Relation

Team 기능은 Full Access Mode와 함께 쓰일 가능성이 높다.

초기 정책:

- Team Run 내부 per-action approve/deny는 제외한다.
- persona별 권한 차단은 MVP에서 강제하지 않는다.
- 대신 persona별 audit attribution을 반드시 남긴다.
- 어떤 agent가 어떤 task에서 어떤 command/output/file change를 만들었는지 기록한다.
- secret denylist, session-level trust, audit log, checkpoint/diff는 Full Access security operating model을 따른다.

## 11. Implementation Phases

### Phase 1: Data model + Persona CRUD

- SQLite table 추가
- Persona service/API
- UI는 Persona Library placeholder 또는 minimal list

### Phase 2: Team Run creation + read model

- Team Run 생성 API
- leader/member persona snapshot 생성
- Team Run list/detail API

### Phase 3: Leader planning

- Leader agent가 goal을 task JSON으로 분해
- task 저장
- `planning_only` mode 완료

### Phase 4: Worker execution

- `plan_and_execute` mode에서 task별 worker 실행
- worker output/message 저장
- SSE event publish

### Phase 5: Team UI integration

- Agent Teams navigation
- New Team Run
- Persona Library
- Team Run Detail
- 기존 activity stream과 결합

## 12. Verification

- Persona CRUD API tests
- Team Run 생성 시 persona snapshot이 저장되는지 테스트
- 원본 persona 수정 후 기존 Team Run agent snapshot이 바뀌지 않는지 테스트
- Leader planning이 task rows를 만드는지 테스트
- Planning only mode가 worker execution 없이 completed 되는지 테스트
- Plan + execute mode가 worker별 message/result를 저장하는지 테스트
- `/api/events`에 team event가 publish되는지 테스트
- UI smoke:
  - Persona Library 렌더
  - New Team Run 생성
  - Team Run Detail에서 agent lanes/task board/activity 표시

## 13. Open Questions

- Team Run 결과가 git diff/checkpoint와 어떻게 연결될지는 Full Access checkpoint 설계와 함께 확정한다.
- worker별 git worktree는 MVP 이후 Phase로 둔다.
- Team template 저장 기능은 Persona/Team Run MVP 이후 추가한다.
- `review_only` mode에서 입력 대상이 session/job/artifact/team-run 중 무엇인지 별도 UX가 필요하다.
