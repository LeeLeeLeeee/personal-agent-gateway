---
title: 대시보드 운영 현황 확장 구현 계획
type: todo
domain: personal-agent-gateway
feature: dashboard-operations
status: done
aliases:
  - 대시보드 확장 계획
  - Dashboard operations plan
tags:
  - dashboard
  - operations
  - frontend
  - api
updated_at: 2026-07-22
---

# 대시보드 운영 현황 확장 구현 계획

작성일: 2026-07-22

상태: 구현·검증 완료

결과 보고서: [대시보드 운영 현황 확장 구현 결과](../reports/2026-07-22-dashboard-operations-expansion-implementation.md)

## 결론

1차 확장은 신규 backend endpoint나 production schema 변경 없이 구현한다. 기존 `/api/dashboard/usage`는 사용량 위젯에 유지하고, 기존 `/api/operations` 응답의 `items`, `health`, `intake_open`, `diagnostics`를 frontend에서 읽기 전용으로 투영해 `진행 중 작업`, `시스템 상태`, `조치 필요`를 만든다.

`/api/operations`의 `counts`에는 Team Run의 `planning`, `summarizing`이 없으므로 진행 중 합계를 계산하는 근거로 사용하지 않는다. `items`의 명시적 status allowlist로 계산한다. background health는 `last_error`가 있어도 `ready=true`와 `detail="degraded: ..."`일 수 있으므로, 1차 조치 필요 합계에는 `ready=false`만 넣고 degraded 문구는 시스템 상태 상세에 그대로 표시한다.

## 목표와 비목표

### 목표

- 기존 사용량 카드 아래에 운영 현황 요약을 추가한다.
- 사용량과 운영 현황을 독립적으로 조회해 한쪽 장애가 다른 위젯을 가리지 않게 한다.
- 작업을 `열기`로 기존 Chat/Team Run/Job/Schedule 화면에 연결한다.
- 쉬운 한국어 라벨과 명확한 loading/error/empty/stale 상태를 제공한다.

### 비목표

- Dashboard에서 재시도, 재개, 긴급 중지, backup 같은 mutation을 실행하지 않는다.
- `OperationsView` 전체를 Dashboard 안에 중첩하지 않는다.
- 1차에서 `/api/dashboard/operations` 같은 중복 집계 endpoint를 만들지 않는다.
- degraded 상태를 backend가 별도 enum으로 계약화하지 않는다.

## UX 설계

기존 `DashboardView`의 사용량 섹션은 그대로 둔다. 그 아래에 `운영 현황`을 두고 다음 순서로 표시한다.

| 영역 | 표시 내용 | 빈 상태 | 사용자 동작 |
| --- | --- | --- | --- |
| 요약 | `진행 중`, `조치 필요`, `정상 시스템` 숫자 | 숫자 `0` | 없음 |
| 진행 중 작업 | 최근 갱신 순 최대 5건, 유형·제목·한국어 상태·갱신 시각 | `현재 진행 중인 작업이 없습니다.` | `열기` |
| 시스템 상태 | component별 `정상/확인 필요`와 detail, intake·workspace 쓰기 가능 여부 | `시스템 상태 정보가 없습니다.` | 없음 |
| 조치 필요 | 문제 유형·제목·이유, 최근 갱신 순 최대 5건 | `조치가 필요한 항목이 없습니다.` | target이 있으면 `열기` |

Dashboard의 `열기`는 상태 변경이 아니라 navigation만 수행한다. 상세 조치 버튼은 기존 `OperationsView`가 계속 소유한다.

## 데이터 계약과 판정 규칙

### source별 책임

| Source | 기존 client 함수 | Dashboard 사용 field | 판정 |
| --- | --- | --- | --- |
| `/api/dashboard/usage` | `api.dashboardUsage()` | `providers[].weekly_limit/used/remaining/reset_at/usage_status` | 변경 없음 |
| `/api/operations` | `api.operations()` | `items`, `health`, `intake_open`, `access_mode`, `diagnostics.workspace_writable` | 1차에 충분 |

### frontend projection

- 진행 중 status: `planning`, `running`, `summarizing`, `waiting_approval`, `queued`.
- 조치 필요 item: status가 `waiting_approval`, `interrupted`, `failed`, `canceled`이거나 `retryable=true`, `resumable=true`이거나 `policy_status`가 `paused_failure`, `paused_interrupted`인 항목.
- 조치 필요 system: `health[].ready=false`, `intake_open=false`, `diagnostics.workspace_writable=false`.
- 중복 제거: 같은 operation은 `domain:id`로 한 번만 세며 여러 이유는 한 카드에 합친다.
- 정렬: 유효한 `updated_at` 내림차순, 날짜가 없거나 잘못된 항목은 뒤로 보낸다.
- 최대 표시: 진행 중과 조치 필요 각각 5건. 요약 count는 잘라내기 전 전체 개수다.
- `counts`는 화면 계산에 사용하지 않고 API 회귀 관찰용으로만 둔다.

