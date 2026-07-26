# PAG-LMG Operational Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 동시 실행 폭주, 같은 upstream session의 경쟁 resume, 세션 목록의 반복 전체 scan, 공유 Claude 저장소의 과잉 삭제, LMG 장애가 빈 데이터로 숨는 문제를 제거한다.

**Architecture:** LMG 진입점의 한 admission manager가 global/provider 실행 수를 원자적으로 예약하고 bounded wait queue와 upstream-session reservation을 관리한다. 세션 파일 경로와 크기는 run 종료 시 한 번 찾아 SQLite에 저장하고 list/delete는 DB 경로만 사용한다. readiness/capability/query 결과는 transport failure와 빈 결과를 구분하며 PAG dashboard가 상태를 표시한다.

**Tech Stack:** Python 3.11, FastAPI, React/JavaScript, pytest, Vitest, Go 1.26.5, channels, mutex, SQLite, GitHub Actions

## Global Constraints

- 선행 조건은 앞의 local interface, terminal stream, session lifecycle 계획 구현 완료다.
- 기본 제한은 global 4, Codex 2, Claude 2, OpenAI 4, 대기 16이다.
- 같은 `provider + upstream_session_id` resume이 이미 실행 중이면 대기시키지 않고 409 `session_busy`를 반환한다. 새 session은 session ID가 생기기 전이라 session lock 대상이 아니다.
- queue가 가득 차면 provider를 실행하지 않고 429 `capacity_exceeded` JSON 오류를 반환한다.
- 세션 list 요청에서 filesystem tree walk를 하지 않는다.
- Claude의 공유 `~/.claude`에서는 LMG DB가 정확한 파일 경로를 기록한 session만 삭제한다. substring 재귀 삭제를 금지한다.
- migration 전 기존 DB처럼 storage 검증 이력이 없고 path도 없는 record는 `stale`로 보이되 자동 scan/delete하지 않는다. resolver가 파일 부재를 확인해 `missing`으로 기록한 row와 기록된 path의 파일이 검증 후 없어진 row는 DELETE가 DB row를 정리하고 성공한다.
- 배포는 Task 1~5의 LMG 변경을 먼저 병합·배포하고 Task 6~8의 PAG 변경과 E2E를 그 뒤에 병합한다. PAG CI의 E2E는 LMG 기본 branch 계약을 사용한다.
- UI Operations 줄바꿈 수정은 이미 별도 변경 범위이며 이 계획에 섞지 않는다.

---

## File Structure

### local-model-gateway

- Modify: `../local-model-gateway/internal/config/config.go`
- Modify: `../local-model-gateway/internal/config/config_test.go`
- Create: `../local-model-gateway/internal/limit/manager.go`
- Create: `../local-model-gateway/internal/limit/manager_test.go`
- Modify: `../local-model-gateway/internal/httpapi/router.go`
- Modify: `../local-model-gateway/internal/httpapi/runs.go`
- Modify: `../local-model-gateway/internal/httpapi/runs_test.go`
- Modify: `../local-model-gateway/internal/httpapi/sessions.go`
- Modify: `../local-model-gateway/internal/httpapi/sessions_test.go`
- Modify: `../local-model-gateway/internal/session/session.go`
- Modify: `../local-model-gateway/internal/session/session_test.go`
- Create: `../local-model-gateway/internal/health/health.go`
- Create: `../local-model-gateway/internal/health/health_test.go`
- Modify: `../local-model-gateway/internal/models/models.go`
- Modify: `../local-model-gateway/internal/models/models_test.go`
- Modify: `../local-model-gateway/cmd/lmg/main.go`
- Create: `../local-model-gateway/.github/workflows/test.yml`
- Modify: `../local-model-gateway/README.md`

### personal-agent-gateway

- Modify: `src/personal_agent_gateway/lmg_client.py`
- Modify: `src/personal_agent_gateway/agents.py`
- Modify: `src/personal_agent_gateway/api/dashboard.py`
- Modify: `frontend/src/components/organisms/DashboardView/index.jsx`
- Modify: `frontend/src/components/organisms/DashboardView/DashboardView.test.jsx`
- Modify: `tests/test_lmg_client.py`
- Modify: `tests/test_agents.py`
- Modify: `tests/test_api_dashboard.py`
- Create: `tests/integration/test_pag_lmg_e2e.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`

