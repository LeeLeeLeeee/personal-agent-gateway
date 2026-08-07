---
title: Library와 Outputs navigation 분리 설계
type: adr
domain: personal-agent-gateway
feature: library-outputs-separation
status: active
aliases:
  - Archive Library Outputs 분리
  - Library Outputs 정보 구조
  - Archive 2단계 개편
tags:
  - archive
  - artifacts
  - information-architecture
  - navigation
updated_at: 2026-08-07
---

# Library와 Outputs navigation 분리 설계

## 목표

사용자용 `Archive` 화면을 재사용 지식인 `Library`와 작업 결과 파일인
`Outputs`로 분리한다. Library에는 `Published → Drafts → Requests`의 Knowledge
workflow만 남기고, Outputs는 기존 Artifact browser를 독립된 top-level 화면으로
제공한다.

이번 변경은 Phase 1의 Archive Map 제거 다음 단계다. 사용자 정보 구조와 frontend
screen ownership만 정리하며 Archive/Artifact backend domain과 저장 계약은 유지한다.

## 배경

현재 `ArchiveView`는 Library, Drafts, Requests와 Artifacts를 같은 tab list에 둔다.
화면 상단의 안내문은 Artifacts가 Library knowledge와 다르다고 설명하지만, 실제
navigation은 두 개념을 같은 Archive section으로 표현한다.

코드 경계는 이미 분리돼 있다.

- `ArchiveView`는 Archive entry, revision, persona scope, Knowledge Request와
  documentation Team delegation을 소유한다.
- `ArtifactsView`는 browser query, retention segment, cleanup preview, selection,
  preview와 mutation을 자체 state/API로 소유한다.
- `ArchiveView`는 `artifacts`와 `onArtifactChange`를 해석하지 않고
  `ArtifactsView`로 전달할 뿐이다.
- production caller는 두 component 모두 `GatewayApp` 경로 하나다.

Graphify traversal도 `ArchiveView()`와 `ArtifactsView()`의 연결이
`ArchiveView/index.jsx`의 직접 import를 통한 두 hop임을 확인했다. 소스 대조 결과
공유 domain state는 없었다. 상세 근거는
`docs/component-inspector/ArchiveView/2026-08-07-0956.md`에 기록했다.

## 결정

### 사용자 정보 구조

```text
Sidebar
├── Library
│   ├── Published
│   ├── Drafts
│   └── Requests
└── Outputs
    ├── Saved
    ├── Recent
    └── Cleanup
```

`Library`와 `Outputs`는 동급의 primary navigation item이다. 현재 Sidebar 순서에서
`Archive` 자리를 `Library`, `Outputs` 두 항목으로 교체한다.

```text
Dashboard
Chat
Jobs
Schedules
Hooks
Library
Outputs
Operations
Settings
```

Jobs/Schedules/Hooks의 Automations 통합은 후속 단계이므로 이번 변경에서 순서를
재구성하지 않는다.

### frontend screen key

`GatewayApp.screen`은 다음 명시적 key를 사용한다.

- `library`: `ArchiveView`를 렌더한다.
- `outputs`: `ArtifactsView`를 직접 렌더한다.

기존 `archive` screen key는 제거한다. 현재 screen state는 URL route나 persisted
preference가 아니므로 compatibility alias를 두지 않는다.

### 내부 이름 유지

다음 내부 이름은 그대로 둔다.

- `ArchiveView`
- `/api/archive/*`
- `ArchiveService`
- Archive entry/request database schema
- 일반 `archive-*` CSS selector

`Archive`는 user-facing navigation 이름으로는 넓고 모호하지만, 검토된 reusable
knowledge와 draft/request lifecycle을 저장하는 내부 domain 이름으로는 유효하다.
이번 단계에서 component, API, service, schema를 `Library`로 일괄 rename하면 동작상
가치 없이 diff와 회귀 범위만 커진다.

## frontend architecture

### GatewayApp

`GatewayApp`이 두 top-level 화면의 caller가 된다.

```mermaid
flowchart TD
  Sidebar -->|library| GatewayApp
  Sidebar -->|outputs| GatewayApp
  GatewayApp --> ArchiveView
  GatewayApp --> ArtifactsView
  ArchiveView --> ArchiveAPI[/api/archive/*]
  ArchiveView --> TeamAPI[/api/team-runs on Requests]
  ArtifactsView --> ArtifactAPI[/api/artifacts/*]
```

변경 사항:

