---
title: 영속 Mail Team Run과 Hook Cycle 구현 계획
type: todo
domain: personal-agent-gateway
feature: hook-team-mail-workspace
status: done
aliases:
  - 메일 Team Hook 구현
  - Mail Team Run Cycle 구현
  - 메일 명함 수첩 작업 계획
tags:
  - event-hooks
  - team-run
  - email
  - workspace
updated_at: 2026-07-16
---

# 영속 Mail Team Run과 Hook Cycle 구현 완료

작성일: 2026-07-16
상태: done

`NAVER WORKS Inbox`를 Team/Rules snapshot 기반의 영속 continuous Mail Team Run `c7428e97d19d4bb8bcd609f91adaf35b`에 연결했다. 새 메일은 같은 Run 아래 고유 Cycle로 직렬 처리되고 DB 기반 Mail archive, 발신자 연락처, immutable `MAIL_CONTEXT.md`, `RESULT.md`로 투영된다. 기존 Agent Hook과 일반 Team Run 호환을 유지하며 SMTP 자동 발송은 구현하지 않았다.

## 단계별 완료 기록

| 상태 | 작업 | 잠금/실패 사유 | 검증 |
| --- | --- | --- | --- |
| SUCCESS | P0 stable workspace root를 `data/workspace`로 설정하고 기존 경로를 dry-run/backup 후 이전 | 완료: active Run 0건, `data/backups/p0-workspace-migration-20260716-160237` 백업, 원본부터 누락된 Run 1개는 빈 경로로 복구 | 3개 Run 파일/해시 일치, DB integrity `ok`, schema v5→v8 restart 회귀, Hook cursor/2개 이력 보존 |
| SUCCESS | P1 `team_runs.lifecycle_mode`, `team_run_cycles`, Task/Message/Decision의 `cycle_id` migration과 service 추가 | 완료: schema v6, idempotent Cycle 생성, lineage 검증 | migration idempotency, 일반 Run backfill/호환, targeted 79 tests |
| SUCCESS | P2 Team runtime의 planning/drain/synthesis/budget/resume을 active Cycle 범위로 변경 | 완료: Cycle별 Task/Message/synthesis/round budget 격리 | 2-cycle runtime 검증, 기존 Team/API 포함 81 tests |
| SUCCESS | P3 start/resume lifecycle을 `TeamRunOrchestrator`로 추출 | 완료: 동적 Runtime provider와 registry finally 정리 | orchestrator/API 회귀 30 tests |
| SUCCESS | P4 Hook target에 `target_team_run_id`, Hook Run에 unique `team_run_cycle_id`를 추가하고 serial queue 연결 | 완료: agent/team_run target, lifecycle observer, waiting/interrupted queue | Hook+Team 통합 113 tests |
| SUCCESS | P5 `mail_messages`, `mail_contacts`, idempotent Workspace projector 구현 | 완료: DB source of truth, Hook/Cycle lineage, startup retry, `NOTES.md` 보존 | Email Hook 통합 포함 117 tests, Ruff/diff/secret scan |
| SUCCESS | P6 `MAIL_CONTEXT.md` untrusted boundary와 mail별 RESULT projection 구현 | 완료: 외부 payload 비삽입 instruction, immutable context 변조 감지 | prompt injection/path isolation 포함 119 tests, Ruff/diff/secret scan |
| SUCCESS | P7 UI에 continuous Mail Team Run 생성/선택, Cycle 상태와 lineage 표시 | 완료: draft mailbox 생성, Hook target picker, Cycle history/lineage | frontend 96 + backend 51 tests, Vite build, Ruff/diff/secret scan |
| SUCCESS | P8 `NAVER WORKS Inbox`를 Mail Team Run에 연결하고 새 테스트 메일 1건 smoke | 완료: 신규 Hook Run `c8a333ed9b394de6a3c10350e0d98d9f`, Cycle `5d2a4d83888f4669b75b349e6f01720d` | 기존 Hook Run 2건 보존, 신규 1건·Cycle 1건·contact 1건, projection 성공, 즉시 재poll 0건, SMTP 경로 0개 |
| SUCCESS | P9 전체 회귀, privacy/secret scan, build, docs 완료처리 | 완료: Claude 다중행 prompt stdin 전환과 live 보정 검증 포함 | backend 489 + frontend 228 tests, build, Ruff, diff-check, exact secret scan, registry 통과 |

## 실행 중 발견한 실패와 교훈

- 첫 live Cycle은 Hook/Cycle/DB/Workspace 투영에는 성공했지만 Claude Worker가 여러 줄 prompt의 첫 줄만 받아 실제 메일 검토를 수행하지 못했다.
- Windows에서 Claude prompt를 positional argument로 넘기던 방식을 `stdin=PIPE`로 바꾸고 다중행 회귀 테스트를 추가했다. Worker 공통 prompt의 코드 작업 전용 문구도 작업 유형 중립적으로 수정했다.
- 기존 Hook Run과 Cycle을 삭제하거나 복제하지 않고 같은 Cycle에 보정 Task 1개를 추가해 resume했다. 최종 Cycle summary, Hook Run result, Mail DB result, `RESULT.md`가 동일하고 `P8 smoke test` 제목을 포함함을 확인했다.

## 최종 검증

- [x] stable `data/workspace` 이전, 백업 DB integrity와 restart 검증
- [x] live Mail Team Run 1개, 신규 Hook Run 1개, Cycle 1개 생성
- [x] 기존 Hook Run 2건과 cursor/connection reference 보존
- [x] Mail DB, 연락처, `MAIL_CONTEXT.md`, `RESULT.md` projection 성공
- [x] 즉시 재poll 신규 0건과 SMTP 발송 경로 0개 확인
- [x] backend 489 tests, frontend 228 tests, Vite build 통과
- [x] Ruff, `git diff --check`, exact secret scan 통과

## 문서 승격 및 정리

- 아키텍처 결정과 대안은 accepted [영속 Mail Team Run ADR](../adr/2026-07-16-hook-team-mail-workspace.md)에 유지했다.
- 반복 가능한 운영 순서는 done [메일 Hook Team Run 흐름](../flows/2026-07-16-mail-hook-team-run.md)에 유지했다.
- 도메인 관계와 Claude CLI stdin 규칙은 [Runtime 도메인 관계 지도](../knowledge/2026-07-16-runtime-domain-relationship-map.md)에 유지했다.
- 초기 Assumptions, 목표 초안, 중복 Architecture Review, 상태 규칙, 명령 목록은 위 문서와 이 완료 기록으로 대체해 제거했다.

## Related

- [영속 Mail Team Run ADR](../adr/2026-07-16-hook-team-mail-workspace.md)
- [메일 Hook Team Run 흐름](../flows/2026-07-16-mail-hook-team-run.md)
- [Runtime 도메인 관계 지도](../knowledge/2026-07-16-runtime-domain-relationship-map.md)