`scheduled`와 `paused`는 현재 실행 중으로 세지 않는다. `paused`가 `resumable=true`이면 조치 필요에는 포함된다.

## 데이터 흐름과 책임 경계

```mermaid
flowchart LR
  DashboardView -->|독립 GET| Usage[/api/dashboard/usage]
  DashboardView -->|독립 GET| Operations[/api/operations]
  Operations --> Projection[co-located pure projection]
  Projection --> Active[진행 중 작업]
  Projection --> Health[시스템 상태]
  Projection --> Attention[조치 필요]
  DashboardView -->|onOpenTarget target| GatewayApp
  GatewayApp --> Detail[Chat / Team Run / Job / Schedule]
```

- `DashboardView`: source별 fetch state, 상태 분기, 한국어 presentation을 소유한다.
- co-located pure projection: raw operations 응답을 세 영역의 view model로 바꾼다. 전역 util이나 범용 abstraction으로 승격하지 않는다.
- `client.js`: 기존 `operations()`를 그대로 사용한다. endpoint 함수의 신규 production 변경은 없다.
- `GatewayApp`: 기존 `handleOpenOperationTarget`을 `onOpenTarget`으로 주입하고 화면 선택을 계속 소유한다.
- backend: raw read model과 인증을 소유한다. 1차에는 production 변경 없이 contract test만 보강한다.

## 오류 처리 기준

- 두 GET은 mount 시 함께 시작하지만 `Promise.all`의 단일 실패 경계로 묶지 않는다.
- 최초 loading은 widget별 skeleton/status를 표시한다. 한 source가 성공하면 다른 source를 기다리지 않고 렌더링한다.
- network/5xx 오류는 해당 widget 안에 쉬운 한국어 메시지와 `다시 시도`를 표시한다.
- 갱신 실패 시 마지막 성공 데이터를 지우지 않고 `최신 정보를 불러오지 못했습니다.`를 함께 표시한다.
- 401은 공통 `apiErrorAction` 판정에 따라 기존 재로그인 흐름으로 위임한다. 이를 위해 필요하면 `DashboardView`에 선택적 `onRelogin` prop만 추가하며 인증 UI를 중복 구현하지 않는다.
- payload의 선택 field가 없거나 날짜가 잘못돼도 화면 전체가 throw하지 않도록 빈 배열·안전한 라벨로 처리한다. 필수 top-level shape 자체가 잘못되면 해당 operations widget 오류로 처리한다.
- 사용량 미수집과 operations empty는 오류가 아니라 각각 기존 미수집 안내와 명시적 empty state로 표시한다.

## 변경 영향 범위

| 구분 | 경로 | 예정 변경 |
| --- | --- | --- |
| organism | `frontend/src/components/organisms/DashboardView/index.jsx` | operations 조회·상태·섹션·callback 추가 |
| projection | `frontend/src/components/organisms/DashboardView/operationsModel.js` | status/attention/health 순수 투영 |
| style | `frontend/src/components/organisms/DashboardView/DashboardView.css` | 요약·목록·상태 badge 반응형 style |
| component test | `frontend/src/components/organisms/DashboardView/DashboardView.test.jsx` | 두 API, 상태 격리, retry/empty/deep-link 검증 |
| model test | `frontend/src/components/organisms/DashboardView/operationsModel.test.js` | 경계 status, 중복 제거, 정렬, malformed data 검증 |
| parent | `frontend/src/components/containers/GatewayApp/index.jsx` | 기존 target/relogin callback 주입 |
| integration test | `frontend/src/components/containers/GatewayApp/GatewayApp.test.jsx` | Dashboard item에서 실제 화면 전환 검증 |
| client test | `frontend/src/api/client.test.js` | `GET /api/operations` 계약 고정; production client는 변경 없음 |
| backend contract | `tests/test_api_operations.py` | Dashboard가 의존하는 공통 field와 status signal 회귀 검증 |
| catalog | `frontend/src/components/references/organisms.md` | DashboardView public props·상태·API 갱신 |

실제 test file 명명은 기존 client test 구조를 먼저 확인하고 맞춘다. 위 목록 밖의 refactor는 포함하지 않는다.

## 세부 작업 분해와 착수 순서

