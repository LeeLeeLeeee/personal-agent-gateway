---
title: Personal Agent Gateway R0 신뢰 기반 구현 보고서
type: report
domain: personal-agent-gateway
feature: r0-trust-foundation
status: active
aliases:
  - PAG R0 구현 결과
  - 인증 lifecycle UI 계약 구현 보고서
tags:
  - authentication
  - lifecycle
  - reliability
  - implementation
updated_at: 2026-07-15
---

# Personal Agent Gateway R0 신뢰 기반 구현 보고서

## Summary

[R0 실행 플랜](../todo/2026-07-15-r0-trust-foundation-execution-plan.md)의 여섯 변경 묶음을 완료했다. 서버 검증 session, Worker/Scheduler lifespan, background failure isolation, runtime capability 기반 UI, 핵심 회귀 test와 CI gate가 현재 코드에 연결돼 있다.

## Changes

- SQLite `auth_sessions`에 raw token 대신 SHA-256 hash와 absolute/idle expiry, revoke 상태를 저장한다.
- Login/setup/status/logout과 모든 protected router가 공통 `SessionPrincipal` dependency를 사용한다.
- FastAPI lifespan이 Team/Job recovery, Worker/Scheduler start-stop, non-Chat queued Job 재실행을 소유한다.
- runner/tick 예외를 작업 단위로 격리하고 shutdown interruption과 일반 실패를 구분하며 secret을 redaction한다.
- Chat Job은 이력 mirror로 유지해 Job 승인·worker 실행 경로에서 제외한다.
- Team Run은 지원 중인 Planning/Execute만 노출하고 effective worker `1 · SEQUENTIAL`을 표시한다.
- Automation이 unhealthy하면 Schedule 생성과 Run now를 비활성화하고 원인을 표시한다.
- Settings가 실제 session, bind/tunnel, Worker/Scheduler, automation, CLI availability를 보여준다.
- `.github/workflows/ci.yml`에 backend lint/test와 frontend test/build를 필수 검증으로 추가했다.

관련 결정은 [Auth session ADR](../adr/2026-07-15-auth-session-storage.md)과 [Automation lifecycle ADR](../adr/2026-07-15-automation-lifecycle.md)에 기록했다.

## Verification

- `python -m pytest -q` → `365 passed`
- `python -m ruff check src tests` → 통과
- `npm --prefix frontend test` → `28 files, 177 passed`
- `npm --prefix frontend run build` → production build 통과
- 실제 생성돼 있던 Team Run/workspace는 테스트에 사용하지 않았다.

## Follow-ups

- R1의 session revoke-all UI, automation history/retry, readiness endpoint, audit와 구조 분리는 별도 승인 후 진행한다.
- 실제 Review flow와 bounded Team concurrency는 R2까지 미지원 상태로 유지한다.
- Vite build의 정적 vendor 경로 경고는 runtime-served asset 계약이며 이번 R0 gate 실패는 아니다.
