---
title: Personal Agent Gateway R2 제품 확장 완료 기록
type: todo
domain: personal-agent-gateway
feature: r2-product-expansion
status: done
aliases:
  - PAG R2 실행 계획
  - R2 완료 기록
  - R2-B 브라우저 알림 완료
  - LATER 단계 완료
tags:
  - execution-plan
  - product-expansion
  - usability
  - notification
updated_at: 2026-07-16
---

# Personal Agent Gateway R2 제품 확장 완료 기록

작성일: 2026-07-15
완료일: 2026-07-16
상태: SUCCESS — 실제 사용 1회에서 확인된 `PG-1`과 `R2-B`로 범위를 축소해 완료

## 결과 요약

R2는 최초 wishlist 전체를 구현하지 않았다. 실제 Team Run 사용에서 확인된 Documents/정렬/assignment/roster/개별 중단 마찰은 `PG-1`으로 보정했고, 완료될 때까지 화면을 지켜보는 문제는 열린 Gateway 탭의 opt-in Browser Notification으로 해결했다. 사용자가 Windows와 Chrome 알림을 활성화한 뒤 실제 완료 알림 수신을 확인했으며, 관찰 근거가 없던 Result package, Template, Search/Metrics, Review, Persona-local concurrency는 별도 backlog로 이동했다.

## 완료 이력

| 단계 | 상태 | 결과 | 검증 |
| --- | --- | --- | --- |
| G2-0 R1 NEXT Gate | SUCCESS | R1 운영 계약 유지 | R1 full gate |
| G2-1 실제 사용 검토 | SUCCESS | 사용자 결정으로 1회 사용을 최종 근거로 채택 | [실제 사용 기록](../reports/2026-07-15-r2-g2-user-validation.md) |
| PG-1 사용성 보정 | SUCCESS | preview, 최신순, assignment/current task, roster, Stop run | Backend 454, frontend 210, Ruff, build, 8787 smoke |
| D2-2 Notification/privacy | SUCCESS | 열린 탭 opt-in, generic payload, webhook/service worker 제외 | ADR과 component analysis |
| R2-B Completion notification | SUCCESS | completed/failed 알림, dedupe, Team Run click 이동 | Targeted 50, frontend 217, build |
| R0/R1 regression | SUCCESS | Auth/lifecycle/stop/audit/retry/backup 회귀 없음 | Backend 454, Ruff, frontend 217, build |
| 실제 알림 수신 | SUCCESS | Windows/Chrome 알림 활성화 뒤 사용자 확인 | 2026-07-16 수동 확인 |

## 최종 범위

### PG-1

- 지원 가능한 Team document만 노출하고 raster image와 sandbox HTML preview를 제공한다.
- Documents, Results, Live Activity, Handoff group을 최신 활동순으로 표시한다.
- Task owner, Agent current Task, leader/member roster identity를 snapshot과 runtime 상태에 연결한다.
- 기존 backend cancel lifecycle을 active Team Run의 `Stop run` UI에 연결한다.

### R2-B

- Settings에서 사용자 동작으로만 browser permission을 요청한다.
- `unsupported`, `default`, `granted`, `denied`와 local opt-in을 구분한다.
- `team.run.completed`와 `team.run.failed`만 generic title/body로 표시한다.
- Prompt, command, output, summary, error, local path, secret, visible Run ID를 표시하지 않는다.
- `(run id, terminal type, finished_at)`으로 page-lifetime duplicate를 막는다.
- 알림 click은 열린 Gateway에 focus하고 해당 Team Run 상세를 선택한다.
- Gateway 탭을 닫은 상태, webhook, service worker/background push는 지원하지 않는다.

## 수동 확인과 운영 교훈

- 첫 확인에서는 Windows 전역 알림 `ToastEnabled`가 꺼져 있어 Gateway preference가 `ON`이어도 toast가 보이지 않았다.
- Windows 전역 알림과 Google Chrome 알림을 켠 뒤 사용자가 실제 Team Run 완료 알림을 확인했다.
- 따라서 Gateway 상태, browser site permission, OS notification permission, 열린 탭이 모두 delivery 전제다.
- 이미 끝난 Run은 설정 변경 후 다시 알리지 않으므로 새 Run으로 확인해야 한다.

## 제거 및 의존성 정리

- R2-B를 위해 backend notification service/API, provider interface, webhook, service worker를 만들지 않았다.
- R2-A stable route 의존성을 제거하고 기존 Teams 화면의 selected Run navigation을 사용했다.
- 사용자 Team Run과 workspace는 자동화 fixture로 사용하지 않았다.
- 관찰 근거가 없는 기능을 `SUCCESS`로 바꾸지 않고 [R2 후속 제품 가설 backlog](2026-07-16-r2-deferred-product-backlog.md)로 분리했다.

## 완료 체크리스트

- [x] Documents/Results/Activity/Handoff 탐색과 preview 마찰을 보정했다.
- [x] Task owner/current work와 Run roster identity가 runtime source와 일치한다.
- [x] Active Run을 개별 중단할 수 있고 기존 결과를 보존한다.
- [x] 알림은 opt-in이며 민감 정보를 노출하지 않는다.
- [x] Duplicate terminal delivery가 같은 알림을 반복하지 않는다.
- [x] 알림 click이 해당 Team Run으로 이동한다.
- [x] 열린 탭 한계를 UI와 운영 문서에 명시했다.
- [x] R0/R1 full regression gate가 통과했다.
- [x] 사용자가 실제 완료 알림 수신을 확인했다.

## Docs 승격 결과

| 원래 내용 | 이동 위치 또는 처리 |
| --- | --- |
| Notification provider/privacy 결정과 대안 | [Browser Notification privacy ADR](../adr/2026-07-16-browser-notification-privacy.md) |
| Opt-in, OS permission, 수동 확인과 troubleshooting | [Team Run 완료 알림 flow](../flows/2026-07-16-team-run-completion-notification.md) |
| PG-1/R2-B 구현·검증·범위 결정 | [R2 구현 보고서](../reports/2026-07-16-r2-scoped-product-expansion-implementation.md) |
| 실제 사용 근거와 수동 알림 결과 | [G2-1 실제 사용 기록](../reports/2026-07-15-r2-g2-user-validation.md) |
| R2-A/C/D/E/F와 닫힌 페이지 delivery | [R2 후속 제품 가설 backlog](2026-07-16-r2-deferred-product-backlog.md) |
| PG-1과 R2-B의 상세 파일 inventory·수정 계획·rollback 초안 | 구현 보고서와 test가 결과를 보존하므로 완료 플랜에서 제거 |
| 미구현 기능의 speculative architecture와 test 초안 | 실제 근거 전 설계를 고정하지 않기 위해 제거하고 backlog에는 해제 조건만 보존 |
| 공통 `TODO/LOCK/FAIL/SUCCESS` 운영 규칙 | 반복되는 todo template 내용이므로 승격 없이 제거 |

## 관련 문서

- [통합 서비스 개선 로드맵](2026-07-15-service-improvement-roadmap.md)
- [Browser Notification privacy ADR](../adr/2026-07-16-browser-notification-privacy.md)
- [Team Run 완료 알림 flow](../flows/2026-07-16-team-run-completion-notification.md)
- [R2 구현 보고서](../reports/2026-07-16-r2-scoped-product-expansion-implementation.md)
- [R2 후속 제품 가설 backlog](2026-07-16-r2-deferred-product-backlog.md)
- [GatewayApp Browser Notification component analysis](../component-inspector/GatewayApp/2026-07-16-0855.md)
