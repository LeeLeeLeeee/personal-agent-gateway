---
title: Event Hooks Backend 완료 기록
type: todo
domain: personal-agent-gateway
feature: event-hooks-backend
status: done
aliases:
  - Event Hooks 백엔드 완료
  - IMAP hook 자동 실행 완료
tags:
  - hooks
  - automation
  - imap
  - backend
updated_at: 2026-07-16
---

# Event Hooks Backend 완료 기록

## 결과 요약

IMAP email source를 polling해 durable Hook Run을 생성하고 headless Agent runtime으로 순차 실행하는 Event Hooks backend를 구현했다. Secret은 별도 파일 store에 보관하고, app lifespan이 HookLoop와 HookRunner를 시작·정리하며 재시작 시 queued run을 복구한다.

## 단계별 상태

| 단계 | 상태 | 결과 |
| --- | --- | --- |
| Schema | SUCCESS | hooks와 hook_runs table/index 추가 |
| Secret store | SUCCESS | hook 연결 비밀의 파일 저장 경계 구현 |
| Run service | SUCCESS | 실행 이력과 상태 전이 구현 |
| Source adapter | SUCCESS | adapter contract와 IMAP 구현 |
| Hook service | SUCCESS | CRUD와 poll 조율 구현 |
| Headless runtime | SUCCESS | AgentRuntimeFactory 진입점 추가 |
| Runner/Loop | SUCCESS | queued 순차 실행과 주기 polling 구현 |
| Config/API/Lifespan | SUCCESS | 설정, router와 app 수명주기 연결 |
| Hardening | SUCCESS | queued recovery와 blocking IMAP 분리 |

## 실행 교훈

- 외부 IMAP 호출은 event loop를 막지 않도록 thread 경계에서 실행한다.
- 재시작 시 queued Hook Run을 runner에 다시 넣되 완료된 실행은 중복 처리하지 않는다.

## 검증

- [x] Hook schema, secret, service, adapter, runner, loop와 API test가 유지된다.
- [x] 현재 backend 전체 suite `454 passed`를 확인했다.
- [x] Ruff 검사가 통과한다.

## 문서 승격 및 정리

- architecture와 privacy 결정은 [Event Hooks 설계](../specs/2026-07-15-event-hooks-design.md)에 남긴다.
- frontend 연결은 [Event Hooks Frontend 완료 기록](completed_2026-07-15-event-hooks-frontend.md)이 소유한다.
- webhook, OAuth와 provider 확장은 원 계획의 범위 밖이므로 새 active plan이 생길 때까지 보류한다.
- 원본의 10개 TDD task와 전체 구현 스니펫은 제거했다.
