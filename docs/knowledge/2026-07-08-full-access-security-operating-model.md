---
title: Full Access Mode Security Operating Model
type: knowledge
domain: personal-agent-gateway
feature: full-access-security
status: active
aliases:
  - full access 보안
  - workspace 제한 없이 쓰는 보안 기준
  - shell 제한 없는 agent gateway 보안
  - approve deny 없는 보안 모델
  - 외부에서 로컬 PC 수정 보안 점검
tags:
  - security
  - full-access
  - otp
  - cloudflare-access
  - audit
  - agent-teams
updated_at: 2026-07-08
---

# Full Access Mode Security Operating Model

personal-agent-gateway는 외부 브라우저에서 로컬 PC의 agent를 실행해 파일 수정, shell 실행, 빌드, 배포까지 수행할 수 있는 개인용 control console이다.

사용성 우선 방향에서는 workspace 제한, shell 제한, per-action approve/deny를 강하게 걸지 않는다. 이 경우 보안 모델은 "명령 실행 전 차단"이 아니라 다음 기준으로 바뀐다.

```text
실행은 최대한 허용한다.
대신 입구를 강하게 막고, 실행 환경을 격리하고, 모든 행동을 기록하고, 복구 가능하게 만든다.
```

## 제품 전제

- OTP는 입구 보안의 한 요소일 뿐이다.
- Full Access Mode에서는 gateway에 들어온 사용자가 로컬 PC를 조작할 수 있다고 가정한다.
- approve/deny, workspace 제한, shell 제한은 사용성을 떨어뜨릴 수 있으므로 기본 UX에서 제거하거나 선택 옵션으로 둔다.
- 따라서 보안은 session-level trust, OS-level isolation, audit, recovery 중심으로 설계한다.

## 권장 실행 모드

설정에는 실행 모드를 명시적으로 둔다.

```text
Execution Mode
- Restricted: workspace/capability 제한, 위험 작업 승인 중심
- Full Access: shell/filesystem unrestricted, session-level trust 중심
```

사용자는 Full Access를 기본값으로 사용할 수 있다. 단, 코드와 UI는 현재 gateway가 Full Access 상태임을 명확히 알고 있어야 한다.

## 우선순위 보안 체크리스트

### 1. Cloudflare Access 앞단 적용

Full Access Mode에서는 Cloudflare Tunnel URL이 사실상 로컬 PC 자동화 콘솔의 입구다.

권장:

- Cloudflare Access 또는 동등한 앞단 인증을 적용한다.
- 허용 이메일 또는 IdP 계정을 제한한다.
- 가능하면 기기, 국가, IP 조건을 추가한다.
- OTP 하나만으로 public tunnel을 보호하지 않는다.

### 2. OTP + Session-Level Trust

per-action approve/deny 대신 로그인 session 자체를 신뢰 단위로 둔다.

필수:

- OTP login
- 짧은 idle timeout
- logout
- revoke all sessions
- login success 시 session id 재발급
- failed login rate limit/backoff

### 3. Cookie 및 CSRF 보호

cookie 기반 인증을 쓰므로 state-changing API는 CSRF 위험을 가진다.

필수:

- `HttpOnly`
- `Secure`
- `SameSite=Lax` 또는 가능하면 `Strict`
- `POST /api/chat`, `/api/jobs`, `/api/team-runs`, `/api/schedules`, `/api/deploy` 등 state-changing endpoint에 Origin/Referer 검증 또는 CSRF token 적용

### 4. Gateway 전용 OS 사용자로 실행

Full Access Mode에서 가장 현실적인 피해 범위 제한 장치다.

권장:

```text
Administrator / main user
  - 일반 개발 및 관리용

agent-runner
  - gateway 실행 전용
  - 필요한 프로젝트 폴더만 접근 가능
  - 개인 문서, 브라우저 프로필, SSH key, password manager export 접근 불가
```

앱 내부에서는 전부 허용처럼 쓰되, OS 권한으로 피해 범위를 줄인다.

### 5. Audit Log

