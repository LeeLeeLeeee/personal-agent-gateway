---
title: Gateway 자동화 lifecycle과 R0 실행 계약
type: adr
domain: personal-agent-gateway
feature: automation-lifecycle
status: active
decision_status: accepted
aliases:
  - PAG Worker Scheduler lifecycle 결정
  - D0-2 D0-3 D0-4 자동화 결정
  - Chat Job mirror 정책
tags:
  - worker
  - scheduler
  - lifecycle
  - team-run
updated_at: 2026-07-15
---

# Gateway 자동화 lifecycle과 R0 실행 계약

## Context

`JobWorker`와 `SchedulerLoop`는 start/stop 구현이 있지만 app startup에 연결되지 않았다. Team Run UI는 실제로 동작하지 않는 Review mode와 `max_workers` 병렬 실행을 노출한다. Chat shell approval은 명령을 직접 실행하지만 동시에 별도 Job row를 생성해 Job worker 경로와 실행 소유권도 다르다.

## Decision

- Gateway는 single-process automation을 지원한다.
- FastAPI lifespan이 DB recovery, Worker start, non-Chat queued Job 재enqueue, Scheduler start와 역순 shutdown을 소유한다.
- Restart 때 `running` Job은 failed로 수렴하고 `queued` manual/schedule/api Job은 재enqueue한다.
- R0 Team execution은 Sequential이며 Review mode는 API/UI에서 거부·숨긴다. Effective worker 수는 1이다.
- Chat runtime이 Chat shell command의 유일한 실행 주체다. `source=chat` Job은 R0에서 이력 mirror이며 Job approve/worker enqueue 대상이 아니다.
- Worker/Scheduler의 task 생존 상태를 settings capability로 노출한다.

## Alternatives

### 외부 queue 또는 multi-process worker

현재 단일 사용자·로컬 process 범위를 넘어선다. 정확한 single-process lifecycle과 recovery를 먼저 검증한다.

### Chat approval을 즉시 Job worker로 통합

기존 Chat runtime도 승인 후 직접 shell을 실행하므로 성급히 합치면 동일 command가 두 번 실행될 수 있다. R1에서 이력 동기화와 migration을 별도로 설계한다.

### Review와 concurrency를 R0에서 구현

인증과 background lifecycle 회복보다 실패 반경이 크다. R0은 제품 문구를 실제 동작에 맞추고 새 실행 variant는 R2에서 구현한다.

## Consequences

- Server lifespan 안에서만 automation이 ready가 된다.
- App context를 열지 않는 단위 test는 Worker/Scheduler를 자동 기동하지 않는다.
- 저장된 legacy `max_workers`가 1보다 커도 API의 effective 실행 수는 1로 표시한다.
- Chat Job은 R1 동기화 전까지 Job 화면의 terminal 상태와 실제 Chat 결과가 완전히 일치하지 않을 수 있다.

## Follow-ups

- R1에서 Chat Job mirror의 approval/status lineage를 명시한다.
- R2에서 target 기반 Review Strategy와 workspace conflict 정책을 구현한 뒤 bounded concurrency를 연다.
