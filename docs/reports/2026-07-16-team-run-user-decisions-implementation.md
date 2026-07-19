---
title: Team Run 배치형 사용자 결정 구현 보고서
type: report
domain: personal-agent-gateway
feature: team-run-user-decisions
status: done
aliases:
  - Team Run INPUT NEEDED 구현
  - Leader 질문 batch 구현 결과
tags:
  - team-run
  - user-input
  - decision-request
  - implementation
updated_at: 2026-07-16
---

# Team Run 배치형 사용자 결정 구현 보고서

## Summary

Worker가 요청한 정보 중 Leader가 내부적으로 답할 수 없는 질문을 하나의 decision batch로 모으고, Run이 더 진행할 수 없을 때 사용자에게 한 번에 요청하는 흐름을 구현했다. 답변 대기 중에는 실행 process가 없으며, 모든 질문을 제출하면 관련 blocked Task만 자동 재개한다.

## Delivered

| 영역 | 결과 |
| --- | --- |
| Persistence | migration 5, `team_decision_requests`, active batch unique index |
| State | 별도 Run status `waiting_for_user`, task `blocked`, agent `waiting` |
| Runtime | Leader `answer | ask_user` mediation, task drain batching, run-scope immediate gate |
| Projection | Run root `USER_DECISIONS.md`, resolved history와 revision 표시 |
| Answer API | request ID/revision CAS, 전체 답변 검증, 단일 background resume |
| Recovery | waiting 상태 restart 보존, waiting Cancel 정리, Add work/Resume/Delete 차단 |
| UI | `INPUT NEEDED`, 이유·영향·추천·선택/자유 답변, `ANSWER & RESUME` |
| Notification | 열린 opt-in 탭에 내용 없는 `Team Run needs input` 알림 |

## Key contracts

- Worker의 기존 `needs_info` 형식은 유지한다.
- Leader가 plain text로 답하는 기존 behavior도 answer fallback으로 유지한다.
- user decision이 생긴 Task는 실패하지 않고 `blocked`가 된다.
- task-scoped 질문이 있어도 다음 pending Task를 계속 실행한다.
- active batch는 Run당 하나이며 질문 추가와 publish마다 revision이 증가한다.
- answer API는 stale, duplicate, 누락 답변을 `409`로 거부한다.
- 사용자 답변은 Worker 재실행 prompt의 `Resolved user decisions` context로 전달된다.
- `waiting_for_user`는 startup에서 `interrupted`로 정규화되지 않는다.
- notification과 audit metadata에는 질문과 답변 전문을 넣지 않는다.

## Changed areas

- Backend: `db.py`, `migrations.py`, `teams.py`, `team_runtime.py`, `api/team_runs.py`
- Frontend: API client, `useTeamRunController`, `GatewayApp`, `TeamRunDetail`, `StatusBadge`, browser notification
- Tests: service, runtime, API/restart, component, API client, notification

## Verification

| Check | Result |
| --- | --- |
| Team service/runtime/API/migration pytest | 73 passed |
| Frontend Vitest, single worker | 32 files / 222 tests passed |
| Frontend production build | SUCCESS |
| Ruff changed Python files | SUCCESS |
| Decision docs registry and local links | SUCCESS |

병렬 frontend suite에서는 unrelated `HooksView` 5초 timeout이 한 번 발생했지만 해당 test 단독 실행은 통과했고, `--maxWorkers=1` 전체 suite도 통과했다. Production build의 기존 vendor resolution 및 잘못 닫힌 Hooks CSS comment warning은 이번 변경과 무관하게 남아 있다.

## Related

- [배치형 사용자 결정 ADR](../adr/2026-07-16-team-run-batched-user-decisions.md)
- [사용자 결정 요청과 재개 흐름](../flows/2026-07-16-team-run-user-decision-request.md)
- [Browser Notification privacy 경계](../adr/2026-07-16-browser-notification-privacy.md)
