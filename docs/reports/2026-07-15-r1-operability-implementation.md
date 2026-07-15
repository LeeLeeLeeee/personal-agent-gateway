---
title: Personal Agent Gateway R1 운영 가능성 구현 보고서
type: report
domain: personal-agent-gateway
feature: r1-operability
status: done
aliases:
  - PAG R1 구현 결과
  - NEXT Release Gate 보고서
  - 운영 복구 성능 구현 보고서
tags:
  - implementation
  - operations
  - recovery
  - performance
updated_at: 2026-07-15
---

# Personal Agent Gateway R1 운영 가능성 구현 보고서

## 결론

R1-A부터 R1-G까지 구현하고 NEXT Release Gate를 통과했다. 운영자는 한 화면에서 DB, Worker, Scheduler, CLI, intake, cookie, tunnel 보고 상태와 workspace 쓰기 가능 여부를 확인하고, 실행 중단·재개·재시도·backup 검증·원래 상세 화면 이동을 수행할 수 있다.

검증은 모두 pytest 임시 data/workspace root를 사용했다. 기존에 생성된 Team Run과 사용자 workspace는 테스트에 사용하거나 삭제하지 않았다.

## 구현 결과

| 묶음 | 상태 | 결과 |
| --- | --- | --- |
| R1-A Security/Migration | SUCCESS | schema v4 순차 migration, hash auth session 목록·폐기, Restricted/Full Access, 외부 path 정책과 실제 Settings 진단 |
| R1-B Health/Audit | SUCCESS | live/ready 분리, correlation error envelope·local log, redaction된 append-only audit, 기간/filter/stable cursor 조회 |
| R1-C Stop/Backup | SUCCESS | intake gate, Chat·Team·Job emergency stop, resume, SQLite online backup, checksum/schema dry-run과 별도 target restore round-trip |
| R1-D Automation | SUCCESS | Schedule 실행 이력·preview·detail, failed/canceled Job input 보존 retry와 lineage, UI 복구 action |
| R1-E Operations UI | SUCCESS | 공통 `ApiError`, Operations projection, diagnostics·deep link·Stop/Resume/Retry/Backup, correlation 복사와 데이터 보존 안내 |
| R1-F Read performance | SUCCESS | cursor pagination, transcript metadata index, Team aggregate read와 SSE delta, 실제 query-plan index와 측정 보고서 |
| R1-G Structure | SUCCESS | Chat/session router 분리, bootstrap/session/Team Run controller hook 추출, 기존 endpoint·payload·event·UI 동작 유지 |

## 주요 계약

- `/health/live`는 process 생존, `/health/ready`는 component 준비 상태를 나타낸다.
- Operations의 Emergency Stop은 gateway process를 종료하지 않고 새 intake를 닫은 뒤 Chat, Team Run, Job을 중단한다.
- Restore는 실행 중인 live DB를 HTTP로 교체하지 않는다. intake가 닫힌 상태에서 별도 target으로 복원·검증한 뒤 운영자가 전환한다.
- Audit는 prompt, raw output, secret, file body를 저장하지 않으며 기본 90일 read window를 적용한다.
- HTTP 실패는 status, code, detail, retryability, correlation ID를 보존하고 기존 화면 데이터를 빈 값으로 지우지 않는다.
- Job retry는 원본을 변경하지 않고 새 Job을 만들며 `source_job_id`로 계보를 남긴다.

## 성능 결과

세부 환경·fixture·query plan은 [읽기 성능 기준선](2026-07-15-read-performance-baseline.md)에 기록했다.

| 경로 | fixture | budget | 측정 p95 | 결과 |
| --- | ---: | ---: | ---: | --- |
| Session metadata page | 1,000 / page 100 | 50ms | 3.257ms | PASS |
| Session metadata rebuild | 1,000 | 2,000ms | 272.358ms | PASS |
| Session activity page | 10,000 / page 200 | 50ms | 2.973ms | PASS |
| Team task list | 100 | 50ms | 1.963ms | PASS |
| Team document summary | 1,000 | 100ms | 37.933ms | PASS |
| 기본 page payload | page 100/200 | 256KiB | 최대 39,002B | PASS |

1,000 Session 목록은 JSONL 전체 스캔 p95 133.899ms에서 metadata index p95 3.257ms로 줄었다.

## 구조 결과

- `src/personal_agent_gateway/app.py`: 406줄, composition·lifespan·router 연결 중심
- `src/personal_agent_gateway/api/chat_sessions.py`: Chat/session HTTP 계약 소유
- `frontend/src/components/containers/GatewayApp/index.jsx`: 748줄, 화면 조합과 navigation 중심
- `useGatewayBootstrap`: 인증·초기 read model
- `useSessionController`: 단일 EventSource, session cache, Chat command와 approval
- `useTeamRunController`: Team Run 선택·상세·문서·delta·복구 action

전면 재작성, global store, ORM, repository abstraction은 추가하지 않았다.

## 최종 검증

```powershell
python -m pytest
# 407 passed

python -m ruff check src tests scripts
# All checks passed

npm --prefix frontend test -- --run
# 29 files, 190 passed

npm --prefix frontend run build
# production build passed
```

`GatewayApp.test.jsx` 37개도 bootstrap, session, Team Run controller 추출 단계마다 통과했다. Vite build의 기존 static vendor 경로 경고는 남아 있지만 bundle 생성은 성공하며 R1 변경으로 생긴 경고가 아니다.

## 운영 제한과 다음 Gate

- Tunnel mode는 실행 환경에서 자동 탐지하지 못하면 `NOT REPORTED`로 명시한다.
- Audit 자동 purge와 JSONL export는 ADR에 따라 후속 owner action이다.
- Document summary는 filesystem scan이므로 10,000개 이상 실제 데이터에서 재측정한다.
- R2의 기술 진입 Gate는 열렸지만, 5~10회 실제 사용 기록이 없으므로 제품 확장 구현은 계속 잠근다.

## 관련 문서

- [R1 실행 플랜](../todo/2026-07-15-r1-operability-execution-plan.md)
- [Operations 진단 가이드](../knowledge/2026-07-15-operations-diagnostics-guide.md)
- [Emergency Stop 흐름](../flows/2026-07-15-emergency-stop-and-resume.md)
- [Backup/Restore 흐름](../flows/2026-07-15-backup-restore.md)