## Task 1: 제한 설정과 limiter 단위

**Files:**
- Modify: `../local-model-gateway/internal/config/config.go`
- Modify: `../local-model-gateway/internal/config/config_test.go`
- Create: `../local-model-gateway/internal/limit/manager.go`
- Create: `../local-model-gateway/internal/limit/manager_test.go`

- [ ] **Step 1: config 기본값/오류 테스트 작성**

다음 환경 변수를 검사한다.

| 변수 | 기본값 | 유효 범위 |
|---|---:|---:|
| `LMG_MAX_CONCURRENT` | 4 | 1 이상 |
| `LMG_MAX_CONCURRENT_CODEX` | 2 | 1 이상 |
| `LMG_MAX_CONCURRENT_CLAUDE` | 2 | 1 이상 |
| `LMG_MAX_CONCURRENT_OPENAI` | 4 | 1 이상 |
| `LMG_MAX_QUEUE` | 16 | 0 이상 |

- [ ] **Step 2: limiter 동작 테스트 작성**

다음 순서를 deterministic channel test로 검증한다.

- global cap과 provider cap 중 먼저 찬 제한이 다음 실행을 대기시킴
- provider A의 cap 대기자가 global slot을 선점하지 않아 provider B가 남은 global capacity로 실행됨
- 대기 수가 max queue를 넘으면 `ErrQueueFull`
- waiting context 취소 시 slot 누수 없음
- release 후 대기 실행 하나가 진입
- 같은 provider/upstream ID의 두 번째 acquire는 즉시 `ErrSessionBusy`
- 다른 upstream ID는 cap 안에서 병렬 실행
- release를 두 번 호출해도 panic/slot 증가 없음

- [ ] **Step 3: 실패 확인**

Run: `cd ../local-model-gateway && go test ./internal/config ./internal/limit`

Expected: limit 패키지와 config 필드가 없어 실패한다.

- [ ] **Step 4: 최소 limiter 구현**

```go
type Config struct {
    MaxConcurrent int
    ProviderMax   map[string]int
    MaxQueue      int
}

type Manager struct {
    mu                sync.Mutex
    maxGlobal         int
    providerMax       map[string]int
    running           int
    runningByProvider map[string]int
    maxQueue          int
    waiting           int
    reservedSessions  map[string]struct{}
    changed           chan struct{}
}

type Snapshot struct {
    Running           int
    RunningByProvider map[string]int
    GlobalCapacity    int
    ProviderCapacity  map[string]int
    Waiting           int
    QueueLimit        int
}

func (m *Manager) Acquire(
    ctx context.Context,
    providerName string,
    upstreamSessionID string,
) (release func(), err error)

func (m *Manager) Snapshot() Snapshot
```

대기자 수 증감과 global/provider 실행 수 예약은 같은 mutex 아래 처리한다. session key는 첫 진입 때 reservation하고 이미 존재하면 `ErrSessionBusy`를 즉시 반환한다. global과 provider capacity가 모두 남은 경우에만 두 실행 수를 한 번에 증가시킨다. 하나라도 찼으면 queue admission 뒤 `changed` channel을 기다리며, 대기 중에는 어떤 실행 slot도 점유하지 않는다. 취소와 queue full에서는 session reservation과 waiting count를 정리한다. release는 `sync.Once`로 실행 수와 session reservation을 제거하고 새 `changed` channel로 교체하면서 기존 channel을 닫아 대기자를 깨운다.

`Snapshot`은 내부 map을 복사해 반환한다. `CanAcceptAny(readyProviders)` helper는 준비된 provider 중 즉시 실행 capacity가 있거나 queue에 빈자리가 있는지를 계산한다. `QueueLimit == 0`이어도 현재 즉시 실행 가능한 provider가 있으면 수용 가능하다.

- [ ] **Step 5: limiter 테스트 통과 확인**

Run: `cd ../local-model-gateway && go test -race ./internal/config ./internal/limit`

Expected: PASS

- [ ] **Step 6: 커밋**

```bash
cd ../local-model-gateway
git add internal/config/config.go internal/config/config_test.go internal/limit/manager.go internal/limit/manager_test.go
git commit -m "feat: bound LMG provider concurrency"
```

