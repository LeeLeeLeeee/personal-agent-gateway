---
title: Agent Teams Backend 완료 기록
type: todo
domain: personal-agent-gateway
feature: agent-teams-backend
status: done
aliases:
  - Agent Teams 백엔드 완료
  - Teams Rules backend 완료
tags:
  - teams
  - rules
  - backend
  - api
updated_at: 2026-07-16
---

# Agent Teams Backend 완료 기록

## 결과 요약

재사용 가능한 Team과 global/team/persona Rule Set을 저장하는 schema와 service를 구현했다. Team 기반 Run 생성 시 roster와 규칙을 snapshot으로 고정하고, 실행 prompt에 주입하며 목록 aggregate와 안전한 workspace document API를 제공한다.

## 단계별 상태

| 단계 | 상태 | 결과 |
| --- | --- | --- |
| Schema | SUCCESS | teams, rule_sets와 team_run 연계 필드 추가 |
| RuleSetService | SUCCESS | scope별 규칙 CRUD와 기본 seed 구현 |
| TeamService | SUCCESS | roster CRUD와 leader/member 제약 구현 |
| Run snapshot | SUCCESS | Team과 규칙 snapshot 기반 Run 생성 |
| Runtime injection | SUCCESS | leader/worker prompt에 동결 규칙 주입 |
| Enriched list | SUCCESS | leader/member/task/elapsed aggregate 제공 |
| Documents API | SUCCESS | workspace 경로 이탈을 막는 목록·내용 조회 |
| Router wiring | SUCCESS | Teams/Rules/Team Run API 연결 |

## 실행 교훈

- Run 재현성을 위해 현재 Team/Rule을 직접 참조하지 않고 생성 시 snapshot을 고정한다.
- 규칙 scope와 Team 존재 여부는 service/API 경계에서 검증한다.

## 검증

- [x] DB, Team, Rule, Team Run과 document API test가 유지된다.
- [x] 현재 backend 전체 suite `454 passed`를 확인했다.
- [x] Ruff 검사가 통과한다.

## 문서 승격 및 정리

- 설계와 데이터 계약은 [Agent Teams Rules 설계](../specs/2026-07-14-agent-teams-rules-and-teams-design.md)에 남긴다.
- 운용 지식은 [Persona Team 사용 가이드](../../knowledge/persona-team-usage-guide.md)가 소유한다.
- 원본의 SQL, service와 endpoint 구현 recipe는 제거했다.
