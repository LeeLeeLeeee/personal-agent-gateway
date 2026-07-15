---
title: Gateway 인증 세션을 SQLite hash session으로 저장
type: adr
domain: personal-agent-gateway
feature: auth-session-storage
status: active
decision_status: accepted
aliases:
  - PAG 인증 세션 결정
  - agent_session 저장 방식
  - D0-1 Auth session source of truth
tags:
  - authentication
  - session
  - sqlite
updated_at: 2026-07-15
---

# Gateway 인증 세션을 SQLite hash session으로 저장

## Context

현재 로그인은 random cookie를 발급하지만 서버는 이후 cookie 존재만 검사한다. 임의 cookie, 만료 cookie, logout 전에 복사한 cookie를 구분할 source of truth가 없다. R0은 absolute/idle expiry와 즉시 revoke를 함께 제공해야 한다.

## Decision

- Raw session token은 browser cookie에만 둔다.
- SQLite `auth_sessions`에는 token의 SHA-256 hash, session id, 발급·최근 사용·absolute/idle 만료·폐기 시각을 저장한다.
- 모든 protected API는 하나의 FastAPI dependency가 `SessionPrincipal`을 검증한 뒤 접근한다.
- 기본 absolute TTL은 24시간, idle TTL은 1시간이며 환경 변수로 조정한다.
- Logout은 현재 session을 revoke한 뒤 cookie를 삭제한다.
- State-changing request는 `Origin`이 있을 경우 현재 request origin과 일치해야 한다. Origin이 없는 local non-browser client는 유효 session이 있으면 허용한다.

## Alternatives

### Signed stateless cookie

Absolute 만료는 단순하지만 idle 만료와 즉시 revoke/revoke-all을 위해 server state가 다시 필요하다. 두 source를 조합하는 것보다 기존 SQLite에 session row 하나를 두는 편이 작다.

### 기존 AuthStore JSON에 session 추가

TOTP secret/recovery code와 빈번히 갱신되는 access session은 수명과 query 패턴이 다르다. JSON 전체 rewrite와 credential/session 책임 혼합을 피한다.

## Consequences

- 임의·만료·폐기 cookie를 일관되게 거부할 수 있다.
- Protected request마다 SQLite lookup이 발생하고 idle 갱신 시 write가 발생한다. R1 성능 측정 전 별도 cache를 추가하지 않는다.
- DB backup에는 active session metadata가 포함되므로 raw token을 저장하지 않는 원칙을 유지한다.

## Follow-ups

- R1에서 현재 session 목록과 revoke-all owner UI를 제공한다.
- R1 성능 측정에서 last-seen write throttling 필요 여부를 판단한다.