## Task 2: RunsHandler admission 적용

**Files:**
- Modify: `../local-model-gateway/internal/httpapi/router.go`
- Modify: `../local-model-gateway/internal/httpapi/runs.go`
- Modify: `../local-model-gateway/internal/httpapi/runs_test.go`
- Modify: `../local-model-gateway/cmd/lmg/main.go`

- [ ] **Step 1: HTTP admission 테스트 작성**

provider가 block되는 fixture로 다음을 검사한다.

- cap 내 요청은 SSE 200
- queue에 들어온 요청은 slot release 후 실행
- queue full은 SSE header를 쓰기 전에 429와 `{"code":"capacity_exceeded"}` 반환
- waiting client 취소는 provider를 실행하지 않음
- 동일 upstream session의 두 번째 resume은 SSE header 전에 409와 `{"code":"session_busy"}` 반환

- [ ] **Step 2: 실패 확인**

Run: `cd ../local-model-gateway && go test -race ./internal/httpapi`

Expected: 현재 모든 요청이 즉시 provider로 들어가 실패한다.

- [ ] **Step 3: handler admission 구현**

`Deps`에 `Limiter *limit.Manager`를 추가한다. JSON·provider·execution 검증 후 SSE header 전에 `Acquire`한다. `ErrSessionBusy`는 409, `ErrQueueFull`은 429, request cancellation은 응답 추가 쓰기 없이 반환, 다른 limiter 내부 오류는 503이다. 획득 성공 즉시 `defer release()`한다.

- [ ] **Step 4: main 조립**

config 기본값으로 manager를 하나 만들고 router deps에 전달한다. limiter snapshot은 Task 5 readiness가 같은 instance를 사용하게 보관한다.

- [ ] **Step 5: HTTP 테스트 통과 확인**

Run: `cd ../local-model-gateway && go test -race ./internal/httpapi ./internal/limit`

Expected: PASS

- [ ] **Step 6: 커밋**

```bash
cd ../local-model-gateway
git add internal/httpapi/router.go internal/httpapi/runs.go internal/httpapi/runs_test.go cmd/lmg/main.go
git commit -m "feat: apply LMG run admission control"
```

## Task 3: 세션 저장 경로를 DB metadata로 전환

**Files:**
- Modify: `../local-model-gateway/internal/session/session.go`
- Modify: `../local-model-gateway/internal/session/session_test.go`
- Modify: `../local-model-gateway/internal/httpapi/runs.go`
- Modify: `../local-model-gateway/internal/httpapi/runs_test.go`
- Modify: `../local-model-gateway/internal/httpapi/sessions.go`
- Modify: `../local-model-gateway/internal/httpapi/sessions_test.go`
- Modify: `../local-model-gateway/cmd/lmg/main.go`

- [ ] **Step 1: DB migration과 상태 테스트 작성**

기존 DB에 다음 column을 additive migration하고 list/get에서 읽는지 검사한다.

```text
storage_path TEXT
size_bytes INTEGER NOT NULL DEFAULT 0
storage_status TEXT NOT NULL DEFAULT 'stale'
```

`storage_status` 값은 `ready`, `missing`, `stale` 세 가지로 제한한다.

- [ ] **Step 2: list no-scan 테스트 작성**

SessionsHandler의 scan dependency를 제거하고, 호출 횟수를 세는 filesystem scanner 없이 DB record만으로 JSON이 만들어지는지 검사한다.

- [ ] **Step 3: run 종료 후 정확한 파일을 한 번 resolve하는 테스트 작성**

새 upstream ID를 기록한 run은 provider 종료 뒤 resolver를 한 번 호출해 canonical file path와 해당 파일의 `os.Stat().Size()`를 저장한다. Claude는 basename이 `<upstream-id>.jsonl`과 정확히 같아야 하고, Codex는 `.jsonl` stem이 upstream ID로 끝나는 rollout 파일만 일치시킨다. 일치 파일이 없으면 `missing`, 둘 이상이거나 scan/stat 오류면 run terminal은 바꾸지 않고 `stale` 및 server log를 남긴다.

- [ ] **Step 4: 실패 확인**

Run: `cd ../local-model-gateway && go test ./internal/session ./internal/httpapi`

