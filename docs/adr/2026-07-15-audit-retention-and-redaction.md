---
title: Audit 보존과 민감 정보 제외 정책
type: adr
domain: personal-agent-gateway
feature: audit-retention-redaction
status: active
decision_status: accepted
aliases:
  - audit 보존 정책
  - 로그 redaction 정책
  - prompt 저장 여부
tags:
  - audit
  - privacy
  - redaction
updated_at: 2026-07-15
---

# Audit 보존과 민감 정보 제외 정책

## Context

Transcript, Job event, SSE는 각각 대화 재현·작업 표시·live 갱신 목적이며 사고 분석용 durable audit가 아니다. 2026-07-08 초안은 prompt 기록을 제안했지만 R1 RULE은 prompt/output/file 본문을 audit에 저장하지 않도록 정했다.

## Decision

- Audit는 별도 SQLite `audit_events`에 append-only로 저장한다.
- 기본 보존 기준은 90일이며 R1에서는 read filter에 적용하고 자동 삭제 API는 만들지 않는다.
- session/team/task/job/artifact ID, actor, action, status, severity, correlation ID와 sanitized metadata만 저장한다.
- prompt 원문, raw stdout/stderr, recovery code, OTP secret, file content는 저장하지 않는다.
- command는 redaction 후 500자 preview만 허용하며 환경 secret과 private-key block을 제거한다.
- IP와 user-agent가 필요할 때는 원문 대신 hash를 저장한다.
- public update/delete API는 제공하지 않는다.

## Alternatives

### Prompt와 command 전체 저장

사고 재현성은 높지만 secret과 개인 데이터가 durable DB와 backup에 중복돼 피해 범위를 키운다. 원문이 필요하면 transcript 또는 Job artifact에 대한 ID 연결로 확인한다.

### 기존 transcript/job event 재사용

저장소는 줄지만 actor/correlation/보존·redaction 정책이 서로 달라 append-only 보안 이력으로 사용할 수 없다.

## Consequences

- Audit만으로 대화 내용을 완전히 재현할 수는 없다.
- 동일 correlation ID로 structured log와 domain record를 연결해야 한다.
- audit write 실패는 작업을 무조건 중단하지 않되 local error log에 남긴다. access mode 전환과 Emergency Stop은 API 실패로 돌려보내는 fail-closed action으로 취급한다.

## Follow-ups

- JSONL export와 retention purge는 별도 owner action으로 추가한다.
- redaction fixture와 append-only API test를 유지한다.
