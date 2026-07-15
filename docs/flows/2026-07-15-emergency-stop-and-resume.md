---
title: Emergency Stop과 Intake 재개 흐름
type: flow
domain: personal-agent-gateway
feature: emergency-stop
status: active
aliases:
  - 전체 작업 중단
  - kill switch 재개 절차
  - emergency stop flow
tags:
  - operations
  - recovery
  - stop
updated_at: 2026-07-15
---

# Emergency Stop과 Intake 재개 흐름

## Summary

Gateway process는 유지하면서 새 실행 intake를 먼저 닫고 Chat, Team, Job을 순서대로 terminal 상태로 수렴시킨다. 자동 재개하지 않으며 소유자가 상태를 확인한 뒤 별도 action으로 intake를 연다.

## Entry Points

- Operations 화면의 `Emergency stop`
- `POST /api/operations/emergency-stop`
- `POST /api/operations/resume-intake`

## Flow

1. Intake gate를 닫아 새 Chat, Team start, Job create/approve/retry, Schedule create/run-now를 409로 거부한다.
2. active Chat task를 cancel한다.
3. active Team task를 cancel하고 run을 `interrupted`로 기록한다.
4. running Job을 canceled 처리하고 queued Job을 queue에서 제거한다.
5. 대상 수와 결과를 `security.emergency_stop` audit로 남긴다.
6. 소유자가 Operations/Health에서 상태를 확인한다.
7. `resume-intake`를 명시적으로 실행해 새 실행을 허용한다.

## Edge Cases

- Stop을 반복 호출하면 이미 닫힌 상태와 0개 대상 결과를 반환한다.
- gateway shutdown은 intake 재개 없이 종료하며 일반 Emergency Stop과 별도 interruption reason을 사용한다.
- 취소 중 실패한 대상은 결과와 audit metadata에 남기고 다른 domain 취소는 계속한다.

## Verification

- 임시 workspace에서 long-running fake Chat/Team/Job을 시작하고 Stop 후 terminal 상태와 409 intake를 확인한다.
- Stop 두 번과 resume 두 번이 500을 만들지 않는지 확인한다.

## Related

- [R1 실행 플랜](../todo/2026-07-15-r1-operability-execution-plan.md)
- [Audit 정책](../adr/2026-07-15-audit-retention-and-redaction.md)