Expected: DB가 metadata를 저장하지 않고 list마다 scan해서 실패한다.

- [ ] **Step 5: store migration과 update 구현**

다음 메서드를 추가한다.

```go
func (s *Store) UpdateStorage(
    upstreamID string,
    storagePath string,
    sizeBytes int64,
    status string,
) error

func (s *Store) Ping(ctx context.Context) error
```

`Record`, `List`, `Get` query가 storage metadata와 계획 3의 consumer metadata를 모두 보존하게 한다.

- [ ] **Step 6: handler scan 제거**

`Deps.Scan`을 `ResolveStorage func(session.Record) (string, int64, error)`로 바꾸고 RunsHandler에서 session record 직후 한 번 호출한다. resolver는 디렉터리가 아니라 단일 regular file의 canonical path만 반환한다. SessionsHandler의 GET list에서는 resolver를 호출하지 않는다.

- [ ] **Step 7: 테스트 통과 확인**

Run: `cd ../local-model-gateway && go test ./internal/session ./internal/httpapi`

Expected: PASS

- [ ] **Step 8: 커밋**

```bash
cd ../local-model-gateway
git add internal/session/session.go internal/session/session_test.go internal/httpapi/runs.go internal/httpapi/runs_test.go internal/httpapi/sessions.go internal/httpapi/sessions_test.go cmd/lmg/main.go
git commit -m "perf: persist LMG session storage metadata"
```

## Task 4: 기록된 정확한 파일만 삭제

**Files:**
- Modify: `../local-model-gateway/internal/session/session.go`
- Modify: `../local-model-gateway/internal/session/session_test.go`
- Modify: `../local-model-gateway/internal/httpapi/sessions.go`
- Modify: `../local-model-gateway/internal/httpapi/sessions_test.go`
- Modify: `../local-model-gateway/cmd/lmg/main.go`

- [ ] **Step 1: 안전 삭제 테스트 작성**

다음을 검사한다.

- `ready` record의 정확한 단일 파일 삭제
- path가 provider storage root 밖이면 거부
- symlink가 root 밖을 가리키면 거부
- `stale`/빈 path는 파일을 건드리지 않고 DB record도 남기며 409
- resolver가 확인한 `missing` record는 DB row 삭제 후 204
- 이미 파일이 없지만 `ready` record면 DB row 삭제 후 204
- 같은 upstream ID substring을 가진 다른 파일은 보존

- [ ] **Step 2: 실패 확인**

Run: `cd ../local-model-gateway && go test ./internal/session ./internal/httpapi`

Expected: 현재 recursive substring delete가 다른 파일까지 찾을 수 있어 실패한다.

- [ ] **Step 3: 정확한 path 삭제 구현**

`DeleteStorageFor`의 시그니처를 record 기반으로 바꾸고 recursive walk를 제거한다.

```go
func DeleteRecordedStorage(
    rec Record,
    codexHome string,
    claudeHome string,
) error
```

provider별 root를 `EvalSymlinks`하고 `filepath.Rel`로 포함 관계를 확인한 뒤 해당 파일 하나만 `os.Remove`한다. target이 존재하면 target도 `EvalSymlinks`해 root 밖 symlink를 거부한다. target이 이미 없으면 canonical parent가 root 안인지 다시 확인한 뒤 성공으로 처리한다. `missing` status는 파일 작업 없이 성공이고, `stale` 또는 빈 path는 소유 대상을 증명할 수 없으므로 별도 typed 오류다.

- [ ] **Step 4: stale 구분과 DB 순서 구현**

