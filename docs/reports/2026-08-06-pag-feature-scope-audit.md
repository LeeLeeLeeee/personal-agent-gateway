---
title: Personal Agent Gateway 기능 범위 및 화면 통합 진단
type: report
domain: personal-agent-gateway
feature: product-scope-audit
status: active
aliases:
  - PAG 불필요 기능 진단
  - PAG 화면 통합 제안
  - PAG 기능 다이어트
tags:
  - product-scope
  - information-architecture
  - maintainability
  - archive
updated_at: 2026-08-06
---

# Personal Agent Gateway 기능 범위 및 화면 통합 진단

## Summary

PAG에는 당장 backend 기능을 대거 삭제해야 하는 문제보다 **하나의 사용자 여정을 너무 많은 독립 화면으로 노출한 문제**가 더 크다. 사이드바는 13개 화면이며, 실행 정의·실행 이력·정책·운영 상태가 각각 별도 메뉴로 갈라져 있다.

현재 근거로 즉시 제거에 가장 가까운 기능은 Archive Map이다. 그 외에는 backend capability를 없애기보다 다음처럼 화면을 합치는 편이 안전하다.

1. `Jobs + Schedules + Hooks` → **Automations**
2. `Rules + Spaces` → **Policies** 또는 Team/Persona 설정 내부
3. `Library + Drafts + Requests` → 하나의 **Knowledge workflow**
4. `Artifacts` → Knowledge와 구분되는 **Outputs**
5. `Dashboard`는 요약, `Operations`는 복구·비상제어로 책임을 명확히 구분

이 진단은 repository 구조와 UI/API 계약을 기준으로 했다. 실제 사용자 데이터나 실행 telemetry는 읽지 않았으므로 “사용하지 않는다”는 결론이 아니라 “고유 가치 대비 노출·유지 비용이 크다”는 우선순위다.

## 현재 정보 구조

`Sidebar`에는 일반 메뉴 8개와 Team 메뉴 5개가 노출된다.

```text
Dashboard
Chat
Jobs
Schedules
Hooks
Archive
Operations
Settings

TEAMS
Team Runs
Teams
Personas
Spaces
Rules
```

기존 제품 진단 문서도 기능 추가보다 “설정·실행·결과가 다른 화면과 저장 개념으로 분리된 문제”를 우선 해결해야 한다고 보았다. 현재 구현은 그 문제를 여전히 가진 채 Archive 내부 탭까지 다섯 개로 늘어났다.

## 기능별 판단

| 기능 | 고유 역할 | 중복·비용 | 판단 |
| --- | --- | --- | --- |
| Chat | 개인 Agent 실행과 session 연속성 | 핵심 진입점 | **유지** |
| Team Runs | 장기 협업, Cycle·Task·복구 | PAG 차별점 | **유지** |
| Teams / Personas | 재사용 가능한 실행 구성 | 실행 화면과 분리될 이유는 있으나 nav 두 칸을 점유 | **유지, Configuration으로 그룹화** |
| Jobs | 공통 capability 실행 상태·승인·retry·log | Schedule 실행 이력과 Operations 항목에 재노출 | **backend 유지, Automations history로 통합** |
| Schedules | 반복 실행 정의, Job 생성 | Jobs와 한 lifecycle | **Automations definitions로 통합** |
| Hooks | 외부 mail source polling과 Persona/Team 전달 | core Hook·mail knowledge 7개 backend 파일·약 2,043줄에 email adapter 1개·287줄이 더 있는 전문 subsystem. 미사용 시 runtime·secret·복구 비용이 큼 | **사용하면 유지, 아니면 optional module/숨김** |
| Library / Drafts / Requests | 검토된 재사용 지식과 human review workflow | 주요 경로는 Request→Draft→Library지만 direct entry와 hook/team-origin draft 분기도 있음 | **유지, 관련 workflow를 한곳에서 정리** |
| Archive Map | 여러 entity를 한 화면에 모은 파생 시각화 | 고유 도메인 상태·action 없음, custom graph 비용 큼 | **제거 우선** |
| Artifacts | 작업 결과 파일의 보관·검색·미리보기·삭제 | Library 지식과 의미가 다르지만 Archive 안에 있어 명명 혼란 | **유지, Outputs로 명확히 분리** |
| Rules | prompt instruction priority와 snapshot | Team/Persona 설정과 강하게 결합 | **backend 유지, Policies/Configuration으로 통합** |
| Spaces | read/write/worktree 정책과 snapshot | Team/Persona 설정과 강하게 결합 | **backend 유지, Rules와 Policies로 통합** |
| Dashboard | 계정 한도, 로컬 session, 운영 요약 | `api.operations()`를 직접 다시 조회 | **Home 요약으로 유지** |
| Operations | health, 실행 복구, emergency stop, backup | Dashboard와 상태 표시는 겹치지만 안전 action은 고유 | **유지, recovery/admin으로 집중** |
| Settings | access mode, auth session, runtime 진단 | Operations와 일부 시스템 정보 중복 | **유지, System 영역에서 Operations와 이웃 배치** |
| CapabilityRegistry | Job input 검증과 runner 선택의 내부 계약 | Jobs가 직접 의존하는 core service | **유지** |
| `GET /api/capabilities` | CapabilityRegistry 조회용 HTTP surface | 독립 화면은 이미 제거됐고 repository frontend consumer가 없음 | **메뉴화 금지, 외부 consumer 확인 후 deprecation 가능** |
| Audit API | 보안·변경 기록 | 일반 사용자 화면은 없으나 운영·검증 기반 | **유지, 필요 시 Operations에서만 노출** |

