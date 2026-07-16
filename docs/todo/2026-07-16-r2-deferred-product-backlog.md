---
title: Personal Agent Gateway R2 후속 제품 가설 backlog
type: todo
domain: personal-agent-gateway
feature: r2-deferred-product-hypotheses
status: active
aliases:
  - R2 남은 작업
  - R2 후속 backlog
  - Result Template Search Review Concurrency
tags:
  - r2
  - backlog
  - product-validation
updated_at: 2026-07-16
---

# Personal Agent Gateway R2 후속 제품 가설 backlog

작성일: 2026-07-16
상태: active — 구현 대기 목록이 아니라 실제 사용 근거를 기다리는 가설 목록

## 배경

R2 최초 계획에는 Result package, Template, Search/Metrics, Review, Persona-local concurrency가 포함됐지만 실제 사용 1회에서는 구현 필요성이 확인되지 않았다. 완료된 R2 플랜에서 미래 작업을 분리하고, 각 항목은 아래 해제 증거가 생길 때만 새 실행 플랜으로 만든다.

## 체크리스트

| 상태 | 가설 | 잠금 사유 | 해제 증거 |
| --- | --- | --- | --- |
| LOCK | R2-A Result package와 delete preview | 결과 위치를 바로 찾았고 cross-source 왕복·삭제 영향 혼란이 관찰되지 않음 | 같은 Run의 task/document/artifact 반복 왕복 또는 삭제 범위 판단 실패 사례 |
| LOCK | R2-C Reusable work template | 반복 입력이 불편하지 않았음 | 반복 Run 생성에서 구성 재입력 비용이 실제로 반복됨 |
| LOCK | R2-D Global search와 local-only metrics | 최신순 탐색 보정으로 현재 문제 해결, global search 요구 없음 | source를 몰라 결과를 찾지 못한 사례 또는 지표 기반 결정 필요 |
| LOCK | R2-E 실제 Review workflow | target/finding/verification 계약의 실제 사용 요구 없음 | 구체 target을 반복 검토하고 재현 가능한 finding이 필요한 사례 |
| LOCK | R2-F Persona-local concurrency | Team Task 순차 실행을 선호하고 CLI capability 계약 미확인 | 독립 하위 업무의 반복 대기 비용과 공식 cancel/write isolation capability |
| LOCK | 닫힌 페이지 알림 또는 webhook | 열린 탭 Browser Notification으로 현재 대기 문제 해결 | 탭을 닫은 상태의 delivery가 반복적으로 필요하고 privacy/provider 계약 승인 |

## 원칙

- 구조적 가능성이나 기존 문서의 wishlist만으로 `LOCK`을 해제하지 않는다.
- 해제 증거가 생기면 해당 항목만 새 todo/ADR로 분리한다.
- Team Task 순차 실행과 현재 notification privacy 경계는 새 결정 전까지 유지한다.

## 관련 문서

- [R2 완료 실행 기록](2026-07-15-r2-product-expansion-execution-plan.md)
- [R2 구현 보고서](../reports/2026-07-16-r2-scoped-product-expansion-implementation.md)
- [R2 실제 사용 검증](../reports/2026-07-15-r2-g2-user-validation.md)
