---
title: Archive Map 제거 및 Documentation Team 지연 조회 구현 결과
type: report
domain: personal-agent-gateway
feature: archive-map-removal
status: done
aliases:
  - Archive Map 제거 결과
  - Archive Team Runs 지연 조회
tags:
  - archive
  - performance
  - cleanup
updated_at: 2026-08-07
---

# Archive Map 제거 및 Documentation Team 지연 조회 구현 결과

## Summary

Archive Map UI, frontend client, API route, backend graph read model과 전용
스타일을 제거했다. Archive 기본 조회에서 Team Runs를 제외하고 Requests
최초 진입 시 조회하며, 성공 결과는 컴포넌트가 유지되는 동안 재사용하고
실패 후에는 명시적으로 재시도하도록 변경했다.

## Changes

- Archive 기본 조회를 entries, drafts, personas, requests 네 요청으로 제한했다.
- Team Runs 실패가 Write, Later, Dismiss 같은 직접 Request 작업을 막지 않게 했다.
- client 전환 전 시작한 Team Runs 응답은 request generation으로 무효화한다.
- `/api/archive/map`은 인증 상태에서도 404를 반환한다.
- Library, Draft, Artifact, Knowledge Request workflow와 Artifact 소유 경계는 유지했다.
- 격리 워크트리에서 production frontend asset을 다시 생성해 반영했다.

## Verification

- Focused frontend: 3 files, 104 tests passed.
- Focused backend: 28 tests passed, 기존 Starlette/httpx deprecation warning 1건.
- Full frontend: 40 files, 356 tests passed with `--maxWorkers=1`.
- Full backend: 1,387 tests를 수집했지만 repository baseline 실패로 전체 GREEN은 아니다.
  - main에서도 Agent catalog 5건, emergency-stop 1건,
    `runtime_factory_headless` 16건의 동일한 22 failures가 재현됐다.
  - 격리 워크트리에서는 `.env`가 없어 `test_local_runtime_scripts.py` 1건이 추가된다.
- Production build: Vite exit 0, 80 modules transformed in 3.75s.
  - JavaScript: 468.26 kB, gzip 132.86 kB.
  - CSS: 104.77 kB, gzip 17.43 kB.
  - HTML: 1.00 kB, gzip 0.55 kB.
- Static removal scan: production source와 generated asset에서 Map 계약 0건.
  의도적인 제거 계약 테스트 문자열 3건만 유지했다.
- `git diff --check`: 통과.

Build는 기존 vendor asset 해석 경고 세 건을 그대로 출력했다:
`highlight.min.js`의 non-module script, build 시점에 없는
`github-dark.min.css`, 해석되지 않은 `PretendardVariable.woff2`다.

## Follow-ups

- 다음 단계는 Knowledge와 Outputs 정보 구조 분리 설계다.
- dependency audit의 기존 high severity 1건과 build vendor 경고는 별도 범위에서 진단한다.
