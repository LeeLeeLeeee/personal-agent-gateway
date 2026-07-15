---
title: Personal Agent Gateway R2 G2-1 실제 사용 검증 기록
type: report
domain: personal-agent-gateway
feature: r2-product-expansion
status: active
aliases:
  - R2 사용자 검증 기록
  - G2-1 실제 사용 기록
  - R2 진입 Gate 피드백
tags:
  - product-validation
  - g2-1
  - r2
  - usability
updated_at: 2026-07-15
---

# Personal Agent Gateway R2 G2-1 실제 사용 검증 기록

## Summary

- 최소 Gate: 실제 사용 5회
- 현재 기록: 1회
- 상태: `G2-1 LOCK` 유지
- 1회차 결과: Team Run 완료 결과 위치는 바로 찾았지만, 문서 preview와 목록 정렬, roster/Task 담당자/현재 업무 가시성, 완료 알림, Run 단위 강제 종료에 마찰이 있었다.

## Changes

### 실제 사용 #1

| 검증 항목 | 사용자 피드백 | 판단 |
| --- | --- | --- |
| 최종 상태 | 완료 | 정상 terminal 경험 확보 |
| 결과 확인 | 결과 위치를 바로 찾음 | Result 위치 자체는 문제 없음 |
| Document 목록 | 폴더도 표시되고 이미지 파일이 보이지 않음 | 열람 가능한 파일만 표시하고 image preview 필요 |
| HTML 확인 | HTML이 raw code로 열림 | 안전한 rendered preview 필요 |
| 완료 알림 | 완료까지 화면을 계속 지켜봄. 브라우저 알림 선호 | R2-B Browser Notification 근거 있음 |
| 반복 구성 | 다시 입력하는 것이 별로 귀찮지 않음 | R2-C Template 우선순위 낮음 |
| 실행 중단 | Team Run 하나를 강제 종료하는 기능 필요 | 전체 Emergency Stop과 별도인 Run 단위 종료 UX 필요 |
| Concurrency | 상위 Task는 순서대로 진행하는 편이 적절함 | Team 전체 Task 병렬화 선호 없음 |
| Persona 내부 병렬성 | Persona가 맡은 Task 안의 독립 업무는 병렬 처리 선호 | 동일 파일 writer는 순차 처리하는 제한적 병렬성 후보 |
| 복구 경험 | timeout, Resume, Retry 경험 없음 | 실제 발생 시 후속 기록 |
| 과거 결과 탐색 | Documents, Results, Live Activity가 오래된 순으로 표시됨 | 최신순 기본 정렬을 global search보다 먼저 수정 |
| Handoff 탐색 | Shared / Handoffs도 오래된 순으로 표시됨 | query/answer pairing 후 최신 활동순 projection 필요 |
| Task 담당자 | Task Board에서 담당 Persona를 식별하기 어려움 | profile image와 이름을 함께 표시하고 runtime owner assignment를 보존 |
| Team Run roster | Team Runs의 leader는 이름만, members는 이미지 위주로 표시됨 | leader/member 모두 snapshot profile image와 이름을 함께 표시 |
| Agent 현재 업무 | Agent Sessions 카드에서 수행 중 Task를 확인하기 어려움 | runtime의 `current_task_id`를 실제 갱신하고 Task 제목을 간략 표시 |

## Verification

- R2 실행 플랜의 G2-1 필드인 결과 확인, 알림 필요, 반복 구성, 검색 대상, concurrency 필요를 사용자 문답으로 기록했다.
- Recovery는 이번 사용에서 발생하지 않아 검증하지 않았다.
- 현재 생성된 Team Run이나 workspace를 자동화 fixture로 사용하지 않았다.

## Follow-ups

1. Documents에서 폴더를 제외하고 열람 가능한 파일만 표시한다.
2. 이미지와 HTML을 preview로 열며 HTML은 실행 권한을 제한한 안전한 렌더링 경계를 사용한다.
3. Documents, Results, Live Activity, Shared / Handoffs의 기본 정렬을 최신순으로 통일한다.
4. 브라우저 완료 알림을 R2-B 우선 후보로 유지한다.
5. 개별 Team Run 강제 종료와 terminal 상태 기록을 R2 전 운영 UX 보완 후보로 분리한다.
6. Concurrency는 상위 Task 순서를 유지하고 Persona 내부 독립 하위 업무에만 적용하는 정책으로 검토한다.
7. 실제 사용 기록 4회를 추가한 뒤 G2-1 해제 여부와 R2 범위를 결정한다.
8. Task assignment와 Agent current work 상태를 runtime source of truth에 기록하고 Task Board/Agent Sessions에 표시한다.
9. Team Runs roster에서 leader와 members의 snapshot avatar·name을 함께 표시한다.

## 관련 문서

- [R2 제품 확장 실행 플랜](../todo/2026-07-15-r2-product-expansion-execution-plan.md)
- [통합 서비스 개선 로드맵](../todo/2026-07-15-service-improvement-roadmap.md)
- [기획 PM 사용성·기능 기회](2026-07-15-product-pm-usability-opportunities.md)