1. `ArtifactsView`를 `GatewayApp`에서 직접 import한다.
2. `screen === "library"`일 때 `ArchiveView`를 Artifact props 없이 렌더한다.
3. `screen === "outputs"`일 때 `ArtifactsView`에 기존 `artifacts` fallback list와
   `onChange` refresh callback을 전달한다.
4. screen별 load effect는 `outputs`에서만 `api.artifacts()`를 호출한다.
5. Chat의 registered path와 Artifact action을 위해 `screen === "chat"`에서 수행하는
   `api.artifacts()` 조회는 유지한다.

### ArchiveView

`ArchiveView`는 Knowledge workflow만 렌더한다.

- 화면 heading: `Archive` → `Library`
- tab value/label:
  - `published` / `Published`
  - `drafts` / `Drafts`
  - `requests` / `Requests`
- 기본 tab: `published`
- `startNewEntry`, published `editEntry`, `beginRequestDraft`, fulfilled entry open은
  `published` tab으로 이동한다.
- `ArtifactsView` import와 `artifacts`, `onArtifactChange` props를 제거한다.
- Work Outputs guide card, Artifacts tab, boundary note와 panel을 제거한다.
- Knowledge lifecycle guide는 한 열의 full-width card로 유지한다.

Library editor, revision, delete, persona scope, Knowledge Request status/delegation과
Requests-only Team Run loading은 변경하지 않는다.

### ArtifactsView

`ArtifactsView`의 state와 API ownership은 유지한다.

- 화면 heading: `Artifacts` → `Outputs`
- Saved, Recent, Cleanup과 type/search filter는 유지한다.
- `artifacts` prop은 browser 결과가 없거나 browser 조회에 실패했을 때 사용하는
  fallback list로 유지한다.
- `onChange`는 delete, cleanup, pin 뒤 `GatewayApp`의 fallback list를 새로 읽는다.
- `ArtifactModal`, preview, provenance, selection deletion은 변경하지 않는다.

`ArtifactsView`를 rename하거나 hook/model로 분리하는 작업은 포함하지 않는다.

## 데이터 흐름

### Library 진입

```text
User selects Library
  -> GatewayApp renders ArchiveView without requesting Artifacts
  -> ArchiveView requests entries, drafts, personas, and requests in parallel
  -> Published tab renders
```

### Requests 진입

```text
User selects Requests
  -> Requests panel remains usable immediately
  -> teamRunsStatus idle: request Team Runs
  -> loading/error: disable delegation-only controls
  -> success: cache Team Runs until ArchiveView unmounts
```

Library에서 Outputs로 이동하면 `ArchiveView`가 unmount된다. 다시 Library의 Requests로
돌아오면 Team Runs를 새로 조회한다. 이는 다른 top-level screen 전환과 같은 기존
mount lifecycle이며 별도 global cache를 추가하지 않는다.

### Outputs 진입

```text
User selects Outputs
  -> GatewayApp requests /api/artifacts fallback list
  -> ArtifactsView requests /api/artifacts/browser
  -> browser result wins when available
  -> browser failure leaves fallback list usable
```

Artifact mutation 성공 후 `ArtifactsView.onChange()`가 `GatewayApp`의 fallback list를
재조회한다. Browser/cleanup state refresh는 기존 `ArtifactsView`가 계속 소유한다.

## loading과 error behavior

- Library base loading과 error는 기존 Archive alert를 사용한다.
- Requests Team Run 실패는 Write, Later, Dismiss 같은 direct action을 막지 않으며
  Retry는 `teamRuns()`만 다시 호출한다.
- Outputs의 `artifactBrowser()` 실패는 현재처럼 조용히 fallback list를 사용한다.
- `GatewayApp`의 `/api/artifacts` fallback 조회 실패는 기존 screen error와 retry를
  사용한다.
- Library와 Outputs는 screen 전환 시 `GatewayApp`이 screen error를 초기화하므로
  서로의 error presentation을 공유하지 않는다.
- 새로운 loading component, global cache 또는 error abstraction은 추가하지 않는다.

## style 제거와 유지

Archive의 embedded Artifacts가 사라져 사용처가 없어지는 selector만 제거한다.

- `.archive-artifacts`
- `.archive-artifacts > .artifacts-view`
- `.archive-artifacts-boundary`
- `.archive-artifacts-boundary strong`
- `.archive-artifacts-boundary p`
- `.archive-guide-card-artifacts`

Knowledge lifecycle guide가 full width가 되도록 `.archive-guide`의 두-column layout을
한 column으로 조정한다.

일반 `.artifacts-*`, `.artifact-*`, `.archive-library*`, `.archive-request*`,
`.archive-editor*` selector는 유지한다.

## 제거 범위

### production

