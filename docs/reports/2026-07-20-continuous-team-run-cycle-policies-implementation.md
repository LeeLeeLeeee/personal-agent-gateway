---
title: Continuous Team Run Cycle Policies Implementation
type: report
domain: team-run
feature: continuous-cycle-policies
status: done
aliases:
  - AUTO TRIGGERED 구현 결과
tags:
  - team-run
  - cycle
  - automation
updated_at: 2026-07-20
---

# Continuous Team Run Cycle Policies 구현 결과

## 구현 범위

- `src/personal_agent_gateway/team_cycles.py`가 AUTO Series와 Manual, Hook,
  AUTO, Retry CycleRequest를 저장하고 source idempotency, Run별 FIFO claim,
  pause와 terminal settlement를 관리한다.
- `src/personal_agent_gateway/team_cycle_dispatcher.py`와
  `src/personal_agent_gateway/team_cycle_loop.py`가 요청을 기존
  `TeamRunOrchestrator`에 직렬 전달하고 due AUTO slot을 enqueue한다.
- `src/personal_agent_gateway/hook_runner.py`, `hook_runs.py`, `hooks.py`가 HookRun을
  직접 Cycle 실행 경로 대신 공통 CycleRequest 큐와 연결한다.
- `src/personal_agent_gateway/app.py`가 startup interrupt와 reconcile 이후 Dispatcher,
  AUTO Loop를 순서대로 시작하고 shutdown 또는 시작 실패 때 역순으로 정리한다.
- frontend의 `TeamPicker`, `TeamRunDetail`, `HooksView`, `OperationsView`, API client와
  controller가 fixed CONTINUOUS 생성, AUTO/TRIGGERED 입력과 제어, policy 상태,
  이전 summary, TRIGGERED Hook target, Cycle event refresh를 제공한다.

## 상태 및 데이터 마이그레이션

- schema migration 11은 `team_runs.execution_policy`,
  `team_run_cycles.request_id`, `hook_runs.team_cycle_request_id`,
  `team_run_auto_series`, `team_cycle_requests`를 추가한다.
- partial unique index로 Run별 active AUTO Series 하나, dispatching Request 하나,
  Request별 Cycle 하나, HookRun별 CycleRequest 하나를 강제한다.
- 기존 continuous Run의 빈 policy는 `triggered`로 backfill한다. 기존 STANDARD
  기록은 변환하지 않고 조회와 기존 start, resume, add-work 호환 경로를 유지한다.
- AUTO 첫 slot은 Run 생성 transaction에서 즉시 생성한다. 다음 slot은 이전 slot
  settlement 시각과 interval로 계산하며, Retry는 같은 slot과 원본 previous-summary
  snapshot lineage를 유지하고 Continue만 실패 slot을 정산한다.

## API와 UI

- 신규 Team Run API는 `auto | triggered` 중 하나를 요구하고 lifecycle을 continuous,
  run mode를 `plan_and_execute`, worker 수를 1로 고정한다. AUTO만 repeat count와
  interval을 받는다.
- manual trigger, AUTO retry, continue, restart endpoint는 service의 상태와 소유권
  검증을 재사용하고 queued Request가 생길 때 Dispatcher를 깨운다.
- detail과 Operations 응답은 policy status, active Series, queue count, active Request와
  Cycle lineage를 노출한다. SSE는 Cycle 시작·정산 및 AUTO pause·continue·complete를
  갱신 신호로 제공한다.
- 신규 생성 UI에는 STANDARD 또는 run-mode selector가 없고 AUTO/TRIGGERED만 표시한다.
  AUTO는 진행률, 다음 실행 countdown, 상태별 action을 제공하며 TRIGGERED는 이전
  정산 Cycle summary를 확인하고 수동 instruction을 제출한다. Hook target은
  TRIGGERED continuous Run으로 제한한다.

## 재시작 복구

- `tests/test_team_cycle_recovery.py`는 같은 SQLite DB를 fresh service와 Dispatcher로
  다시 열어 startup recovery matrix를 검증한다.
- running Cycle은 interrupt 후 `paused_interrupted`, waiting-for-user Cycle은
  `paused_user`가 되며 둘 다 기존 dispatching Request와 Cycle을 유지해 다음 claim을
  차단한다.
- terminal Cycle의 dispatching Request는 한 번만 정산하고, Cycle 없는 orphan claim은
  한 번만 queued로 되돌린다. due AUTO slot과 기존 queued Run은 반복 reconcile 또는
  재개 후에도 Request/Cycle을 중복 생성하지 않는다.
- failure pause의 Retry/Continue는 원래 Run, Series, slot 소유권을 유지한다. Manual과
  Hook은 재개 후에도 같은 insertion-order FIFO와 source idempotency를 유지한다.
- 복구 테스트 첫 실행은 `9 passed, 1 failed`였다. 실패한 한 assertion은 helper의 실제
  생성시각과 고정 과거시각 때문에 조회 순서만 뒤집힌 테스트 결함이었으며, ordinal과
  중복 계약을 검증하도록 수정했다. production recovery 코드는 변경하지 않았다.

## 검증 결과

- `pytest tests/test_team_cycle_recovery.py -q`: `10 passed in 10.49s`.
- `pytest tests/test_team_cycle_recovery.py tests/test_app_lifecycle.py -q`:
  `17 passed in 20.88s`.
- 첫 `pytest -q`: `590 passed, 2 errors in 541.21s`. 기존
  `tests/test_team_documents.py` fixture가 필수 `execution_policy` 없이 신규 Run을
  생성하던 drift였다. 승인된 Task 9 추가 범위로 해당 fixture에
  `execution_policy: triggered`만 추가했고 production validation은 바꾸지 않았다.
- `pytest tests/test_team_documents.py -q`: `2 passed in 4.66s`.
- 수정 후 `pytest -q`: `592 passed in 187.33s`.
- `ruff check src tests`: 통과.
- frontend `npm test -- --run`: `34` files, `247 passed`.
- frontend `npm run build`: `74` modules transformed, build 성공. runtime에 해석되는
  기존 vendor asset 경고 세 건은 유지됐고 compile error는 없었다.
- docs registry generator를 두 번 실행해 매번 `118 docs`를 기록했고 두 결과의 SHA-256
  `EBEC5C7074792413AEFC558A1902BABA665FCFE822C9B5EFDB8CC299632882E3`가 같았다.
- `git diff --check`: 통과.

## 남은 제한

- 여러 Cycle의 동시 실행, 한 Run에서 AUTO와 TRIGGERED의 동시 활성화, Cron 또는
  달력 기반 AUTO 일정은 지원하지 않는다.
- 실행 중 policy 변경, 기존 STANDARD 기록의 Continuous 변환, worker 전체에 이전
  transcript 직접 주입, Cycle 결과의 외부 시스템 자동 전송은 범위 밖이다.
- database terminal transition 이후 event publication은 at-most-once이며 외부 outbox나
  publication retry는 추가하지 않았다.
- Vite build의 `highlight.min.js`, `github-dark.min.css`,
  `PretendardVariable.woff2` runtime-resolution 경고는 기존 동작이며 이번 범위에서
  변경하지 않았다.
