---
title: 대시보드 운영 현황 확장 구현 결과
type: report
domain: personal-agent-gateway
feature: dashboard-operations
status: done
aliases:
  - 대시보드 확장 구현 결과
  - Dashboard operations expansion report
  - 대시보드 운영 현황 통합 결과
tags:
  - dashboard
  - operations
  - usage
  - health
  - implementation
updated_at: 2026-07-22
---

# 대시보드 운영 현황 확장 구현 결과

## 결론

1차 대시보드 확장은 구현 및 targeted 검증을 완료했다. 기존 사용량 위젯은 유지하고, 같은 `DashboardView`에서 기존 `/api/operations`를 독립적으로 읽어 운영 현황을 읽기 전용으로 표시한다. backend endpoint·production schema·migration은 추가하지 않았다.

## 최종 위젯 구성과 데이터 소스

| 영역 | 표시 내용 | 데이터 소스 | 동작 |
| --- | --- | --- | --- |
| 이번 주 사용량 | provider별 가용성, 버전·모델, weekly limit/used/remaining/reset, 수집 상태 | `GET /api/dashboard/usage` → `providers[]`, `detected_at` | 수치가 없으면 `unconfirmed`/`partial`/`unavailable`로 표시하고 추정하지 않음 |
| 운영 요약 | 진행 중, 조치 필요, 정상 시스템 count | `/api/operations`의 `items`, `health`, `intake_open`, `diagnostics`를 frontend projection | 상태 변경 없음 |
| 진행 중 작업 | `planning`, `running`, `summarizing`, `waiting_approval`, `queued` 중 조치 필요로 분류되지 않은 항목, 최근순 최대 5건 | `/api/operations` → `items[]` | `target`이 있으면 기존 화면으로 `열기` |
| 시스템 상태 | component별 `ready/detail`, intake, access mode, workspace 쓰기 가능 여부 | `/api/operations` → `health[]`, `intake_open`, `access_mode`, `diagnostics.workspace_writable` | Health를 별도 endpoint로 재조회하지 않음 |
| 조치 필요 | 실패·중단·취소·승인 대기, retryable/resumable, policy pause와 intake/workspace/Health 경고, 최근순 최대 5건 | `/api/operations` → `items[]`, `health[]`, `intake_open`, `diagnostics` | item target이 있으면 `열기`; Retry/Resume/Emergency Stop은 기존 Operations가 소유 |

사용량과 운영 현황은 각각 독립 effect·loading·error·retry 상태를 가진다. 따라서 한 endpoint가 실패해도 다른 위젯의 성공 데이터는 유지된다. `operationsModel.js`는 `items`의 status allowlist로 active/attention을 계산하며 API의 `counts`를 화면 합계의 근거로 사용하지 않는다. 이는 Team Run의 `planning`·`summarizing`이 `counts`에 없을 수 있다는 계약 차이를 보존한다.

## 결정 사항

### Health는 Operations 응답으로 통합

인증된 Dashboard는 `/health/ready`를 별도로 호출하지 않고, `/api/operations`의 `health`, `intake_open`, `diagnostics`를 시스템 상태와 조치 필요 영역에 함께 투영한다. Operations API가 실행 항목과 운영 진단을 같은 인증 경계에서 제공하고, 대시보드에 별도 Health 집계 endpoint가 필요하지 않기 때문이다.

`health[].ready === false`만 1차 조치 필요 count에 포함한다. `ready === true`이면서 `detail`에 `degraded` 문구가 있는 경우는 시스템 상태 상세에 표시하되, 별도 enum이나 장애 count로 승격하지 않는다. degraded를 기계 판독해 반드시 경고해야 한다는 요구가 확인될 때만 optional `state` 계약을 별도 검토한다.

### 재사용 대비 신규 개발 판정

- 재사용: 기존 `api.dashboardUsage()`와 `api.operations()`, `/api/dashboard/usage`·`/api/operations` payload, `StatusBadge`, `GatewayApp.handleOpenOperationTarget`을 그대로 사용한다.
- 신규 frontend: `DashboardView`의 operations fetch/상태/섹션, co-located 순수 projection `operationsModel.js`, Dashboard 전용 style 및 단위·컴포넌트·통합 테스트를 추가했다.
- 신규 backend: 없음. `/api/dashboard/operations` 같은 중복 집계 endpoint, schema 변경, migration, OperationsView mutation 복제는 하지 않았다.
- 범위 경계: Dashboard의 `열기`는 navigation만 수행하며 retry/resume/emergency-stop/backup은 기존 `OperationsView`가 계속 소유한다.

## 2차 이관 항목

다음 항목은 이번 구현에 포함하지 않았으며, 실제 데이터 계약 또는 제품 기준이 확정된 뒤 별도 작업으로 착수한다.