## 가장 먼저 줄일 것

### P0 — Archive Map 제거

Map은 `ArchiveService.graph()`가 Archive 데이터를 node/edge로 만들고, frontend가 다시 네 개 고정 열로 배치한다. Map에서 가능한 `Review draft`, `Open in Library`, `Write in Library`는 기존 탭에 모두 존재한다. Mutation workflow를 잃지 않는 대신 cross-domain provenance 개요를 포기하고 UI, API read model, CSS, 테스트 부담을 줄이는 선택이다.

대체 개요가 필요하면 graph가 아니라 Request 상태 보드로 제한한다. 먼저 Map을 제거한 뒤 실제 사용에서 전체 진행 보기가 반복적으로 필요할 때만 구현한다.

### P1 — Automations로 통합

Schedule은 실행할 때 Job을 만든다. Hook도 외부 event로 실행을 시작하고 자체 run 이력을 가진다. 세 메뉴를 다음처럼 묶으면 실행 정의와 결과 추적이 한곳에 모인다.

```text
Automations
├── Definitions
│   ├── Schedules
│   └── Hooks
└── Runs
    ├── Jobs
    └── Hook Runs
```

Backend lifecycle은 합치지 않는다. `JobWorker`, `SchedulerLoop`, `HookRunner`의 실행 소유권은 현재처럼 분리하고 UI navigation과 cross-link만 합친다.

### P1 — Policies로 통합

Rules와 Spaces는 모두 실행 구성을 제어하지만 적용 계약은 다르다. Rules는 주로 Team Run/Cycle의 instruction snapshot에 포함되고, Space policy는 개인 runtime에서 resolve되며 Team Run/Cycle에서는 별도 snapshot으로 동결된다. 별도 backend service는 유지하되 UI를 다음처럼 합친다.

```text
Configuration
├── Personas
├── Teams
└── Policies
    ├── Instructions (Rules)
    └── Workspace access (Spaces)
```

`Rules`의 REQUIRED는 보안 강제가 아니라 prompt 우선순위이고, `Spaces`는 실제 파일 접근 정책이다. 같은 화면에 두더라도 설명과 권한 효과는 섞지 않는다.

### P1 — Archive의 개념 분리

현재 Archive는 검토된 지식과 원본 작업 파일을 한 메뉴에 넣고 “서로 다르다”는 안내문으로 경계를 설명한다. 이는 기능이 같은 것이 아니라 navigation이 같은 것이다.

권장 구조는 다음 둘 중 하나다.

- `Library`: Published / Drafts / Requests
- `Outputs`: Artifacts

또는 상위 `Library` 화면 안에 `Knowledge`와 `Outputs` 두 섹션을 두되 Map은 제거한다. 핵심은 Draft가 “발행 전 지식”, Artifact가 “작업 중 생긴 파일”이라는 점을 UI 구조 자체로 보여주는 것이다.

## 유지해야 할 기능

다음은 화면 중복이 있더라도 backend에서 제거하면 안 된다.

- Job engine과 `CapabilityRegistry`: Schedule과 API 실행의 공통 상태·승인·retry 계약이다. 조회용 `/api/capabilities` route는 별도 판단 대상이다.
- Operations의 emergency stop, intake gate, backup verification: 편의 기능이 아니라 원격 로컬 실행의 안전장치다.
- Team Run/Cycle Rules snapshot과 Space policy resolution/snapshot: 각각 실행 재현성과 workspace 안전 경계다.
- Artifacts: Library와 다르지만 실행 결과 검토와 파일 수명주기에 필요하다.
- Audit: 일반 메뉴가 없어도 보안·변경 추적의 근거다.

## 조건부 축소 후보: Hooks

