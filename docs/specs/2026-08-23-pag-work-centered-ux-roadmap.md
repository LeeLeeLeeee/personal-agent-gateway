---
title: PAG 작업 중심 UX 개편 기획
type: product-spec
domain: personal-agent-gateway
feature: work-centered-ux-roadmap
status: implemented
updated_at: 2026-08-23
---

# PAG 작업 중심 UX 개편 기획

## 1. 결정 요약

PAG의 첫 화면과 navigation을 기능 목록이 아니라 사용자의 작업 흐름에 맞게 개편한다.

1. P0에서는 사용하지 않는 `Jobs`, `Schedules`, `Hooks`를 primary navigation에서 제거하고, 실패한 작업을 발견하고 복구하는 흐름을 우선 개선한다.
2. P1에서는 `Chat`, `Team Run`, `Outputs` 사이의 시작·결과 이동을 연결하고 설정성 기능을 `Configuration`으로 묶는다.

`Jobs`, `Schedules`, `Hooks`의 backend lifecycle과 저장 데이터는 삭제하지 않는다. 이번 개편은 기능 삭제가 아니라 사용자 정보 구조와 작업 복구 경험을 개선하는 작업이다.

## 2. 배경과 검증 근거

### 현재 관찰

- Sidebar에 14개의 top-level 항목이 노출된다.
- 현재 local data에는 Job, Schedule, Hook, Hook Run이 모두 0건이다.
- Jobs, Schedules, Hooks 화면은 빈 상태에서도 필터와 생성 UI가 큰 비중을 차지한다.
- Dashboard 상단의 Codex와 Claude 계정 한도는 모두 `미수집` 상태지만, 실제 조치가 필요한 실패 Team Run은 하단에 배치된다.
- 실패 Team Run의 Overview에는 실패 원인이 없다. `claude: capabilities_unavailable` 원인은 Tasks 탭까지 이동해야 확인할 수 있다.
- Activity에는 retry task가 생성됐으며 resume이 필요하다고 표시되지만, 사용자가 즉시 실행할 명확한 복구 action이 없다.
- Chat은 이미 자연어 목표 입력을 담당한다. 별도의 통합 작업 entity를 추가하면 기존 기능과 중복된다.
- Outputs에는 검색, 유형 필터, 보관 상태 필터가 이미 있다. 동일 기능을 다시 만들 이유가 없다.
- Team Run에는 Reports와 Files 탭이 있으며 Outputs에도 Team Run 결과가 묶인다. 새 결과 화면보다 기존 화면 간 연결이 필요하다.

### 문제 정의

현재 PAG의 핵심 문제는 기능 부족이 아니라 다음 세 가지다.

1. 자주 사용하지 않는 관리 기능과 핵심 작업 기능의 navigation 우선순위가 같다.
2. 실패 상태는 보이지만 원인 파악과 복구 action이 서로 다른 화면에 흩어져 있다.
3. 작업 시작, 실행 확인, 결과 확인 사이의 이동이 entity 중심으로 분리돼 있다.

## 3. 목표

- 사용자는 Home 첫 화면에서 조치가 필요한 작업을 바로 발견할 수 있다.
- 실패한 Team Run은 다른 탭을 탐색하지 않고 원인과 다음 action을 확인할 수 있다.
- primary navigation은 사용자 목적 중심의 8개 이하 항목으로 줄인다.
- Chat과 Team Run은 서로 다른 실행 방식으로 유지하되 하나의 작업 시작 영역에서 선택할 수 있다.
- 작업 결과에서 원본 실행과 후속 action으로 이동할 수 있다.
- 저빈도 자동화 기능은 찾을 수 있지만 항상 navigation 공간을 차지하지 않는다.

## 4. 비목표

- Job, Schedule, Hook backend service 또는 database table 삭제
- Chat과 Team Run을 하나의 실행 모델로 통합
- Outputs 검색·필터 재구현
- 새로운 global router 도입
- Team Run 실행 엔진과 lifecycle 전면 수정
- 자동 복구 또는 사용자 확인 없는 provider/model 변경
- 결과에서 Schedule을 생성하는 자동화 기능
- Work preset과 global search

## 5. 제품 원칙

### 작업 우선