| ID | 상태 | 담당 | 작업 | 선행 | 완료 근거 |
| --- | --- | --- | --- | --- | --- |
| T0 | DONE | 테크 리드 | existing component/API 추적, status 정책·비목표 확정 | 없음 | component 분석 보고서와 이 계획서 |
| T1 | DONE | 백엔드 담당 | `/api/operations` 공통 item/health/diagnostics 계약 test 보강 | T0 | `tests/test_api_operations.py` targeted PASS |
| T2 | DONE | 프런트엔드 담당 | `operationsModel` RED test 작성 후 최소 projection 구현 | T0 | model unit PASS |
| T3 | DONE | 프런트엔드 담당 | DashboardView에 독립 operations fetch와 loading/error/empty/stale UI 추가 | T2 | DashboardView test PASS |
| T4 | DONE | 프런트엔드 담당 | GatewayApp에 target 및 필요 시 relogin callback 연결 | T3 | GatewayApp deep-link integration PASS |
| T5 | DONE | 프런트엔드 담당 | CSS와 organism catalog를 실제 public 계약에 맞게 갱신 | T3, T4 | build 및 catalog 확인 |
| T6 | DONE | QA/검증 담당 | source 장애 격리, 401/5xx, empty, malformed payload, keyboard 접근 경로 회귀 | T1, T5 | targeted frontend/backend test와 build PASS |
| T7 | DONE | 테크 리드 | diff/contract/비목표 이탈 검토 후 완료 판정 | T6 | `git diff --check`, 검증 로그, 계획 status 갱신 |

T1과 T2는 T0 뒤에 병렬 착수할 수 있다. T3는 projection 계약, T4는 렌더링된 target 동작에 의존한다. backend production 변경은 아래 gate를 만족할 때만 별도 작업으로 연다.

## backend 보강 gate

현재 판정은 `필요 없음`이다. 다음 acceptance가 명시될 때만 최소 보강을 검토한다.

- degraded component를 `정상`, `저하`, `중단`으로 기계 판독하고 조치 필요 수에 반드시 포함해야 함.

이 경우 `ComponentHealth`에 optional `state: "ready" | "degraded" | "down"`을 추가하고 기존 `ready/detail`을 유지한다. `/api/operations` top-level에 Dashboard 전용 집계나 별도 endpoint는 만들지 않는다.

## 검증 전략

### 단위

- projection: 모든 allowlist status, terminal 제외, 중복 이유 병합, invalid timestamp, 누락 field, 최대 표시와 전체 count.
- DashboardView: usage/operations 동시 호출, 한쪽 실패 격리, 최초 loading, stale error, empty, retry, 한국어 라벨, `onOpenTarget`, 401 relogin.
- API client: `api.operations()`가 인증 header와 `GET /api/operations`를 사용하는지 검증.

### 통합·회귀

- GatewayApp: Dashboard의 session/team_run/job/schedule target이 기존 handler를 통해 맞는 화면과 focus로 이동.
- backend: representative session/team_run/job/schedule item에 공통 field가 있고 Team Run policy field, health, intake, diagnostics가 유지됨.
- 회귀: 기존 `OperationsView` action test와 Dashboard usage test 유지.

### 실행 명령

```text
cd frontend
npm test -- --run src/components/organisms/DashboardView/DashboardView.test.jsx src/components/organisms/DashboardView/operationsModel.test.js src/components/containers/GatewayApp/GatewayApp.test.jsx src/components/organisms/OperationsView/OperationsView.test.jsx
npm run build

python -m pytest tests/test_api_operations.py tests/test_api_dashboard.py -q
git diff --check
```

## 완료 기준

- [x] 사용량 widget과 운영 현황 widget이 서로 독립적으로 성공·실패·재시도한다.
- [x] 진행 중 작업·시스템 상태·조치 필요가 위 판정 규칙과 한국어 라벨로 표시된다.
- [x] operation `열기`가 기존 target navigation을 재사용한다.
- [x] Dashboard에는 상태 변경 mutation이 추가되지 않는다.
- [x] 신규 backend production endpoint/schema 변경이 없다.
- [x] 단위·통합·backend contract test와 frontend build가 통과한다.
- [x] 실제 변경에 맞춰 component catalog와 이 계획의 상태가 갱신된다.

## 분석 근거

- `frontend/src/components/organisms/DashboardView/index.jsx:117-204`
- `frontend/src/api/client.js:19-60,143-145,232-234`
- `frontend/src/components/containers/GatewayApp/index.jsx:236-247,624-635,655-659,774-775`
- `frontend/src/components/organisms/OperationsView/index.jsx:169-273`
- `src/personal_agent_gateway/api/operations.py:13-56,178-299`
- `src/personal_agent_gateway/health.py:42-76`
- `tests/test_api_operations.py:29-174`
- `tests/test_api_dashboard.py:20-79`
- [DashboardView Operations Expansion Analysis](../component-inspector/DashboardView/2026-07-22-1321.md)
