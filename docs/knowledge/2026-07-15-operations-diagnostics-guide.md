---
title: Personal Agent Gateway Operations 진단 가이드
type: knowledge
domain: personal-agent-gateway
feature: operations-diagnostics
status: active
aliases:
  - PAG 운영 가이드
  - emergency stop 사용법
  - backup verify 사용법
  - correlation id 확인법
tags:
  - operations
  - diagnostics
  - recovery
updated_at: 2026-07-15
---

# Personal Agent Gateway Operations 진단 가이드

## 어디서 확인하는가

Sidebar의 **Operations**에서 서비스 준비 상태, 실행 상태, 중단·재개·재시도, backup을 확인한다. 인증 session과 Restricted/Full Access 전환은 **Settings**에서 관리한다.

## 진단값 해석

| 표시 | 정상 기준 | 비정상일 때 |
| --- | --- | --- |
| Database | `COMPLETED / ready` | DB path·권한·migration 오류를 local log의 correlation ID로 확인 |
| Worker | `COMPLETED / ready` | Job 자동 실행이 멈춘 상태이므로 gateway lifecycle과 worker last error 확인 |
| Scheduler | `COMPLETED / ready` | 예약 실행이 시작되지 않으므로 scheduler lifecycle과 cron/timezone 확인 |
| CLI | `COMPLETED / ready` | 선택된 필수 agent CLI 설치·로그인·탐지 결과 확인 |
| Intake | `COMPLETED / open` | Emergency Stop 상태다. 원인을 확인한 뒤 `Resume intake` 수행 |
| Cookie | 외부 HTTPS 사용 시 `SECURE` | 원격 노출 전에 HTTPS/tunnel과 secure cookie 설정을 먼저 맞춤 |
| Tunnel | 실제 mode 또는 `NOT REPORTED` | `NOT REPORTED`는 안전 판정이 아니라 자동 탐지되지 않았다는 의미 |
| Workspace write | `AVAILABLE` | workspace 존재 여부와 OS 쓰기 권한 확인 |

`/health/live`는 process 생존만 확인한다. 자동화 실행 가능 여부는 `/health/ready` 또는 Operations의 component별 상태를 사용한다.

## 실패 항목 복구

1. Operations row에서 상태와 domain을 확인한다.
2. `Open`으로 원래 Chat, Team Run, Job, Schedule 화면으로 이동해 상세 이력과 결과를 본다.
3. Team Run `interrupted`는 `Resume`, failed/canceled Job은 `Retry`를 사용한다.
4. Retry는 원본 Job을 덮어쓰지 않고 새 Job을 생성한다. 새 항목의 `source_job_id`로 원본을 추적한다.
5. 화면 오류에 correlation ID가 있으면 복사해 같은 ID의 local log와 audit event를 찾는다.

오류 화면에 “Existing local data was not cleared”가 보이면 마지막 정상 read model은 유지된 상태다. `Retry request`, `Refresh state`, `Sign in again` 중 표시된 action을 따른다.

## Emergency Stop과 재개

1. 위험하거나 꼬인 실행이 있으면 Operations의 `Emergency stop`을 누른다.
2. 확인 후 새 intake가 닫히고 active Chat, Team Run, Job cancel을 시도한다.
3. Operations 목록과 audit에서 중단 결과와 개별 실패를 확인한다.
4. 원인이 해결되기 전에는 intake를 열지 않는다.
5. 준비 상태가 정상일 때 `Resume intake`를 누른다. 중단된 Team Run은 별도로 `Resume`해야 한다.

Emergency Stop은 gateway process나 Schedule 정의를 삭제하지 않는다. 이미 완료된 Team task와 저장 데이터도 유지한다.

## Backup과 Restore 검증

1. Operations에서 `Create backup`을 실행한다.
2. 생성된 backup의 schema와 크기를 확인하고 `Verify`를 실행한다.
3. Verify는 manifest version, SHA-256, schema version, SQLite integrity를 검사한다.
4. 실제 restore는 먼저 Emergency Stop으로 intake를 닫는다.
5. live DB가 아닌 별도 target path에 복원해 initialize/read 검증을 통과시킨 뒤 운영자가 전환한다.

실행 중인 live DB를 HTTP 요청으로 바로 덮어쓰는 기능은 제공하지 않는다. Backup은 SQLite 본문과 auth/session/artifact metadata manifest를 포함하지만 workspace와 artifact 파일 본문 전체를 기본 복제하지 않는다.

## Audit 조회

`GET /api/audit/events`는 인증 session이 필요하며 다음 filter를 지원한다.

- `event_type`, `severity`, `actor_id`, `resource_type`, `correlation_id`
- `since`, `limit`, `cursor`

응답의 `next_cursor`를 다음 요청의 `cursor`로 전달한다. 기본 read window는 90일이다. Prompt, raw stdout/stderr, recovery code, OTP secret, file body는 audit에 저장하지 않는다.

## 운영 시 지키는 경계

- 실제 사용자 Team Run과 workspace를 자동화 테스트 fixture로 사용하지 않는다.
- Full Access는 외부 path 등록이 꼭 필요할 때만 명시적으로 켠다.
- Backup 검증 성공을 workspace 파일 전체 복구 성공으로 해석하지 않는다.
- Tunnel이 `NOT REPORTED`이면 외부 공개가 안전하다고 추정하지 않는다.

## 관련 문서

- [R1 구현 보고서](../reports/2026-07-15-r1-operability-implementation.md)
- [Emergency Stop 흐름](../flows/2026-07-15-emergency-stop-and-resume.md)
- [Backup/Restore 흐름](../flows/2026-07-15-backup-restore.md)
- [Access Mode ADR](../adr/2026-07-15-restricted-full-access-mode.md)
- [Audit ADR](../adr/2026-07-15-audit-retention-and-redaction.md)