사용자가 지금 해야 할 일, 진행 중인 일, 최근 끝난 일을 시스템 진단보다 먼저 보여준다.

### 상태에는 action을 함께 제공

`FAILED`, `NEEDS ATTENTION`만 표시하지 않고 원인, 영향, 가능한 다음 action을 같은 영역에 제공한다.

### 기존 domain을 재사용

새로운 통합 Task나 Result entity를 만들지 않는다. Chat, Team Run, Artifact, Schedule의 기존 계약을 유지하고 navigation과 cross-link만 연결한다.

### 저빈도 기능은 숨기되 막지 않음

자동화 기능은 primary navigation에서 제외하지만 `Configuration > Automations`에서 항상 접근할 수 있게 한다.

## 6. 목표 정보 구조

```text
WORK
├── Home
├── Chat
└── Team Runs

KNOWLEDGE
├── Library
└── Outputs

SYSTEM
├── Configuration
├── Operations
└── Settings
```

`Configuration` 내부 구조는 다음과 같다.

```text
Configuration
├── Teams
├── Personas
├── Policies
│   ├── Instructions (Rules)
│   └── Workspace access (Spaces)
└── Automations
    ├── Definitions
    │   ├── Schedules
    │   └── Email triggers (Hooks)
    └── Run history
        ├── Jobs
        └── Hook runs
```

사용자 화면에서는 mail polling에 특화된 `Hooks`를 `Email triggers`로 표현한다. 내부 API, service, schema 이름은 변경하지 않는다.

## 7. P0 — 핵심 사용성과 복구

### 7.1 Primary navigation 정리

#### 요구사항

- Sidebar에서 `Jobs`, `Schedules`, `Hooks`를 제거한다.
- `Configuration > Automations`에서 세 기능과 실행 기록에 접근할 수 있게 한다.
- 데이터 유무에 따라 메뉴가 나타났다 사라지는 동적 navigation은 사용하지 않는다.
- 기존 Job, Schedule, Hook API와 background runner는 유지한다.
- mobile 또는 좁은 화면에서도 WORK, KNOWLEDGE, SYSTEM 그룹의 순서가 유지돼야 한다.

#### 완료 조건

- 세 기능이 primary navigation에 노출되지 않는다.
- 사용자는 Home 또는 Sidebar에서 두 번 이내의 선택으로 Automations에 진입할 수 있다.
- 기존 Schedule과 Hook 생성·수정·실행 기능이 회귀하지 않는다.
- Job과 Hook Run history를 Automations 안에서 조회할 수 있다.

### 7.2 Dashboard를 Home으로 개편

#### 첫 화면 순서

1. **Needs attention**: 실패, 승인 대기, 수동 재개가 필요한 작업
2. **Running**: 현재 실행 중인 Chat 또는 Team Run
3. **Recent results**: 최근 완료된 결과와 파일
4. **System summary**: 계정 한도, session, gateway 상태의 축약 정보

P1 배포 후에는 `Recent results`와 `System summary` 사이에 `Start work`를 추가한다.

#### 표시 규칙

- `Needs attention`이 1건 이상이면 첫 viewport에 반드시 표시한다.
- 항목마다 상태 설명과 primary action 하나를 제공한다.
- 계정 한도 수집 실패는 Home 전체 error로 취급하지 않는다.
- 상세 계정 상태와 system health는 Operations 또는 Settings로 연결한다.
- Home은 summary만 소유하고 상세 진단과 emergency action은 Operations에 남긴다.

#### 빈 상태

- 조치할 작업이 없으면 `확인할 문제가 없습니다`를 표시한다.
- 실행 중인 작업이 없으면 빈 card 대신 최근 결과를 위로 올린다.
- 최근 결과도 없으면 기존 Chat과 Team Runs navigation을 안내한다. P1 이후에는 Start work 안내로 대체한다.

#### 완료 조건

- 실패 Team Run이 있을 때 Home 진입 직후 추가 navigation 없이 확인할 수 있다.
- 계정 한도가 미수집이어도 작업 시작과 복구 action은 정상 동작한다.
- Dashboard와 Operations가 동일 상세 panel을 중복 렌더하지 않는다.

### 7.3 Team Run 실패 복구 panel