handler는 storage delete 성공 후에만 DB row를 지운다. `missing` status 또는 기록된 canonical path의 파일이 이미 없는 경우 `DeleteRecordedStorage`가 성공이므로 row를 정리하고 204를 반환한다. legacy record처럼 검증 이력 없이 `stale`이고 `StoragePath`도 비어 있어 소유 파일을 특정할 수 없는 경우만 409 `storage_metadata_stale`로 남겨 read-only reconciliation 대상이 되게 한다.

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd ../local-model-gateway && go test ./internal/session ./internal/httpapi`

Expected: PASS

- [ ] **Step 6: 커밋**

```bash
cd ../local-model-gateway
git add internal/session/session.go internal/session/session_test.go internal/httpapi/sessions.go internal/httpapi/sessions_test.go cmd/lmg/main.go
git commit -m "fix: delete only recorded LMG session files"
```

## Task 5: live/readiness/capability 상태 분리

**Files:**
- Create: `../local-model-gateway/internal/health/health.go`
- Create: `../local-model-gateway/internal/health/health_test.go`
- Modify: `../local-model-gateway/internal/httpapi/router.go`
- Modify: `../local-model-gateway/internal/models/models.go`
- Modify: `../local-model-gateway/internal/models/models_test.go`
- Modify: `../local-model-gateway/cmd/lmg/main.go`

- [ ] **Step 1: health 테스트 작성**

`/livez`는 process handler가 응답하면 항상 200이다. 인증된 `/readyz`는 DB ping 성공, queue가 full이 아님, `Provider.Preflight()`에 성공하는 provider가 하나 이상임을 모두 만족할 때만 200이다. DB 실패, queue full, 모든 provider unavailable은 503이다. 일부 provider만 unavailable이면 200을 유지하고 provider별 상태를 응답에 표시한다. 응답에는 token, binary path, workspace path를 포함하지 않는다.

- [ ] **Step 2: capability protocol 테스트 작성**

`/v1/models` report에 기존 `schema_version: 1`을 유지하면서 다음 top-level 필드를 추가한다.

```json
{
  "protocol_version": "1.1",
  "gateway_status": "ready",
  "providers": {
    "codex": {
      "capabilities": {
        "resume": true,
        "sandbox_modes": ["read-only", "workspace-write", "danger-full-access"],
        "permission_modes": []
      }
    },
    "claude": {
      "capabilities": {
        "resume": true,
        "sandbox_modes": [],
        "permission_modes": ["default", "acceptEdits", "plan", "bypassPermissions"]
      }
    },
    "openai": {
      "capabilities": {
        "resume": false,
        "sandbox_modes": [],
        "permission_modes": []
      }
    }
  }
}
```

provider별 available/error는 기존 detection 결과를 유지한다. sandbox/permission 배열은 detector가 보고한 실제 option과 위 provider 지원 종류를 교차해 생성하며, 감지되지 않은 값을 새로 주장하지 않는다. OpenAI는 LMG가 conversation resume을 구현하지 않았으므로 false다. `gateway_status`는 같은 health checker의 `ready`/`not_ready` 결과를 사용한다.

- [ ] **Step 3: 실패 확인**

Run: `cd ../local-model-gateway && go test ./internal/health ./internal/models ./internal/httpapi`

Expected: readiness와 protocol metadata가 없어 실패한다.

- [ ] **Step 4: health checker 구현**

```go
type Checker struct {
    Store         interface{ Ping(context.Context) error }
    Limiter       interface{ Snapshot() limit.Snapshot }
    ProviderReady func(context.Context) map[string]bool
}

func (c Checker) Ready(ctx context.Context) Status
```

`Status`에는 `Ready bool`, redacted component 상태, provider별 boolean을 둔다. readiness는 provider preflight 결과를 먼저 구한 뒤 `Snapshot.CanAcceptAny()`로 준비된 provider가 즉시 실행되거나 queue에 들어갈 수 있는지 판정한다. router에 인증된 `/readyz`를 추가하고 main의 실제 store/limiter 및 registry provider preflight closure를 연결한다. `/v1/models` handler도 같은 `Status`를 받아 `gateway_status`가 readiness와 모순되지 않게 한다.

- [ ] **Step 5: health/capability 테스트 통과 확인**

Run: `cd ../local-model-gateway && go test ./internal/health ./internal/models ./internal/httpapi`

Expected: PASS

- [ ] **Step 6: 커밋**

```bash
cd ../local-model-gateway
git add internal/health/health.go internal/health/health_test.go internal/httpapi/router.go internal/models/models.go internal/models/models_test.go cmd/lmg/main.go
git commit -m "feat: distinguish LMG liveness and readiness"
```

## Task 6: PAG에서 빈 결과와 LMG 장애 구분

**Files:**
- Modify: `src/personal_agent_gateway/lmg_client.py`
- Modify: `src/personal_agent_gateway/agents.py`
- Modify: `src/personal_agent_gateway/api/dashboard.py`
- Modify: `tests/test_lmg_client.py`
- Modify: `tests/test_agents.py`
- Modify: `tests/test_api_dashboard.py`

- [ ] **Step 1: typed query result 테스트 작성**

models/sessions 요청에서 다음을 구분하는 테스트를 추가한다.

- 정상 빈 목록: `status="ready"`, data 빈 값
- connection/timeout: `status="unreachable"`
- 401: `status="unauthorized"`
- 503: `status="not_ready"`
- JSON/schema 오류: `status="protocol_error"`

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_lmg_client.py tests/test_agents.py tests/test_api_dashboard.py -q`

