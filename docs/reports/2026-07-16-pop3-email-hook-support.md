---
title: POP3 Email Hook 지원 및 NAVER WORKS 연결 보고서
type: report
domain: personal-agent-gateway
feature: event-hooks-email-pop3
status: done
aliases:
  - POP3 메일 Hook 설정
  - NAVER WORKS 메일 연동
  - pop.worksmobile.com 연결
tags:
  - event-hooks
  - email
  - pop3
  - naver-works
updated_at: 2026-07-16
---

# POP3 Email Hook 지원 및 NAVER WORKS 연결 보고서

## Summary

기존 IMAP email adapter가 Host/Port에 따라 POP3 SSL도 처리하도록 확장했다. NAVER WORKS POP3 실계정 연결과 최초 baseline Poll을 검증하고, `NAVER WORKS Inbox` Hook을 활성화했다. 최초 Poll은 기존 메일을 실행하지 않고 이후 새 메일부터 처리한다.

## Changes

- `995` 포트 또는 `pop.*` Host를 POP3로 자동 판별한다.
- POP3 `UIDL`을 cursor로 사용해 마지막 처리 위치 이후의 새 메일만 가져온다.
- cursor UID가 서버에서 사라진 경우 현재 mailbox를 새 baseline으로 잡아 과거 메일을 재실행하지 않는다.
- 빈 mailbox에서 시작한 뒤 처음 도착한 메일은 정상 감지한다.
- 연결 비밀번호는 DB나 문서가 아니라 기존 `HookSecretStore` 아래 ignored local file에만 저장한다.
- Hook Agent는 read-only로 실행하며 메일 속 지시를 수행하지 않고 요약, 긴급도, 후속 조치만 제안한다.
- 수동 setup 과정에서 code page로 깨진 한글 prompt를 UTF-8 문장으로 다시 저장하고 placeholder를 검증했다.

## Verification

- NAVER WORKS POP3 SSL 로그인: SUCCESS
- 최초 실계정 Poll: 새 Hook Run 0건, `protocol=pop3`, error 없음
- email adapter 및 Hooks API/service: 28 passed
- backend 전체 pytest: 464 passed
- frontend 전체 Vitest: 32 files / 222 tests passed
- Ruff 및 Python compile: SUCCESS
- 저장 prompt UTF-8/placeholder 검증: SUCCESS

## Follow-ups

- 현재 Event Hook은 수신 메일 처리만 지원한다. SMTP 발송은 별도 요구와 보안 경계를 정한 뒤 구현한다.
- 해당 계정의 IMAP은 관리자 권한에서 비활성화되어 있으므로 현재 연결은 POP3를 사용한다.
