---
title: Backup과 Restore 검증 흐름
type: flow
domain: personal-agent-gateway
feature: backup-restore
status: active
aliases:
  - gateway 백업 복구
  - restore dry run
  - SQLite backup flow
tags:
  - backup
  - restore
  - recovery
updated_at: 2026-07-20
---

# Backup과 Restore 검증 흐름

## Summary

SQLite online backup과 핵심 저장소 manifest를 새 timestamp directory에 만들고, restore 전에 checksum·schema version·누락·충돌을 dry-run으로 확인한다. 현재 live DB를 API에서 바로 교체하지 않는다.

## Entry Points

- Operations 화면의 `Create backup`, `Verify backup`
- `POST /api/operations/backups`
- `POST /api/operations/backups/{id}/dry-run`

## Flow

1. SQLite online backup API로 일관된 DB copy를 새 backup directory에 만든다.
2. Manifest v2에 `database-only` profile, DB SHA-256/schema version, 저장소별
   recoverability, auth/session file metadata, artifact DB metadata, workspace root 상태를 기록한다.
3. Hook credential 값은 포함하지 않고 DB의 connection reference별 존재 여부·size·mtime만
   inventory한다.
4. backup directory와 manifest를 원본과 분리해 닫는다.
5. Dry-run이 manifest version, DB checksum, schema version, Hook credential reference
   inventory와 대상 충돌을 검사한다.
6. 누락된 enabled Hook credential은 SQLite 무결성 실패와 분리된 warning으로 반환한다.
7. 실제 restore service는 intake가 닫힌 maintenance 상태와 별도 target path에서만 실행한다.
8. 복원 DB를 열어 initialize/read 검증 후에만 round-trip 성공으로 본다.

## Edge Cases

- backup 원본은 restore나 재검증 과정에서 덮어쓰지 않는다.
- manifest checksum이 다르면 즉시 실패한다.
- 파일 본문 backup은 R1 기본 범위가 아니며 manifest에 포함 여부를 명시한다.
- Operations의 `database-only`와 `Not fully recoverable` 표시는 전체 Gateway 복구가
  아니라는 계약이다.
- 현재 app DB를 running process 안에서 교체하는 API는 제공하지 않는다.

## Verification

- 임시 DB/data root에서 backup → dry-run → 별도 target restore → DB row read를 검증한다.
- checksum 변조와 target 충돌이 실패하는지 검증한다.
- Hook secret 값이 manifest에 없고 누락 reference가 warning으로 반환되는지 검증한다.

## Related

- [R1 실행 플랜](../todo/2026-07-15-r1-operability-execution-plan.md)
- [Automation lifecycle ADR](../adr/2026-07-15-automation-lifecycle.md)
