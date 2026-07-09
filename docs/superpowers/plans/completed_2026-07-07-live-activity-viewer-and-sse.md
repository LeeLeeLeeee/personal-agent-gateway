# Live Activity Viewer and SSE Completion Record

> `2026-07-07-live-activity-stream-plan.md`와 `2026-07-07-live-activity-viewer.md`를 통합 축약한 완료 기록이다.

## Result Summary

Codex JSONL event를 SSE로 전달하고, chat 화면에서 user/agent/activity/command/artifact/error를 timeline으로 표시하는 live activity viewer 기반은 구현 완료됐다. 이후 React frontend로 이동하면서 Timeline/ChatView/Statusbar 컴포넌트와 frontend tests로 기능이 유지되고 있다.

## Final Status

| Area | Status | Notes |
| --- | --- | --- |
| Codex JSON event capture | SUCCESS | `CodexModelClient`의 `on_event` callback 경로 존재 |
| SSE EventBus | SUCCESS | `events.py`와 `/api/events` 존재 |
| Timeline mapping | SUCCESS | `frontend/src/lib/timeline.js` 존재 |
| Timeline UI | SUCCESS | `Timeline`, `ChatView`, `Statusbar` 존재 |
| Command rendering | SUCCESS | `command_execution` item을 command block으로 렌더 |

## Verification

- 관련 테스트: `tests/test_events.py`, `tests/test_model_client.py`, `frontend/src/lib/timeline.test.js`, `Timeline.test.jsx`, `ChatView.test.jsx`, `GatewayApp.test.jsx`.

## Known Limitations

- 기존 구현은 live SSE activity를 memory-only EventBus와 frontend state에 의존한다.
- 세션 전환/재진입/새로고침 후 durable activity 복원은 아직 부족하다.
- 이 한계는 신규 active spec `docs/specs/2026-07-09-session-scoped-chat-and-sse-spec.md`로 이관했다.

## Cleanup Notes

- 두 원본 계획의 구현 세부 절차와 오래된 vanilla JS 렌더링 계획은 제거했다.
- 완료된 디자인/product spec은 `docs/specs/completed_2026-07-07-live-activity-viewer-chat-redesign-spec.md`에 유지한다.
