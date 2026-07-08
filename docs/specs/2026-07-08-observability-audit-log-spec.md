# Observability and Audit Log Spec

- 작성일: 2026-07-08
- 대상: `personal-agent-gateway`
- 범위: local audit log, structured app logs, Team/Persona attribution
- 관련 문서:
  - `docs/knowledge/2026-07-08-full-access-security-operating-model.md`
  - `docs/specs/2026-07-08-persona-agent-teams-spec.md`
  - `docs/specs/2026-07-07-live-activity-viewer-chat-redesign-spec.md`

## 1. 배경 / 문제

personal-agent-gateway는 외부 브라우저에서 로컬 PC의 agent를 실행하고, Full Access Mode에서는 shell/filesystem/workspace 제한과 per-action approve/deny를 약하게 가져갈 수 있다. 이 방향에서는 실행 전 차단보다 "무슨 일이 있었는지", "누가/어떤 agent가 실행했는지", "무엇을 되돌려야 하는지"를 정확히 남기는 것이 중요하다.

현재 저장소에는 transcript, job_events, EventBus가 있지만 각각 목적이 다르다.

- Transcript: 대화 재현용
- Job events: job lifecycle 표시용
- EventBus/SSE: 현재 실행 중 live UI용

보안/사고 분석/복구 판단을 위한 독립적인 audit log가 필요하다.

## 2. 목표 / 성공 기준

- local SQLite에 append-only audit log를 저장한다.
- audit log는 session, job, team run, team agent, task, command, artifact, auth event를 연결할 수 있어야 한다.
- Full Access Mode에서 어떤 agent/persona가 어떤 task에서 어떤 command/file change/result를 만들었는지 추적할 수 있어야 한다.
- application errors는 structured local log로 남긴다.
- UI에서 최근 audit events와 critical/error events를 확인할 수 있다.
- 기존 SSE activity stream은 live display이고, audit log는 durable source로 분리한다.

## 3. 비목표

- hosted observability platform을 dependency로 만들지 않는다.
- 외부 error tracking으로 command output/transcript를 보내지 않는다.
- multi-user admin console을 만들지 않는다.
- SIEM 수준의 검색/보관 시스템을 만들지 않는다.
- OS kernel/process monitoring까지 포함하지 않는다.

## 4. Observability Layers

### Layer 1: Local Audit Log

보안/복구 판단의 1차 신뢰 소스다. SQLite에 저장한다.

대상:

- login success/failure/logout/session revoke
- chat request started/completed/failed
- Codex process started/completed/failed
- command execution started/completed/failed
- job created/queued/running/succeeded/failed/canceled
- team run created/started/completed/failed/canceled
- team agent started/completed/failed
- team task created/updated/completed/failed
- artifact created/deleted
- deploy/build/git push 같은 high-risk action 감지
- emergency stop

### Layer 2: Structured Application Logs

운영 디버깅용 JSONL 또는 standard logging output이다.

대상:

- FastAPI request error
- background worker error
- scheduler loop error
- model client timeout/non-zero exit
- unexpected exception

저장 위치는 `AGENT_LOG_DIR` 또는 `session_dir.parent / "logs"`로 둔다.

## 5. Audit Event Model

### `audit_events`

```text
id
occurred_at
event_type
severity: debug | info | warning | error | critical
actor_type: owner | agent | system
actor_id
session_id
team_run_id
team_agent_id
team_task_id
job_id
artifact_id
request_id
action
target_type
target_id
status: started | succeeded | failed | canceled | denied | observed
command_preview
cwd
exit_code
ip_hash
user_agent_hash
metadata_json
redaction_version
```

Guidelines:

- `command_preview`는 local DB에는 저장 가능하다.
- `cwd`는 workspace-relative path를 우선 저장한다.
- `ip_hash`, `user_agent_hash`는 원문 대신 hash를 저장한다.
- `metadata_json`은 sanitized payload만 저장한다.
- raw stdout/stderr는 audit log에 기본 저장하지 않는다. 필요하면 artifact/job event로 연결한다.

## 6. Event Type Taxonomy

```text
auth.login.succeeded
auth.login.failed
auth.logout
auth.session.revoked

runtime.chat.started
runtime.chat.completed
runtime.chat.failed

codex.process.started
codex.process.completed
codex.process.failed
codex.command.started
codex.command.completed
codex.command.failed

job.created
job.started
job.completed
job.failed
job.canceled

team.run.created
team.run.started
team.run.completed
team.run.failed
team.agent.started
team.agent.completed
team.agent.failed
team.task.created
team.task.updated
team.task.completed
team.task.failed

artifact.created
artifact.deleted

security.secret_read.blocked
security.high_risk_action.observed
security.emergency_stop
```

## 7. Redaction Policy

Audit log와 structured local log는 같은 redaction helper를 사용한다.

Redact:

- env var values for keys containing `TOKEN`, `KEY`, `SECRET`, `PASSWORD`, `OTP`
- OpenAI/Codex API keys
- TOTP secret and recovery codes
- Cloudflare tunnel URL if configured as sensitive
- `.env` file content
- SSH private key blocks
- local user profile absolute paths where possible

Keep:

- event ids
- job/team/session ids
- status
- command category
- command preview in local audit only
- exit code
- duration
- file count / changed file paths when not secret-denied

## 8. Local Observability Configuration

New env vars:

```text
AGENT_OBSERVABILITY_ENABLED=true|false
AGENT_AUDIT_ENABLED=true|false
AGENT_LOG_LEVEL=INFO
AGENT_LOG_DIR=./data/logs
```

Defaults:

- local audit enabled
- structured local logging enabled
- log level `INFO`
- logs stored under local data root by default

## 9. API

```text
GET /api/audit/events
GET /api/audit/events/{id}
GET /api/observability/status
```

`GET /api/audit/events` filters:

```text
event_type
severity
session_id
team_run_id
team_agent_id
job_id
since
limit
```

## 10. UI Integration

Observability UI should not become a separate monitoring product. It should extend the current control console.

Recommended placement:

- Settings: Observability status, audit enabled, log path
- Activity/Jobs/Team Detail: linked audit event rows
- New "Audit" or "Logs" screen only after the basic read API exists

Initial UI:

- Recent audit events table
- Filters: severity, event type, team run, job, session
- Event detail drawer
- "Open related team run/job/session" links

## 11. Agent Teams Attribution

Team features must populate audit fields:

- `team_run_id`
- `team_agent_id`
- `team_task_id`
- `actor_type="agent"`
- `actor_id=<team_agent_id>`
- `metadata.persona_name`
- `metadata.persona_role`

This supports the Full Access principle: do not block every action, but make each action attributable to the persona/agent/task that caused it.

## 12. Verification

- DB initializes `audit_events`.
- `AuditLogService.record(...)` writes append-only events.
- Auth login success/failure writes audit events.
- Chat request writes started/completed/failed events.
- Job lifecycle writes audit events.
- Team Run lifecycle writes audit events.
- Codex command execution events write audit events with command preview local-only.
- `/api/audit/events` requires OTP session.
- UI renders recent audit events without exposing secrets.

## 13. Open Questions

- Audit retention policy: keep forever for local owner, or add `AGENT_AUDIT_RETENTION_DAYS`.
- Whether command preview should be encrypted at rest in local SQLite.
- Whether audit events should be exportable as JSONL artifact.
