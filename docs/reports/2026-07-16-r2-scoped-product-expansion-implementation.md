---
title: Personal Agent Gateway R2 실제 사용 기반 범위 축소 구현 보고서
type: report
domain: personal-agent-gateway
feature: r2-product-expansion
status: done
aliases:
  - R2 구현 완료 보고서
  - R2-B 알림 완료
  - PG-1 R2 완료 결과
tags:
  - r2
  - implementation
  - usability
  - notification
updated_at: 2026-07-16
---

# Personal Agent Gateway R2 실제 사용 기반 범위 축소 구현 보고서

## Summary

R2는 최초 계획의 모든 제품 확장을 구현하지 않고 실제 사용 1회에서 관찰된 문제만 해결하는 범위로 축소해 완료했다. PG-1은 기존 Team Run 탐색·preview·assignment·중단 UX를 보정했고, R2-B는 열린 Gateway 탭의 opt-in Browser Notification을 제공한다. 사용자가 Windows/Chrome 알림을 활성화한 뒤 실제 완료 알림 수신을 확인했다.

## Changes

- Documents에서 지원 파일만 제공하고 raster image와 sandbox HTML preview를 추가했다.
- Documents, Results, Live Activity, Handoff를 최신 활동순으로 정렬했다.
- Task 담당 Persona, Agent current Task, leader/member roster identity를 실제 runtime snapshot과 SSE delta에 연결했다.
- 기존 Team Run cancel lifecycle을 `Stop run` UI에 연결했다.
- Browser Notification 상태와 permission 요청을 frontend adapter에 격리했다.
- completed/failed terminal event만 generic payload로 알리고 page-lifetime duplicate를 억제했다.
- 알림 클릭은 열린 Gateway에 focus하고 해당 Team Run을 선택한다.
- Backend provider, webhook, service worker, background push는 추가하지 않았다.

## Verification

- PG-1 완료 시 backend 454 tests, frontend 210 tests, Ruff, production build와 8787 smoke를 통과했다.
- R2-B targeted 50 tests와 frontend 전체 217 tests, production build를 통과했다.
- Post-R2-B regression에서 backend 454 tests, Ruff, frontend 217 tests와 production build를 다시 통과했다.
- 2026-07-16 사용자가 Windows/Chrome 알림 활성화 뒤 실제 Team Run 완료 알림 수신을 확인했다.
- 사용자 Team Run과 workspace는 자동화 fixture로 사용하지 않았다.

## Scope Decisions

- 결과 위치를 바로 찾았으므로 Result package는 만들지 않았다.
- 반복 입력이 불편하지 않았으므로 Template은 만들지 않았다.
- 최신순 보정으로 탐색 문제가 해결돼 Global Search/Metrics는 만들지 않았다.
- 실제 Review target 요구와 Persona-local concurrency capability 근거가 없어 runtime을 확장하지 않았다.
- 위 가설은 [R2 후속 제품 가설 backlog](../todo/2026-07-16-r2-deferred-product-backlog.md)에서 실제 근거가 생길 때만 다시 연다.

## Follow-ups

- 현재 R2 범위에는 남은 필수 구현이 없다.
- 닫힌 페이지 알림, webhook 또는 다른 제품 가설은 실제 사용 근거와 별도 결정이 생길 때 새 작업으로 시작한다.