Expected: 현재 `None`과 `[]`로 모든 장애를 숨겨 실패한다.

- [ ] **Step 3: generic result 구현**

```python
T = TypeVar("T")

@dataclass(frozen=True)
class LmgQueryResult(Generic[T]):
    data: T | None
    status: Literal[
        "ready",
        "unreachable",
        "unauthorized",
        "not_ready",
        "protocol_error",
    ]
    message: str | None = None
```

응답 body나 exception 원문에 token/민감 경로가 있을 수 있으므로 `message`에는 고정된 사용자 안전 문구만 넣는다.

- [ ] **Step 4: registry와 dashboard 적용**

Agent registry는 `result.status == "ready"`일 때만 capability data를 사용하고 기존 fallback은 유지하되 상태를 log한다. Dashboard API는 다음 shape을 반환한다.

```python
{
    "sessions": result.data or [],
    "lmg": {"status": result.status, "message": result.message},
}
```

- [ ] **Step 5: API 테스트 통과 확인**

Run: `uv run pytest tests/test_lmg_client.py tests/test_agents.py tests/test_api_dashboard.py -q`

Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add src/personal_agent_gateway/lmg_client.py src/personal_agent_gateway/agents.py src/personal_agent_gateway/api/dashboard.py tests/test_lmg_client.py tests/test_agents.py tests/test_api_dashboard.py
git commit -m "feat: expose LMG query health to PAG"
```

## Task 7: Dashboard 상태 표현

**Files:**
- Modify: `frontend/src/components/organisms/DashboardView/index.jsx`
- Modify: `frontend/src/components/organisms/DashboardView/DashboardView.test.jsx`

- [ ] **Step 1: UI 실패 테스트 작성**

다음 API 상태별 문구를 검사한다.

- ready + 빈 sessions: `로컬 세션 없음`
- unreachable: `로컬 모델 게이트웨이에 연결할 수 없습니다.`
- unauthorized: `로컬 모델 게이트웨이 인증에 실패했습니다.`
- not_ready: `로컬 모델 게이트웨이가 준비되지 않았습니다.`
- protocol_error: `로컬 모델 게이트웨이 응답 형식이 올바르지 않습니다.`

- [ ] **Step 2: 실패 확인**

Run: `cd frontend && npm test -- src/components/organisms/DashboardView/DashboardView.test.jsx`

Expected: 현재 dashboard가 sessions와 LMG 상태를 구분하지 않아 실패한다.

- [ ] **Step 3: 상태 분기 구현**

dashboard state에 `sessions`와 `lmg`를 함께 저장한다. `lmg.status`가 ready가 아닐 때 빈 목록 UI 대신 위 상태 문구와 기존 reload 동작을 사용하는 재시도 버튼을 렌더링한다. 상세 exception이나 token은 표시하지 않는다.

- [ ] **Step 4: frontend 검증**

Run: `cd frontend && npm test -- src/components/organisms/DashboardView/DashboardView.test.jsx`

Expected: PASS

Run: `cd frontend && npm run build`

Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/components/organisms/DashboardView/index.jsx frontend/src/components/organisms/DashboardView/DashboardView.test.jsx
git commit -m "feat: show LMG health on dashboard"
```

## Task 8: fake provider 기반 PAG-LMG 교차 저장소 E2E

**Files:**
- Create: `tests/integration/test_pag_lmg_e2e.py`

- [ ] **Step 1: 실제 프로세스 fixture 작성**

pytest fixture가 다음 순서로 격리된 테스트 환경을 만든다.

