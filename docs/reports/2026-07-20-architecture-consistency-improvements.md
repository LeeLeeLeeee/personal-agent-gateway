---
title: Architecture Consistency Improvements 구현 보고서
type: report
domain: personal-agent-gateway
feature: architecture-consistency
status: done
aliases:
  - 구조 개선 전체 구현
  - Hook 운영 복구 SSE 보안 개선
tags:
  - architecture
  - operations
  - recovery
  - sse
  - security
updated_at: 2026-07-20
---

# Architecture Consistency Improvements 구현 보고서

## Summary

2026-07-20 구조 리뷰의 5개 경계에서 도출한 11개 change request를 구현했다.
기존 단일 process, SQLite, local React state 구조를 유지하면서 누락된 운영·복구·상태·보안
경계를 보완했다.

## Changes

- Operations
  - Hook polling과 `run-now`를 Intake Gate에 연결했다.
  - Emergency Stop이 queued/running Hook Run을 `interrupted`로 수렴시킨다.
  - HookLoop와 HookRunner를 readiness/Operations health에 노출했다.
  - Hook create, enable/disable, delete, run-now 성공·거부를 audit에 남긴다.
- Recovery
  - backup manifest v2에 `database-only` profile과 저장소별 recoverability를 기록한다.
  - Hook credential 값 없이 connection reference 존재 여부를 inventory한다.
  - dry-run은 enabled Hook의 누락 credential reference를 warning으로 반환한다.
- Execution lifecycle
  - `AGENT_JOB_WORKER_CONCURRENCY`는 실제 지원값인 `1`만 허용한다.
  - Team Hook 실패와 startup에서 HookRun/Cycle link와 상태를 reconciliation한다.
- SSE/UI
  - EventBus event에 boot별 `stream_id`를 추가했다.
  - client dedup은 `(stream_id,id)` composite key 512개로 제한한다.
  - Team terminal/input과 Hook event가 collection을 authoritative API로 갱신한다.
- Security
  - Auth/Hook secret JSON은 temp write, flush, fsync, replace를 사용하는 공용 writer로 쓴다.
  - POSIX에서는 directory `0700`, file `0600`을 적용하고 Windows에서는 지원 가능한
    chmod 범위를 적용한다.
  - Hook, Team, Scheduler, mail projection, Hook connection test의 오류가 저장·event·API
    경계 전에 공용 redaction을 통과한다.

## Verification

- `pytest -q` → `505 passed`
- `npm test` → `33 files, 231 tests passed`
- `npm run build` → Vite production build 성공
- `ruff check src tests` → 통과
- `git diff --check` → 통과
- 구조 리뷰 산출물 5개 패키지의 heading, trace, HTML safety 검증 통과

## Follow-ups

- `database-only`는 file/secret 본문을 복구하지 않는다. full bundle은 RPO/RTO와 key
  lifecycle이 정의될 때 별도 설계한다.
- Windows private ACL의 강한 보장은 Python chmod만으로 제공하지 않는다. 다중 사용자
  host를 지원할 때 배포 ACL 또는 credential vault 정책을 추가한다.
- EventBus는 여전히 단일 process memory stream이다. multi-process durable replay가
  필요할 때 broker 또는 persistent sequence를 검토한다.

## Related

- [Operations policy review](../code-structure-review/operations-policy-coverage/2026-07-20-0859/analysis.md)
- [Persistence review](../code-structure-review/persistence-recovery-consistency/2026-07-20-0859/analysis.md)
- [Execution lifecycle review](../code-structure-review/execution-lifecycle-cancellation/2026-07-20-0859/analysis.md)
- [API/SSE/UI review](../code-structure-review/api-sse-ui-consistency/2026-07-20-0859/analysis.md)
- [Security review](../code-structure-review/security-boundaries/2026-07-20-0859/analysis.md)