차단하지 않더라도 기록은 반드시 남긴다.

최소 기록:

- 로그인 성공/실패
- 접속 IP
- user agent
- session id
- user prompt
- agent/team run id
- 실행 command
- cwd
- exit code
- stdout/stderr 요약
- 변경 파일 목록
- 배포, 네트워크, 삭제 명령 여부
- 생성 artifact

Audit log는 transcript와 별도로 보존하는 것이 좋다. transcript는 대화 재현용이고, audit log는 사고 분석과 복구 판단용이다.

### 6. Automatic Checkpoint 및 Diff

Full Access Mode에서는 실행 전 차단보다 실행 후 복구가 중요하다.

권장:

- agent run 시작 전 `git status` 저장
- git repo면 run 시작 시 checkpoint branch 또는 lightweight snapshot 생성
- run 종료 후 변경 파일 목록 저장
- team run 단위 diff 저장
- UI에서 "이 run이 바꾼 파일"을 볼 수 있게 한다.

초기 버전에서는 자동 commit까지 하지 않아도 된다. 최소한 before/after 상태와 diff path는 남겨야 한다.

### 7. Secret File Denylist

Full Access 철학에서도 secret 유출은 피해 규모가 크다. 기본 denylist는 유지하는 것이 좋다.

기본 denylist 후보:

```text
.env
.env.*
*.pem
*.key
id_rsa
id_ed25519
.aws/
.gcp/
.azure/
AppData/Roaming/*/Cookies
browser profile
password manager export
```

정말 완전 허용이 필요하면 "secret denylist off" 설정을 따로 둘 수 있다. 단, 기본값은 deny가 낫다.

### 8. Emergency Stop

Agent Teams가 들어오면 worker 여러 개가 동시에 실행될 수 있다.

필수 기능:

- Kill all running agents
- Stop current team run
- Stop background jobs
- Stop known dev servers
- Cancel queued jobs

이 기능은 승인/거절 UX가 아니라 사고 대응 장치다.

### 9. Agent Teams 권한 해석

Persona는 작업 판단 기준이지만, Full Access Mode에서는 권한 제한으로 강제하지 않을 수 있다.

대신 다음을 기록해야 한다.

- 어떤 persona가 어떤 command를 실행했는지
- 어떤 worker가 어떤 파일을 바꿨는지
- leader가 어떤 결과를 통합했는지
- team run의 최종 diff가 무엇인지

즉, persona별 권한 차단보다 persona별 audit attribution이 우선이다.

## UX 방향

사용성을 해치지 않는 보안 장치를 우선한다.

권장:

- 매 command마다 approve/deny하지 않는다.
- workspace/shell 제한은 기본 UX에서 숨기거나 Full Access 기본값으로 둔다.
- 로그인과 session trust를 강하게 한다.
- 실행 중인 작업, 변경 파일, command log, final diff를 잘 보여준다.
- 위험을 막기보다 "무슨 일이 일어났는지"와 "되돌릴 수 있는지"를 명확히 보여준다.

## 최소 보안 MVP

Full Access Mode를 제품 방향으로 채택한다면 최소한 아래 항목을 먼저 구현한다.

```text
- Cloudflare Access 앞단 적용
- OTP login 유지
- session idle timeout + revoke all sessions
- HttpOnly + Secure + SameSite cookie
- state-changing API Origin/CSRF 검증
- gateway 전용 OS 사용자 실행 가이드
- audit log
- run 시작/종료 checkpoint와 diff 기록
- secret file denylist
- emergency kill switch
```

## 기존 제한형 설계와의 관계

기존 문서들은 shell approval, workspace containment, capability approval을 기본 안전장치로 둔다. 이 문서는 그 설계를 폐기하는 문서가 아니라, 사용성 우선의 Full Access Mode를 선택할 때 필요한 대체 보안 기준을 정리한다.

향후 구현에서는 `Restricted`와 `Full Access`를 명시적 mode로 분리하고, Full Access에서는 per-action gate보다 ingress hardening, audit, checkpoint, recovery를 우선한다.