1. 빈 loopback port와 임시 `LMG_DATA_DIR`, 임시 workspace를 만든다.
2. 테스트 파일 안의 고정 문자열로 실행 가능한 fake agent CLI를 임시 디렉터리에 생성한다. Codex 인자는 JSONL, Claude 인자는 stream-json을 출력하며, 입력 prompt의 control marker에 따라 정상·session ID 생성 후 실패·부분 출력 후 지연을 재현한다. fake는 발급한 session ID와 정확히 일치하는 provider별 transcript 파일도 격리 홈에 생성한다.
3. `LMG_SOURCE_DIR` 환경 변수가 있으면 그 경로, 없으면 `../local-model-gateway`를 사용한다. 해당 디렉터리에서 `go build -o <tmp>/lmg ./cmd/lmg`를 실행한다.
4. `HOME=<tmp-home>`, `LMG_HOST=127.0.0.1`, 선택한 port, `LMG_LOCAL_TOKEN`, 임시 data/workspace allowlist, fake `LMG_CODEX_BIN`/`LMG_CLAUDE_BIN`을 넣어 LMG subprocess를 시작한다.
5. `/livez`가 응답할 때까지 짧은 bounded poll을 수행하고 PAG app/client를 해당 base URL과 같은 token으로 생성한다.
6. fixture 종료 시 PAG client, LMG process 순으로 종료하고 timeout 뒤에만 kill한다.

실제 사용자 홈, 실제 Codex/Claude 로그인 파일, 외부 network에는 접근하지 않는다. 테스트 skip 조건을 두지 않으며 Go toolchain이 없으면 명시적으로 실패한다.

- [ ] **Step 2: 정상 실행·resume·삭제 시나리오 작성**

Codex와 Claude를 parameterize해 첫 실행/link/resume을 모두 실제 HTTP/SSE 경계에서 검증한다. 삭제 실패 사례를 포함한 나머지는 Codex fixture 하나로 실행 시간을 제한한다.

- 첫 메시지: `session.updated` 직후 PAG transcript에 link가 생기고 LMG session record의 `consumer_session_id`, `consumer_run_id`, `consumer_context_fingerprint`가 일치한다.
- 두 번째 메시지: 같은 fingerprint가 같은 upstream ID로 resume된다.
- Space 또는 Persona를 바꾼 다음 메시지: context fingerprint가 달라져 기존 upstream을 resume하지 않고 새 session을 만든다.
- Chat 삭제: 연결된 LMG session과 PAG Chat이 함께 사라진다.
- 연결된 대상 중 하나의 기록된 파일이 이미 없어도 전체 Chat 삭제가 성공한다.
- LMG session API가 보고한 storage file의 parent 권한을 임시로 read/execute-only로 바꿔 첫 삭제에서 I/O 오류를 만들면 Chat이 보존된다. `finally`에서 원래 권한을 복구한 뒤 재시도하면 전체 삭제에 성공한다.
- 같은 LMG DELETE 재호출: 204이고 부작용이 없다.

- [ ] **Step 3: 실패·보안·동시성 시나리오 작성**

독립 테스트로 다음을 검증한다.

- 첫 실행이 session ID를 만든 뒤 provider 실패해도 PAG 상태는 `failed`이고 upstream link가 보존된다.
- 부분 출력 후 overall timeout은 terminal `run_timeout`으로 끝나며 partial content가 진단 정보로 보존되고 성공으로 기록되지 않는다.
- 호출자 취소는 terminal `run_cancelled`로 끝나며 성공으로 기록되지 않는다.
- allowlist 밖 workspace와 symlink escape는 provider 실행 전 422 `invalid_execution_path`다.
- 같은 upstream session의 동시 resume은 하나만 실행되고 다른 요청은 즉시 409 `session_busy`다.
- global/provider cap을 넘은 요청은 bounded queue에 들어가며 queue가 차면 429 `capacity_exceeded`다.

- [ ] **Step 4: E2E 실패 확인**

Run: `uv run pytest tests/integration/test_pag_lmg_e2e.py -q`

Expected: 앞 계획들의 양쪽 구현이 모두 반영되기 전에는 fixture 또는 계약 assertion이 실패한다.

- [ ] **Step 5: 앞 태스크의 계약 누락만 수정**

