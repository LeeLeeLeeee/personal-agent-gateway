---
title: R1 읽기 성능 기준선과 budget
type: report
domain: personal-agent-gateway
feature: read-performance
status: active
tags:
  - performance
  - pagination
  - sqlite
updated_at: 2026-07-15
---

# R1 읽기 성능 기준선과 budget

## 결론

Windows 11, Python 3.13.13 로컬 환경의 임시 fixture에서 1,000개 Session 목록 p95는 JSONL 전체 스캔 133.899ms에서 SQLite metadata index 3.257ms로 줄었다. 기본 페이지는 Session·Job·Artifact·Team Run 100개, event·Team detail·document 200개로 제한했다.

측정은 기존 사용자 data root와 Team Run을 사용하지 않고 `TemporaryDirectory`에서만 수행했다. 재현 명령은 다음과 같다.

```powershell
$env:PYTHONPATH='src'
python scripts/measure_read_performance.py
```

## D1-5 budget

| 읽기 경로 | fixture | budget | 측정 p95 | 결과 |
| --- | ---: | ---: | ---: | --- |
| Session list metadata page | 1,000 sessions / page 100 | 50ms | 3.257ms | PASS |
| Session metadata 최초 rebuild | 1,000 sessions | 2,000ms | 272.358ms | PASS |
| Session activity page | 10,000 events / page 200 | 50ms | 2.973ms | PASS |
| Team task list | 100 tasks | 50ms | 1.963ms | PASS |
| Team document summary | 1,000 files | 100ms | 37.933ms | PASS |
| 기본 page payload | page 100/200 | 256KiB | 최대 39,002B | PASS |
| Team task SSE 중간 이벤트 | selected run | full detail 0회 | delta 적용 | PASS |
| Team terminal/reconnect | selected run | detail 최대 1회 | aggregate refetch 1회 | PASS |

## 측정표

| 경로 | 개수 | 변경 전 p50/p95 | 변경 후 p50/p95 | payload |
| --- | ---: | ---: | ---: | ---: |
| Session list | 10 | 1.581 / 1.821ms | 1.975 / 2.494ms | 2,889B |
| Session list | 100 | 14.015 / 15.419ms | 2.364 / 2.768ms | 28,989B |
| Session list | 1,000 | 128.430 / 133.899ms | 2.650 / 3.257ms | 29,099B |
| Activity page | 100 | - | 1.740 / 2.222ms | 19,084B |
| Activity page | 1,000 | - | 2.558 / 2.842ms | 38,602B |
| Activity page | 10,000 | - | 2.627 / 2.973ms | 39,002B |
| Team task list | 10 | - | 1.647 / 2.181ms | 10 rows |
| Team task list | 100 | - | 1.861 / 1.963ms | 100 rows |
| Document summary | 100 | - | 3.057 / 3.637ms | aggregate |
| Document summary | 1,000 | - | 34.214 / 37.933ms | aggregate |

10개 Session에서는 SQLite connection 비용 때문에 index 경로가 약 0.7ms 느리다. 100개부터 이 비용을 상쇄하며, 1,000개에서 약 41배 빠르다.

## Query plan 확인

실제 `EXPLAIN QUERY PLAN`과 `tests/test_read_performance.py`에서 다음 index 사용을 고정했다.

- Job status/list: `idx_jobs_status_created`
- Job history: `idx_job_events_job_created`
- Artifact list: `idx_artifacts_created`
- Team Run status/list: `idx_team_runs_status_created`
- Team task status: `idx_team_tasks_run_status_created`
- Team detail task order: `idx_team_tasks_run_created`
- Activity history: `idx_session_activity_events_session_seq`

## 적용 사항

- Session title, timestamp, message count, status, agent 설정을 `transcript_metadata`에 유지한다. 기존 JSONL은 source of truth이며 시작 시 index를 재구축한다.
- Cursor는 정렬 키와 ID를 opaque base64 JSON으로 전달한다. 잘못된 cursor는 400이며 각 페이지 사이 누락·중복 test를 둔다.
- Team 목록의 run별 agent/task N+1 읽기를 선택된 run 집합에 대한 2개 aggregate query로 바꿨다.
- Team 상세의 run/agents/tasks/messages/document summary를 단일 HTTP endpoint로 묶었다.
- Team SSE는 run/task delta를 적용하고 terminal 또는 구버전 이벤트에서만 aggregate detail을 다시 읽는다.
- SQLite 편의 메서드가 connection을 닫도록 보장해 Windows file lock과 장기 connection 누수를 제거했다.

## 보존 기준

- Audit은 ADR에 따라 90일 read window를 적용하고 자동 삭제·export는 별도 owner action으로 둔다.
- Activity와 Job event는 복구·실행 근거이므로 R1에서 시간 기반 자동 삭제하지 않는다. 대신 API page 상한을 강제하며 parent 삭제 시 cascade한다.
- 단일 Session activity 또는 Job event가 100,000개를 넘거나 90일 이상 데이터가 실제 운영에서 확인되면 archive/export를 다음 migration으로 결정한다. 측정 근거 없이 자동 purge하지 않는다.

## 제한과 후속

- Document summary는 아직 filesystem scan이므로 파일 수가 10,000개를 넘으면 manifest cache 측정이 필요하다.
- Session 검색은 metadata title 기준이다. transcript 본문 index는 privacy opt-in과 rebuild 정책이 정해질 때까지 만들지 않는다.
- 수치는 개발 PC의 local budget이며 네트워크 tunnel 지연을 포함하지 않는다.