1. 미확인 Hook 배지: Hook 장애/미확인 상태를 Dashboard 경보로 합산할 전역 기준과 source가 없다.
2. 사용량 실제 수치: 현재 local usage reader는 확정된 provider별 실제 source가 없어 `weekly_limit`, `used`, `reset_at`을 비워 반환한다. 숫자를 추정하거나 합산하지 않는다.
3. 전역 Hook-run 피드: 현재 Operations 응답은 Hook-run 전체 피드를 제공하지 않으며, hook별 run 조회를 전역 대시보드 feed로 바꾸는 계약도 없다.
4. 추세/SLA: 기간·성공 정의·복구 성공 정의·허용 지연과 집계 보존 정책이 합의되지 않아 trend, success rate, SLA 위젯을 추가하지 않는다.

## 알려진 한계

- 운영 목록은 화면 표시를 위해 active/attention 각각 최대 5건만 렌더링한다. 요약 count는 잘라내기 전 전체 item과 system warning을 기준으로 한다.
- `scheduled`와 `paused`는 active로 세지 않는다. `paused`는 `resumable`이면 조치 필요로 분류한다.
- `tunnel_mode`은 현재 `not_reported`이므로 시스템 정상/비정상 판정에 사용하지 않는다.
- `health`가 비어 있으면 정상으로 단정하지 않고 “시스템 상태 정보가 없습니다.”를 표시한다. `healthyCount`도 실제 `ready === true` component만 센다.
- Operations 갱신 실패 시 마지막 성공 데이터를 유지하고 오류를 함께 표시하지만, 최초 조회 실패 시에는 운영 영역을 표시할 데이터가 없다.
- 사용량 카드의 gauge는 `used`와 `weekly_limit`이 모두 유한한 숫자이고 limit가 양수일 때만 표시된다. 현재 기본 provider 응답은 소스 미확정으로 gauge를 만들지 않는다.

## 검증 결과

| 검증 | 결과 |
| --- | --- |
| `frontend` targeted Vitest: DashboardView, operationsModel, GatewayApp, OperationsView | 4 files, 60 tests passed |
| `python -m pytest tests/test_api_operations.py tests/test_api_dashboard.py -q --basetemp frontend/node_modules/.pytest-dashboard-plan` | 7 passed |
| `frontend` `npm run build` | production build passed |

Build에는 기존 정적 vendor 경로 관련 경고가 남았다(`highlight.min.js` module 속성, github-dark CSS, Pretendard font 경로). bundle 생성 자체는 성공했으며 이 문서의 대시보드 변경으로 추가된 실패는 확인되지 않았다.

## 인계 및 관련 문서

- 계획과 완료 상태: [대시보드 운영 현황 확장 구현 계획](../todo/2026-07-22-dashboard-operations-expansion-plan.md)
- 구조·계약 분석: [DashboardView Operations Expansion Analysis](../component-inspector/DashboardView/2026-07-22-1321.md)
- 기존 운영 권고: [대시보드 항목 우선순위 권고안](../../../artifacts/2026-07-22-dashboard-widget-recommendation.md), [1차 대시보드 위젯 요구사항](../../../artifacts/2026-07-22-dashboard-widget-requirements.md)
- 사용량 명세: [대시보드 사용량 명세](../../../artifacts/dashboard-usage-spec.md)
- 기존 운영 계약: [R1 운영 가능성 구현 보고서](2026-07-15-r1-operability-implementation.md)
- 제품 권고의 원문: [기획 PM 사용성·기능 기회 진단](2026-07-15-product-pm-usability-opportunities.md)

후속 owner는 provider별 실제 사용량 source의 존재·필드·단위를 실제 CLI 계정 환경에서 확인하고, source가 확정될 때만 `local_usage._read_usage`와 관련 contract test를 갱신한다. 그 전까지 Dashboard는 미확정 상태를 그대로 표시한다.

## 근거 경로

- `frontend/src/components/organisms/DashboardView/index.jsx:1-385`
- `frontend/src/components/organisms/DashboardView/operationsModel.js:1-85`
- `frontend/src/components/organisms/DashboardView/DashboardView.test.jsx:1-185`
- `frontend/src/components/organisms/DashboardView/operationsModel.test.js:1-58`
- `frontend/src/components/containers/GatewayApp/index.jsx:624-635,774-778`
- `frontend/src/api/client.js:143-145,232-234`
- `src/personal_agent_gateway/api/dashboard.py:8-14`
- `src/personal_agent_gateway/api/operations.py:13-56,178-299`
- `src/personal_agent_gateway/local_usage.py:8-12,41-49,77-113`
- `tests/test_api_dashboard.py:20-79`
- `tests/test_api_operations.py:29-174`
- `frontend/src/components/references/organisms.md:11-16`
