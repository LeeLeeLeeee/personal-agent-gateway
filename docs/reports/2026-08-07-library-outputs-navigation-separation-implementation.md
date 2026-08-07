---
title: Library와 Outputs navigation 분리 구현 결과
type: report
domain: personal-agent-gateway
feature: library-outputs-separation
status: done
aliases:
  - Library Outputs 분리 결과
  - Archive 2단계 개편 결과
tags:
  - archive
  - artifacts
  - navigation
updated_at: 2026-08-07
---

# Library와 Outputs navigation 분리 구현 결과

## Summary

사용자용 Archive 진입점을 Library와 Outputs로 분리했다. Library는 Published,
Drafts, Requests Knowledge workflow만 소유하고, Outputs는 기존 Artifact browser를
독립 top-level 화면으로 제공한다. Archive/Artifact backend 계약은 변경하지 않았다.

## Changes

- Sidebar와 `GatewayApp.screen`을 `library`와 `outputs`로 분리했다.
- Library 진입에서 Artifact fallback 조회와 embedded Artifact UI를 제거했다.
- Outputs가 Artifact browser와 mutation refresh를 직접 제공한다.
- Library base load와 Requests-only Team Run error ownership을 분리했다.
- 화면별 async completion에 screen/request generation을 적용해 navigation 뒤 stale
  error/state와 ABA overwrite를 차단했다. Chat/Outputs Artifact refresh와 Operations
  mutation callback은 render-time owner에 묶인다.
- Library의 user/accessibility 명칭을 heading, region, totals, guide, tabs, loading에 맞췄다.
- Archive 내부 domain/API 이름과 모든 backend code를 유지했다.
- tracked production frontend assets를 검증된 source에서 다시 생성했다.

## Verification

- Focused frontend: Task 3의 Outputs fallback refresh test는 1 file에서 1 passed,
  56 skipped였고, 4개 focused test file은 110 passed였다.
- Final-review regressions: base/Team Run error ownership, stale initial Outputs load,
  mutation refresh after navigation, post-unmount delete callback, Outputs ABA refresh,
  pending Operations confirm, Library accessibility copy를 RED→GREEN으로 확인했다.
- Full frontend: `--maxWorkers=1`로 40 test files, 362 tests가 모두 passed했다.
- Production build: Vite v6.4.3가 80 modules를 4.43s에 변환했다. HTML은 1.00 kB
  (gzip 0.55 kB), CSS는 104.29 kB (gzip 17.36 kB), JavaScript는 468.25 kB
  (gzip 132.87 kB)였다.
- Build에는 기존 vendor 경로 경고 세 건이 그대로 있었다: non-module
  `highlight.min.js`, build 시점에 없는 `github-dark.min.css`, 해석되지 않은
  `PretendardVariable.woff2`.
- Static scans: archive screen key 0건, embedded Outputs 0건, Archive Map
  production/generated-asset 0건이었다.
- Backend boundary: 지정한 Archive/Artifact backend source와 contract test diff는
  0 paths였다. 전체 backend suite는 이번 단계에서 실행하지 않았으며, 앞선 단계에서
  확인한 repository baseline failures와 구분한다.
- `git diff --check`와 staged whitespace check는 모두 exit 0, 출력 없었다.

## Follow-ups

- 다음 단계는 Jobs, Schedules, Hooks를 Automations shell로 통합하는 별도 설계다.
- Library editor와 Requests panel 내부 분리는 실제 유지보수 압력이 확인될 때 별도 계획으로 다룬다.