Hooks는 제품 문서상 지원 기능이지만 현재 구현은 mail polling에 특화되어 있다. Hook service·loop·runner·runs·secrets·API와 mail knowledge를 합친 core application 범위가 7개 파일·약 2,043줄이고, `sources/email.py` adapter 287줄이 별도로 있다. secret store, poll loop, run recovery, mail knowledge projection을 함께 운영한다.

실제로 이메일 자동화를 쓰지 않는다면 삭제보다 다음 순서가 안전하다.

1. 설정된 Hook이 없으면 navigation과 background polling을 비활성화한다.
2. `Automations` 안의 optional connector로 이동한다.
3. local-only usage count로 2~4주 실제 사용을 확인한다.
4. 계속 0이면 mail connector를 별도 optional package 또는 후순위 기능으로 분리한다.

## 제안 정보 구조

13개 top-level 화면을 7개 사용자 목적 중심 진입점으로 줄일 수 있다.

```text
WORK
- Home
- Chat
- Team Runs
- Automations

KNOWLEDGE
- Library
  - Knowledge: Published / Drafts / Requests
  - Outputs: Artifacts

SYSTEM
- Configuration
- Operations
```

`Settings`는 Configuration 또는 Operations의 System 탭으로 배치할 수 있다. 실제 변경 전에 mobile width, 빈 상태, 화면 전환 빈도와 deep-link 요구를 확인한다.

## 구조상 함께 고칠 부분

### GatewayApp 집중

`GatewayApp`은 1,047줄이며 screen별 fetch, global state, mutation handler, cross-navigation을 함께 소유한다. 메뉴 통합 시 단순히 조건문을 더 붙이면 집중도가 악화된다. `Automations`, `Configuration`, `Archive/Library`별 controller boundary를 먼저 정하고 각 상위 화면이 자기 query를 소유하게 한다.

### eager fetch와 중복 조회

- Archive는 mount 시 탭과 무관하게 여섯 API를 호출한다.
- Dashboard는 Operations와 별도로 `api.operations()`를 직접 호출한다.
- `GatewayApp`은 screen 전환마다 screen별 collection을 다시 적재하고, 일부 mutation 후 전체 collection을 재조회한다.

메뉴 통합은 모든 데이터를 한 번에 fetch하는 이유가 아니다. 상위 화면은 summary만 가져오고 탭/detail 진입 시 필요한 query를 lazy load/cache하는 편이 맞다.

## 실행 순서 제안

1. **Map 제거**: UI 진입점과 frontend fetch를 먼저 제거하고 workflow 회귀를 검증한다.
2. **Archive 명명 정리**: Knowledge와 Outputs 경계를 결정한다.
3. **Automations shell**: Jobs/Schedules/Hooks 화면을 재사용해 한 상위 화면의 탭으로 옮긴다.
4. **Policies shell**: Rules/Spaces를 Configuration 아래로 옮긴다.
5. **Dashboard/Operations 계약 정리**: Home은 summary와 deep link, Operations는 action과 상세 진단만 소유한다.
6. **local usage 계측**: 민감 내용 없이 screen visit, created/run/success count만 기록해 Hooks 등 조건부 기능을 재평가한다.

## Verification

- ArchiveView component test: `npm --prefix frontend test -- ArchiveView`
  - 1 test file, 21 tests passed.
- 정적 사용처 검색:
  - `ArchiveView` production caller는 `GatewayApp` 하나다.
  - `/api/archive/map` frontend consumer는 `ArchiveView` 하나다.
  - Dashboard와 Operations가 모두 operations read model을 사용한다.
- 화면·backend·API·test 경계를 코드와 기존 제품/아키텍처 문서에 대조했다.

## Follow-ups

- 실제 기능 삭제 전 local usage data 또는 사용자 사용 패턴으로 Hooks, Rules, Spaces의 진입 빈도를 확인한다.
- Map 제거와 navigation 통합은 별도 구현 계획과 RED/GREEN 회귀 범위를 작성한다.
- URL route가 없는 현재 screen state 구조는 통합 작업과 함께 deep link 도입 여부를 결정한다.

## 근거

- `README.md:1-53`
- `frontend/src/components/organisms/Sidebar/index.jsx:3-19`
- `frontend/src/components/containers/GatewayApp/index.jsx:46-75,273-315,788-1040`
- `frontend/src/components/organisms/ArchiveView/index.jsx:100-671,673-726,945-1033,1327-1546`
- `frontend/src/components/organisms/DashboardView/index.jsx:192-260,282-504`
- `frontend/src/components/organisms/OperationsView/index.jsx:117-273`
- `src/personal_agent_gateway/archive.py:857-1095`
- `docs/knowledge/gateway-feature-guide.md`
- `docs/knowledge/gateway-architecture-guide.md`
- `docs/reports/2026-07-15-product-pm-usability-opportunities.md`
- `docs/reports/2026-07-15-development-pm-maintainability-assessment.md`
