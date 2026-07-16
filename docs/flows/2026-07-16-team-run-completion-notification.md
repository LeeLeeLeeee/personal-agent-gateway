---
title: Team Run 완료 알림 사용과 확인
type: flow
domain: personal-agent-gateway
feature: team-run-completion-notification
status: done
aliases:
  - Team Run 알림 켜는 법
  - 브라우저 알림 테스트
  - 알림이 안 올 때
tags:
  - notification
  - operations
  - team-run
  - troubleshooting
updated_at: 2026-07-16
---

# Team Run 완료 알림 사용과 확인

## Summary

열린 Gateway 탭에서 사용자가 알림을 opt-in하면 새 Team Run의 완료 또는 실패 terminal SSE를 browser notification으로 표시한다. Browser site permission과 OS notification permission이 모두 필요하며 Gateway 탭을 닫은 상태는 지원하지 않는다.

## Entry Points

- Gateway `Settings` → `Browser notifications` → `Enable notifications`
- Gateway `Team Runs` → 새 Run 실행

## Flow

1. Gateway Settings에서 `Enable notifications`를 누른다.
2. Browser permission prompt에서 알림을 허용하고 Gateway 상태가 `ON`인지 확인한다.
3. Windows `시스템 → 알림`에서 전역 알림과 Google Chrome 알림을 켠다. 필요하면 `방해 금지`도 끈다.
4. 짧은 새 Team Run을 실행한다.
5. Gateway 탭은 열어두고 다른 탭으로 이동하거나 창을 최소화한다.
6. Run이 completed 또는 failed가 되면 generic notification이 한 번 표시되는지 확인한다.
7. 알림을 클릭해 Gateway가 focus되고 해당 Team Run 상세가 선택되는지 확인한다.

## Edge Cases

- `UNSUPPORTED`: 현재 browser에서 Notification API를 사용할 수 없다.
- `BLOCKED`: site permission이 거부됐다. Browser site settings에서 권한을 다시 허용해야 한다.
- `OFF`: opt-in하지 않았거나 permission이 granted가 아니다.
- Gateway가 `ON`이어도 Windows 전역 알림이 꺼져 있으면 toast가 보이지 않는다.
- 설정을 켜기 전에 끝난 Run은 다시 알리지 않는다. 설정 변경 뒤 새 Run으로 확인한다.
- Gateway 탭을 닫으면 service worker/background push가 없으므로 알림이 오지 않는다.

## Verification

- 알림이 한 번만 나타난다.
- Title/body에 prompt, 결과 내용, error, 파일 경로, visible Run ID가 없다.
- 클릭 시 올바른 Team Run 상세로 이동한다.
- 2026-07-16 수동 확인에서 Windows 알림을 활성화한 뒤 사용자가 실제 수신을 확인했다.

## Related

- [Browser Notification과 privacy 경계](../adr/2026-07-16-browser-notification-privacy.md)
- [R2 범위 축소 구현 보고서](../reports/2026-07-16-r2-scoped-product-expansion-implementation.md)