- Sidebar의 `archive` item
- `GatewayApp`의 `screen === "archive"` load/render branch
- `ArchiveView`의 `ArtifactsView` import
- `ArchiveView`의 Artifact props
- Work Outputs guide card
- Artifacts tab과 panel
- embedded boundary note
- orphan Archive-only Artifact wrapper styles

### tests

기존 `ArchiveView` Artifact 관련 test 여섯 개는 다음처럼 처리한다.

1. wrapper padding test는 제거한다.
2. 일반 Artifact metadata/group layout test 두 개는 `ArtifactsView.test.jsx`로 이동한다.
3. Knowledge/Work Outputs guide test는 Knowledge lifecycle assertion만 유지한다.
4. embedded Artifact/Library boundary test는 Library와 Outputs navigation 분리 test로
   대체한다.
5. embedded delete refresh test는 Outputs screen mutation refresh test로 이동한다.

## 범위 밖

- Archive/Artifact backend API, service, schema, migration 변경
- `ArchiveView` → `LibraryView` rename
- `ArtifactsView` → `OutputsView` rename
- Library editor와 Requests panel의 component/hook 분리
- Artifact browser의 singleton API injection 변경
- `/api/artifacts` fallback과 `/api/artifacts/browser`의 통합
- URL routing, deep link, browser history
- Automations, Configuration, Policies, Dashboard/Operations 개편
- usage telemetry와 Hooks optional-module 판단

## 테스트 전략

### RED

1. Sidebar에는 `Library`와 `Outputs`가 있고 `Archive` button은 없다.
2. Library 진입은 Library heading과 Published/Drafts/Requests tabs를 보여준다.
3. Library 진입은 Archive base API만 호출하고 `/api/artifacts`를 호출하지 않는다.
4. Library 안에는 Artifacts tab, Work Outputs guide, embedded Artifact가 없다.
5. Outputs 진입은 Outputs heading과 existing Artifact browser를 보여주며 Archive API를
   호출하지 않는다.
6. Outputs mutation 뒤 `GatewayApp`이 `/api/artifacts` fallback list를 갱신한다.
7. Requests Team Run lazy load, retry와 stale response 방어가 유지된다.

### 회귀

- `ArchiveView.test.jsx`의 publish, revision, delete, request status/delegation test
- `ArtifactsView.test.jsx`의 Saved/Recent/Cleanup, search, preview, delete test
- `GatewayApp.test.jsx`의 screen navigation과 fetch boundary test
- `client.test.js`의 Archive/Artifact API contract test

### 최종 검증

1. focused ArchiveView, ArtifactsView, GatewayApp와 API client tests
2. 전체 frontend test suite
3. frontend production build
4. tracked `frontend_dist` 갱신
5. `git diff --check`
6. production/test 정적 검색:
   - removed `screen === "archive"`와 Sidebar `archive` item 0건
   - `ArchiveView`의 `ArtifactsView` import/props/panel 0건
   - orphan Archive-only Artifact selector 0건
   - Archive Map production contract 0건 유지
7. backend source diff 0건 확인

backend code와 contract를 변경하지 않으므로 기존 baseline 실패가 있는 전체 backend
suite를 이 단계의 completion gate로 다시 실행하지 않는다.

## 성공 기준

- 사용자가 Library와 Outputs를 별도 primary navigation에서 찾을 수 있다.
- Library의 화면 구조가 Published → Drafts → Requests lifecycle을 직접 표현한다.
- Library 진입 비용에서 Artifact fallback 조회가 제거된다.
- Outputs의 검색, retention, cleanup, preview, delete 기능이 보존된다.
- Archive와 Artifact backend domain은 변경되지 않는다.
- focused/full frontend tests와 production build가 통과한다.
- generated frontend asset과 source가 같은 navigation을 제공한다.

## rollout과 rollback

feature flag 없이 frontend navigation 변경으로 배포한다. persisted screen key나 URL route가
없어 데이터 migration은 필요 없다. Rollback은 frontend source와 generated asset commit을
되돌리면 되며 Archive/Artifact 저장 데이터에는 영향이 없다.

## 후속 단계

1. Library 화면이 안정된 뒤 `ArchiveView`의 Library editor와 Requests panel을
   same-owner component/controller로 분리할지 별도 설계한다.
2. Jobs, Schedules, Hooks를 Automations shell로 통합한다.
3. Teams, Personas, Rules, Spaces, Settings를 Configuration/Policies 구조로 정리한다.
4. Home summary와 Operations recovery 책임을 분리한다.
5. content-free local usage count를 추가한 뒤 Hooks optional connector 여부를 판단한다.
