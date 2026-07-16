---
title: 열린 Gateway 탭의 Browser Notification과 privacy 경계
type: adr
domain: personal-agent-gateway
feature: team-run-completion-notification
status: done
decision_status: accepted
aliases:
  - Team Run 완료 알림 결정
  - 브라우저 알림 privacy
  - R2-B 알림 계약
tags:
  - notification
  - privacy
  - team-run
  - r2
updated_at: 2026-07-16
---

# 열린 Gateway 탭의 Browser Notification과 privacy 경계

## Context

실제 Team Run 사용에서 사용자가 완료될 때까지 화면을 계속 지켜보는 대기 비용이 확인됐다. Browser Notification 선호는 확인됐지만 webhook, 닫힌 페이지의 background push, 외부 provider 요구는 없었다. Team terminal SSE에는 summary와 error 같은 민감 내용이 포함될 수 있으므로 표시 payload도 별도로 제한해야 했다.

## Decision

- Gateway 페이지가 열려 있을 때만 Browser Notification을 제공한다.
- 사용자가 Settings에서 명시적으로 opt-in한 browser origin에만 preference를 저장한다.
- 실제 event인 `team.run.completed`와 `team.run.failed`만 알린다.
- 알림 title/body는 generic status와 Gateway 확인 안내만 사용한다. Prompt, command, output, summary, error, local path, secret, visible Run ID는 포함하지 않는다.
- Run ID는 notification tag와 내부 navigation에만 사용한다.
- `(run id, terminal type, finished_at)`으로 현재 page lifetime의 중복 알림을 막는다.
- 클릭하면 열린 Gateway 창에 focus하고 기존 Teams 화면의 해당 Run을 선택한다.
- Browser permission과 별도로 OS notification permission이 켜져 있어야 한다.

## Alternatives

### Backend notification provider와 webhook

페이지가 닫혀도 전달할 수 있지만 외부 전송, secret 관리, retry와 delivery log 계약이 새로 필요하다. 실제 사용 근거가 없어 도입하지 않았다.

### Service Worker와 Push API

닫힌 페이지 지원에는 적합하지만 subscription lifecycle과 background delivery 범위가 현재 요구보다 크다. 별도 근거가 생길 때 재검토한다.

### 알림을 제공하지 않음

구현은 가장 작지만 실제로 확인된 화면 감시 비용을 해결하지 못한다.

## Consequences

- Backend service나 새 API 없이 기존 SSE 경계를 재사용한다.
- 탭을 닫으면 알림이 오지 않으며 UI가 이를 명확히 설명해야 한다.
- Browser가 `granted`여도 Windows 전역 또는 Chrome 알림이 꺼져 있으면 toast가 표시되지 않는다.
- 민감정보 노출과 중복 폭주가 확인되면 preference를 끄고 Team terminal 처리는 유지할 수 있다.

## Follow-ups

- 닫힌 페이지 알림이나 webhook 요구가 실제 사용에서 확인되면 별도 delivery/privacy ADR을 작성한다.
- 운영 절차는 [Team Run 완료 알림 사용과 확인](../flows/2026-07-16-team-run-completion-notification.md)을 따른다.