#### 노출 위치

Team Run detail header 아래, Overview·Tasks·Activity tab 위에 복구 panel을 배치한다.

#### 표시 정보

- 사용자용 실패 요약
- 원본 error code 또는 provider message
- 실패한 Cycle, Task, Agent
- 이미 완료된 Task와 생성된 파일의 보존 여부
- 복구에 필요한 조건

#### 복구 action 규칙

| 상태 | Primary action | Secondary action |
| --- | --- | --- |
| retry task가 있고 resume 필요 | `Resume cycle` | `Review retry task` |
| capability 또는 provider 사용 불가 | `Change runtime` | `Open diagnostics` |
| 재실행 가능한 단일 Task 실패 | `Retry failed task` | `Open task` |
| 원인을 분류할 수 없음 | `Open diagnostics` | `View activity` |

- 실행할 수 없는 action은 disabled button으로 두지 않고, 필요한 조건과 이동 action을 표시한다.
- 복구 action은 기존 성공 결과와 파일을 삭제하지 않는다.
- provider 또는 model 변경은 사용자가 확인한 뒤 적용한다.
- 이미 retry가 생성된 경우 중복 retry를 생성하지 않는다.

#### 완료 조건

- 실패 원인을 Tasks 탭 진입 없이 확인할 수 있다.
- resume이 필요한 상태에서 활성화된 next action이 하나 이상 제공된다.
- 복구 action 실패 시 원래 실패 상태와 생성된 파일이 보존된다.

## 8. P1 — 작업 흐름 연결

### 8.1 Start work

Home에 새로운 작업 entity를 만들지 않고 다음 두 action을 제공한다.

- `Chat으로 시작`: 빠른 단일 Agent 작업과 대화형 수정
- `Team으로 시작`: 역할 분담, 여러 Task, Cycle 기반 작업

각 action에는 한 줄 설명과 최근 사용한 Persona 또는 Team을 보조 정보로 표시할 수 있다. 목표 입력은 각 기존 화면에서 계속 담당한다.

#### 완료 조건

- 사용자는 Home에서 한 번의 선택으로 Chat 또는 Team Run 생성 흐름에 진입한다.
- Start work가 Chat composer나 Team Run form의 상태를 복제하지 않는다.

### 8.2 결과 provenance와 cross-link

#### Team Run → Outputs

- Overview에 이번 Cycle의 report와 file 개수를 표시한다.
- `View all outputs`로 해당 Team Run에 필터된 Outputs를 연다.
- Files 탭의 preview와 기존 파일 계약은 유지한다.

#### Outputs → 원본 실행

- 각 Artifact에 Chat session, Team Run, Cycle, Task 중 존재하는 출처를 표시한다.
- `Open source` action으로 원본 화면과 entity를 연다.
- 삭제됐거나 접근할 수 없는 원본은 비활성 링크 대신 `원본을 사용할 수 없음` 상태로 표현한다.

#### 완료 조건

- Team Run에서 관련 Outputs까지 두 번 이내의 선택으로 이동한다.
- Output에서 원본 실행까지 한 번의 선택으로 이동한다.
- 기존 Outputs 검색과 filter가 유지된다.

### 8.3 Configuration 그룹화

- Teams와 Personas를 Configuration의 독립 section으로 이동한다.
- Rules는 `Instructions`, Spaces는 `Workspace access`로 사용자용 이름을 변경하고 Policies 아래에 둔다.
- 보안 효과가 다른 Rules와 Spaces를 하나의 설정 값처럼 합치지 않는다.
- Settings는 인증, access mode, runtime 환경 같은 system 설정만 담당한다.
- Operations는 health, emergency stop, backup, 복구 진단만 담당한다.

#### 완료 조건

- Sidebar top-level 항목이 8개 이하가 된다.
- Team, Persona, Rule, Space의 기존 생성·수정·삭제 동작이 유지된다.
- 설정 항목을 찾기 위해 Configuration과 Settings를 왕복하지 않도록 각 화면의 책임 설명을 제공한다.

## 9. 상태와 오류 처리