E2E 실패가 드러낸 계약 누락을 소유 태스크의 파일에서 최소 수정한다. 테스트 전용 분기나 production code의 fake marker 해석은 추가하지 않는다.

- [ ] **Step 6: E2E 통과 확인**

Run: `uv run pytest tests/integration/test_pag_lmg_e2e.py -q`

Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add tests/integration/test_pag_lmg_e2e.py
git commit -m "test: cover PAG-LMG lifecycle end to end"
```

## Task 9: Go CI·문서·전체 회귀

**Files:**
- Create: `../local-model-gateway/.github/workflows/test.yml`
- Modify: `../local-model-gateway/README.md`
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`

- [ ] **Step 1: Go 1.26.5 CI 작성**

workflow는 pull request와 main push에서 checkout, `actions/setup-go`의 `go-version-file: go.mod`, `go test -race ./...`를 실행한다.

- [ ] **Step 2: PAG CI에 교차 저장소 E2E 환경 추가**

기존 PAG checkout 뒤 `actions/checkout@v4`를 한 번 더 사용해 `LeeLeeLeeee/local-model-gateway`를 `local-model-gateway/`에 checkout한다. `actions/setup-go`는 `local-model-gateway/go.mod`를 `go-version-file`로 사용한다. backend test step에는 `LMG_SOURCE_DIR=${{ github.workspace }}/local-model-gateway`를 설정해 기본 pytest에 포함된 E2E가 같은 workspace의 LMG를 빌드하게 한다. token과 fake provider 설정은 pytest fixture가 임시 값으로 만들며 GitHub secret은 사용하지 않는다.

- [ ] **Step 3: 운영 설정 문서화**

기본 concurrency/queue 값, 429 의미, same-session 409 즉시 거부, storage status, exact-file delete, health endpoint, provider capability, PAG dashboard 상태 의미를 기록한다.

- [ ] **Step 4: 전체 검증**

Run: `cd ../local-model-gateway && gofmt -w internal/config/config.go internal/config/config_test.go internal/limit/manager.go internal/limit/manager_test.go internal/httpapi/router.go internal/httpapi/runs.go internal/httpapi/runs_test.go internal/httpapi/sessions.go internal/httpapi/sessions_test.go internal/session/session.go internal/session/session_test.go internal/health/health.go internal/health/health_test.go internal/models/models.go internal/models/models_test.go cmd/lmg/main.go`

Run: `cd ../local-model-gateway && go test -race ./...`

Expected: PASS

Run: `uv run pytest -q`

Expected: PASS

Run: `uv run ruff check src tests`

Expected: PASS

Run: `cd frontend && npm test && npm run build`

Expected: PASS

Run: `git diff --check && git -C ../local-model-gateway diff --check`

Expected: 출력 없음.

- [ ] **Step 5: CI와 문서 커밋**

```bash
cd ../local-model-gateway
git add .github/workflows/test.yml README.md
git commit -m "ci: verify LMG with pinned Go toolchain"
cd ../personal-agent-gateway
git add .github/workflows/ci.yml README.md
git commit -m "ci: verify PAG against local model gateway"
```

## Acceptance Checklist

- [ ] global/provider cap과 queue bound가 race test에서 지켜진다.
- [ ] 같은 upstream session의 경쟁 resume이 즉시 409 `session_busy`로 거부된다.
- [ ] session list가 filesystem scan 없이 DB metadata만 읽는다.
- [ ] Claude/Codex 삭제가 기록된 정확한 파일 하나만 대상으로 한다.
- [ ] liveness, readiness, provider capability, query transport failure가 구분된다.
- [ ] DB/queue가 정상이어도 사용 가능한 provider가 하나도 없으면 readiness는 503이다.
- [ ] capability report가 provider별 resume/sandbox/permission 지원과 protocol version을 명시한다.
- [ ] PAG dashboard가 빈 session과 LMG 장애를 다르게 표시한다.
- [ ] fake provider 교차 저장소 E2E가 첫 실행, resume/context 변경, 실패, partial timeout/cancel, 멱등 삭제/I/O 재시도, 경로 거부, 동시성/queue 계약을 실제 HTTP/SSE 경계에서 검증한다.
- [ ] Go 1.26.5 기반 race CI와 양쪽 전체 테스트가 통과한다.
