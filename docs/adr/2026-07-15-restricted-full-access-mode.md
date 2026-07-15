---
title: Restricted와 Full Access 실행 모드
type: adr
domain: personal-agent-gateway
feature: access-mode
status: active
decision_status: accepted
aliases:
  - Restricted Full Access 정책
  - 외부 파일 접근 정책
  - gateway 실행 권한 모드
tags:
  - security
  - access-mode
  - artifacts
updated_at: 2026-07-15
---

# Restricted와 Full Access 실행 모드

## Context

Gateway는 로컬 파일과 command를 다룰 수 있지만 현재 UI/API는 실제 접근 경계를 명시하지 않는다. 특히 Artifact 등록 API는 workspace 밖 absolute path도 허용해 로그인 session 탈취 시 피해 범위가 넓다.

## Decision

- 기본값은 `restricted`다.
- `restricted`에서는 browser API를 통한 workspace 밖 파일 등록을 거부한다.
- `full_access`는 소유자가 경고를 확인한 명시적 전환으로만 활성화한다.
- mode는 SQLite runtime setting으로 유지하고 Settings에서 현재 값과 workspace write 상태를 표시한다.
- mode 변경과 Full Access의 외부 path 등록은 session/correlation이 연결된 audit 대상이다.
- mode 변경이 Codex/Claude 자체 sandbox 설정을 암묵적으로 바꾸지는 않는다. 실제 sandbox와 access mode를 각각 표시한다.

## Alternatives

### Full Access 기본값

기존 운영 모델의 사용성 방향과 맞지만 현재 revoke/audit/backup이 아직 없는 상태에서 안전 기본값으로 삼기 어렵다.

### 환경 변수 전용 read-only mode

구현은 작지만 사용자가 현재 console에서 명시적으로 전환·확인할 수 없고 mode 변경 audit도 남길 수 없다.

## Consequences

- 기존 외부 path 등록 동작은 기본 설정에서 403으로 바뀐다.
- 내부 runner의 artifact 수집은 browser path policy와 분리해 유지한다.
- Full Access에서도 secret denylist와 OS 권한 경계는 후속 보안 장치로 남는다.

## Follow-ups

- Settings에 mode 전환 경고와 현재 workspace write 상태를 제공한다.
- 외부 path 등록 audit와 Emergency Stop/backup을 R1에서 연결한다.