- 각 summary section은 독립적으로 loading과 error를 표시한다. 한 API 실패로 Home 전체를 막지 않는다.
- Home에서 action을 실행한 뒤 대상 화면 전환이 실패하면 현재 Home 상태를 유지한다.
- Configuration의 tab fetch는 tab 진입 시 수행하고 모든 설정 데이터를 한 번에 가져오지 않는다.
- 삭제된 provenance 대상은 artifact 자체의 조회와 preview를 막지 않는다.

## 10. 데이터와 backend 영향

- 기존 Job, Schedule, Hook, Team Run, Artifact schema를 유지한다.
- Home summary는 기존 API read model을 조합하되 중복 조회를 줄인다.
- 새로운 실행 lifecycle 또는 통합 Task table을 만들지 않는다.
- navigation 변경 때문에 backend route를 rename하지 않는다.

## 11. 사용 지표

content를 수집하지 않는 local event만 사용한다.

| 지표 | 목적 |
| --- | --- |
| screen visit count | 메뉴 정리 효과와 저빈도 기능 확인 |
| Home → work start 선택 수 | Home 진입점의 유효성 확인 |
| failure panel → recovery action 수 | 복구 panel 사용성 확인 |
| failure → resumed/completed 시간 | 복구 시간 개선 확인 |
| source ↔ output 이동 수 | cross-link 가치 확인 |

성공 기준은 다음과 같다.

- 실패 원인을 찾기 위한 평균 tab 이동 수: 0회
- resume 가능한 실패 상태의 primary action 제공률: 100%
- Home에서 Chat 또는 Team 시작까지 선택 수: 1회
- primary navigation 항목: 8개 이하
- Automations 접근: 2회 이내 선택
- Output에서 원본 실행 접근: 1회 선택

## 12. 배포 순서

### Release 1 — P0

1. Configuration의 Automations 진입점 추가
2. Sidebar에서 Jobs, Schedules, Hooks 제거
3. Dashboard를 Home section 구조로 변경
4. Team Run failure panel과 recovery action 추가
5. local usage event 기준선 수집 시작

### Release 2 — P1

1. Home Start work 추가
2. Team Run과 Outputs provenance cross-link 추가
3. Teams, Personas, Rules, Spaces를 Configuration으로 이동
4. Sidebar를 목표 정보 구조로 축소

## 13. 회귀와 rollback

- navigation 개편 전후 screen key mapping을 명시적으로 유지해 기존 component를 재사용한다.
- P0 배포 시 backend worker와 database migration을 포함하지 않는다.
- Home summary 실패가 Chat, Team Run, Operations 진입을 막지 않아야 한다.
- 새 Configuration shell에 문제가 있으면 기존 screen component를 독립 렌더링하는 임시 진입점을 복구할 수 있게 한다.
- 실패 복구 action은 기존 controller method를 호출하고 별도 실행 경로를 만들지 않는다.

## 14. 테스트 범위

### Component

- Sidebar에 제거 대상 메뉴가 없고 목표 그룹이 노출되는지 검증
- Home section 우선순위, empty/error/partial failure 상태 검증
- 실패 유형별 recovery action과 disabled 대체 안내 검증
- Configuration tab별 기존 화면 기능 회귀 검증
- provenance 유무와 삭제된 원본 상태 검증

### Integration

- Schedule 생성 → Job 생성 → history 표시
- 실패 Team Run → retry 또는 resume → 상태 갱신
- Team Run → filtered Outputs → 원본 Team Run 왕복
- Home 일부 API 실패 시 나머지 section과 navigation 유지

### Manual

- desktop과 narrow width navigation
- Job, Schedule, Hook이 0건인 상태
- 계정 한도가 미수집인 상태
- Team Run이 실패, resume 대기, 완료된 각 상태
- 원본 실행이 삭제된 Artifact

## 15. 제외 또는 후속 판단

- Hooks의 backend 완전 삭제는 2~4주 usage가 계속 0이고 외부 consumer가 없다는 검증 후 별도 결정한다.
- URL deep link 도입은 navigation 개편과 분리한다.
- 결과 기반 automation, Work preset, Global search는 이번 기획에서 제외한다. P0·P1 적용 후 실제 사용 불편이 확인될 때 별도 기획한다.
- Dashboard와 Operations의 shared read model 최적화는 실제 중복 요청을 측정한 뒤 범위를 결정한다.
