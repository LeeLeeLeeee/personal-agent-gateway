# Login Account Limits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** OTP 로그인 진행 상태와 Dashboard 이동을 보장하고, Codex·Claude 계정 한도를 Dashboard에서 window별로 표시한다.

**Architecture:** LMG는 provider CLI를 직접 실행하는 소유자로서 보호된 `/v1/usage` endpoint에서 rate-limit snapshot을 만든다. Codex는 app-server JSON-RPC를, Claude는 safe-mode `/usage` JSON 결과를 사용한다. PAG는 LMG snapshot을 검증·병합하고, React Dashboard는 provider별 한도 window를 계정 전체 한도와 함께 표시한다.

**Tech Stack:** Go 1.x, SQLite-backed LMG runtime, Python/FastAPI/Pydantic, React/Vite/Vitest, `httpx`.

## Global Constraints

- 계정 웹 화면·쿠키·비공식 HTTP endpoint를 사용하지 않는다.
- Codex는 `account/rateLimits/read` JSON-RPC만 호출하고, Claude는 `--safe-mode -p --output-format json --max-turns 1 /usage`만 호출한다.
- 수집 실패·형식 불일치는 추정하지 않고 provider별 `미수집`으로 표시한다.
- CLI stderr, 토큰, 인증 정보, 원문 오류는 PAG API나 UI에 노출하지 않는다.
- LMG의 모든 protected route와 같이 `/v1/usage`에도 기존 Bearer 인증을 적용한다.
- Dashboard 진입과 수동 새로고침이 수집 시점이며, background polling을 추가하지 않는다.

---

## File Structure

| Repository | File | Responsibility |
| --- | --- | --- |
| LMG | `internal/usage/usage.go` | provider-independent snapshot types and safe collection result helpers |
| LMG | `internal/usage/codex.go` | `codex app-server --stdio` JSON-RPC client and exact response validation |
| LMG | `internal/usage/claude.go` | safe-mode `/usage` invocation and the two primary limit-row parser |
| LMG | `internal/usage/*_test.go` | collector parsing and malformed-response regression coverage |
| LMG | `internal/httpapi/usage.go` | authenticated `GET /v1/usage` handler |
| LMG | `internal/httpapi/usage_test.go`, `router.go`, `cmd/lmg/main.go` | route wiring, bearer coverage, and runtime collector construction |
| PAG | `src/personal_agent_gateway/lmg_client.py` | `/v1/usage` HTTP client and strict wire validator |
| PAG | `src/personal_agent_gateway/local_usage.py` | merge provider catalog with verified LMG limits without inventing values |
| PAG | `src/personal_agent_gateway/api/dashboard.py` | Dashboard usage endpoint integration |
| PAG | `tests/test_lmg_client.py`, `tests/test_local_usage.py`, `tests/api/test_dashboard.py` | protocol, merge, and unauthenticated/failure coverage |
| PAG frontend | `src/hooks/useGatewayBootstrap.js` | login pending state and success boolean |
| PAG frontend | `src/components/containers/GatewayApp/index.jsx` | explicit Dashboard selection after successful login |
| PAG frontend | `src/components/molecules/AuthCard/index.jsx` | pending login copy and disabled controls |
| PAG frontend | `src/components/organisms/DashboardView/index.jsx` | window-level gauges, labels, and no-data state |
| PAG frontend | matching Vitest files | login and displayed limit regression tests |

### Task 1: LMG rate-limit snapshot types and Codex collector

**Files:**
- Create: `local-model-gateway/internal/usage/usage.go`
- Create: `local-model-gateway/internal/usage/codex.go`
- Create: `local-model-gateway/internal/usage/codex_test.go`

**Interfaces:**
- Produces `usage.RateLimit{WindowMinutes int, UsedPercent float64, ResetsAt string}`.
- Produces `usage.ProviderSnapshot{Provider string, Status string, RateLimits []RateLimit}` and `usage.Report{CollectedAt string, Providers []ProviderSnapshot}`.
- Produces `usage.CollectCodex(ctx context.Context, bin string, env []string, now func() time.Time) ProviderSnapshot`.

- [ ] **Step 1: Write the failing Codex JSON-RPC tests**

```go
func TestCollectCodexMapsPrimaryAndSecondaryWindows(t *testing.T) {
    run := fakeAppServer(`{"id": 2, "result": {"rateLimits": {
        "primary": {"usedPercent": 25, "windowDurationMins": 300, "resetsAt": 1780000000},
        "secondary": {"usedPercent": 41.5, "windowDurationMins": 10080, "resetsAt": 1780600000}
    }}}`)
    got := collectCodex(context.Background(), run, fixedNow)
    require.Equal(t, "ok", got.Status)
    require.Equal(t, []RateLimit{{WindowMinutes: 300, UsedPercent: 25}, {WindowMinutes: 10080, UsedPercent: 41.5}}, got.RateLimits)
}

func TestCollectCodexLeavesLimitsEmptyForMalformedResponse(t *testing.T) {
    got := collectCodex(context.Background(), fakeAppServer(`{"id":2,"result":{}}`), fixedNow)
    require.Equal(t, "unconfirmed", got.Status)
    require.Empty(t, got.RateLimits)
}
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `go test ./internal/usage -run 'TestCollectCodex' -count=1`

Expected: FAIL because `internal/usage` and `CollectCodex` do not exist.

- [ ] **Step 3: Implement the minimal snapshot and app-server collector**

```go
type RateLimit struct {
    WindowMinutes int     `json:"window_minutes"`
    UsedPercent   float64 `json:"used_percent"`
    ResetsAt      string  `json:"resets_at"`
}

type ProviderSnapshot struct {
    Provider   string      `json:"provider"`
    Status     string      `json:"status"`
    RateLimits []RateLimit `json:"rate_limits"`
}

// Write initialize request id 1, initialized notification, then id 2.
// Read JSONL until the id 2 result and reject missing/non-numeric fields.
func CollectCodex(ctx context.Context, bin string, env []string, now func() time.Time) ProviderSnapshot
```

Use `exec.CommandContext` with `bin app-server --stdio`; attach only the LMG allowlisted environment plus `CODEX_HOME`. Convert Unix `resetsAt` seconds into RFC3339 UTC. Never put process stderr in `ProviderSnapshot`.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `go test ./internal/usage -run 'TestCollectCodex' -count=1`

Expected: PASS.

- [ ] **Step 5: Commit the isolated collector**

```powershell
git add internal/usage
git commit -m "feat: collect Codex account limits"
```

### Task 2: LMG Claude collector and protected usage route

**Files:**
- Create: `local-model-gateway/internal/usage/claude.go`
- Create: `local-model-gateway/internal/usage/claude_test.go`
- Create: `local-model-gateway/internal/httpapi/usage.go`
- Create: `local-model-gateway/internal/httpapi/usage_test.go`
- Modify: `local-model-gateway/internal/httpapi/router.go`
- Modify: `local-model-gateway/cmd/lmg/main.go`

**Interfaces:**
- Produces `usage.CollectClaude(ctx context.Context, bin string, env []string, now func() time.Time) ProviderSnapshot`.
- Produces `httpapi.UsageHandler(collect func(context.Context) usage.Report) http.HandlerFunc`.
- Consumes the types from Task 1.

- [ ] **Step 1: Write the failing Claude parser and route tests**

```go
func TestCollectClaudeParsesSessionAndAllModelWeek(t *testing.T) {
    output := `{"type":"result","is_error":false,"result":"Current session: 18% used · resets Jul 27, 12:59pm (Asia/Seoul)\nCurrent week (all models): 4% used · resets Aug 2, 5:59pm (Asia/Seoul)"}`
    got := collectClaude(context.Background(), fakeClaude(output), fixedNow)
    require.Equal(t, []int{300, 10080}, windowMinutes(got.RateLimits))
    require.Equal(t, []float64{18, 4}, usedPercents(got.RateLimits))
}

func TestUsageRouteRequiresBearerAndDoesNotExposeCollectorError(t *testing.T) {
    router := NewRouter(Deps{LocalToken: "secret", CollectUsage: failingCollector})
    require.Equal(t, http.StatusUnauthorized, request(router, "", "/v1/usage").Code)
    body := request(router, "Bearer secret", "/v1/usage").Body.String()
    require.NotContains(t, body, "provider stderr secret")
}
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `go test ./internal/usage ./internal/httpapi -run 'TestCollectClaude|TestUsageRoute' -count=1`

Expected: FAIL because the collector, `CollectUsage` dependency, and route do not exist.

- [ ] **Step 3: Implement the minimal Claude collector and route**

```go
// Execute exactly:
// claude --safe-mode -p --output-format json --max-turns 1 /usage
// Parse only result text lines beginning with Current session and
// Current week (all models). Unknown text returns unconfirmed with no limits.
func CollectClaude(ctx context.Context, bin string, env []string, now func() time.Time) ProviderSnapshot

func UsageHandler(collect func(context.Context) usage.Report) http.HandlerFunc {
    return func(w http.ResponseWriter, r *http.Request) {
        if r.Method != http.MethodGet { methodNotAllowed(w, http.MethodGet); return }
        writeJSON(w, http.StatusOK, collect(r.Context()))
    }
}
```

Wire `GET /v1/usage` inside the existing authenticated `/v1/` mux. Build a report with Codex, Claude, and unavailable/unconfirmed snapshots. Derive a RFC3339 time from Claude's date text only when it parses in the named timezone; otherwise retain no reset timestamp and do not guess.

- [ ] **Step 4: Run focused and package tests to verify GREEN**

Run: `go test ./internal/usage ./internal/httpapi ./cmd/lmg -count=1`

Expected: PASS.

- [ ] **Step 5: Commit the route and Claude collector**

```powershell
git add internal/usage internal/httpapi/usage.go internal/httpapi/usage_test.go internal/httpapi/router.go cmd/lmg/main.go
git commit -m "feat: expose local provider account limits"
```

### Task 3: PAG LMG protocol validation and Dashboard report merge

**Files:**
- Modify: `personal-agent-gateway/src/personal_agent_gateway/lmg_client.py`
- Modify: `personal-agent-gateway/src/personal_agent_gateway/local_usage.py`
- Modify: `personal-agent-gateway/src/personal_agent_gateway/api/dashboard.py`
- Modify: `personal-agent-gateway/tests/test_lmg_client.py`
- Modify: `personal-agent-gateway/tests/test_local_usage.py`
- Modify: `personal-agent-gateway/tests/api/test_dashboard.py`

**Interfaces:**
- Produces `fetch_usage(config, *, transport=None) -> LmgQueryResult[dict[str, object]]`.
- Produces `RateLimit(window_minutes: int, used_percent: float, resets_at: str | None)` in the Dashboard response.
- Consumes LMG `GET /v1/usage` from Task 2.

- [ ] **Step 1: Write the failing PAG protocol and merge tests**

```python
def test_fetch_usage_rejects_limit_outside_0_to_100() -> None:
    transport = json_transport({"collected_at": "2026-07-27T00:00:00Z", "providers": [{"provider": "codex", "status": "ok", "rate_limits": [{"window_minutes": 300, "used_percent": 101, "resets_at": "2026-07-27T04:00:00Z"}]}]})
    assert fetch_usage(config, transport=transport).status == "protocol_error"

def test_dashboard_usage_merges_lmg_limits_without_inventing_quota() -> None:
    report = collect_local_agent_usage(registry, reader=lambda _: {}, lmg_reader=lambda: ready_usage)
    codex = next(item for item in report.providers if item.provider == "codex")
    assert codex.rate_limits[0].window_minutes == 300
    assert codex.weekly_limit is None
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `pytest tests/test_lmg_client.py tests/test_local_usage.py tests/api/test_dashboard.py -k 'usage or limit' -q`

Expected: FAIL because `fetch_usage`, `rate_limits`, and `lmg_reader` do not exist.

- [ ] **Step 3: Implement strict client validation and merge**

```python
class RateLimit(BaseModel):
    window_minutes: int
    used_percent: float
    resets_at: str | None = None

def fetch_usage(config: AppConfig, *, transport: httpx.BaseTransport | None = None) -> LmgQueryResult[dict[str, object]]: ...

def collect_local_agent_usage(registry: AgentRegistry, *, lmg_reader: Callable[[], LmgQueryResult[dict[str, object]]] = fetch_usage, ...) -> UsageReport: ...
```

Accept only non-empty provider names, status values `ok`/`unconfirmed`/`unavailable`, positive window minutes, finite percentages from 0 through 100, and RFC3339-or-null reset strings. Preserve catalog availability and leave `rate_limits` empty for every LMG non-ready condition. Call the new reader in `dashboard_usage` with `request.app.state.app_config`.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `pytest tests/test_lmg_client.py tests/test_local_usage.py tests/api/test_dashboard.py -k 'usage or limit' -q`

Expected: PASS.

- [ ] **Step 5: Commit PAG backend integration**

```powershell
git add src/personal_agent_gateway/lmg_client.py src/personal_agent_gateway/local_usage.py src/personal_agent_gateway/api/dashboard.py tests/test_lmg_client.py tests/test_local_usage.py tests/api/test_dashboard.py
git commit -m "feat: show provider account limits in dashboard API"
```

### Task 4: Dashboard account-limit rendering

**Files:**
- Modify: `personal-agent-gateway/frontend/src/components/organisms/DashboardView/index.jsx`
- Modify: `personal-agent-gateway/frontend/src/components/organisms/DashboardView/DashboardView.test.jsx`
- Modify: `personal-agent-gateway/frontend/src/components/references/organisms.md` only if the public props contract changes.

**Interfaces:**
- Consumes provider `{ provider, label, available, rate_limits: [{ window_minutes, used_percent, resets_at }] }` from Task 3.
- Produces one accessible `progressbar` per rate-limit window.

- [ ] **Step 1: Inspect DashboardView and write the component-inspector report before source edits**

Run component-inspector in `api-state,test` mode for `frontend/src/components/organisms/DashboardView/index.jsx`, write the required reviewed report, and confirm its current shared-organism ownership before editing.

- [ ] **Step 2: Write the failing UI test**

```jsx
it("renders each collected account-limit window without calling it local run usage", async () => {
  api.dashboardUsage.mockResolvedValue({ providers: [{ provider: "codex", label: "Codex", available: true, rate_limits: [
    { window_minutes: 300, used_percent: 25, resets_at: "2026-07-27T04:00:00Z" },
    { window_minutes: 10080, used_percent: 41, resets_at: "2026-08-02T09:00:00Z" }
  ] }] });
  render(<DashboardView />);
  expect(await screen.findByRole("progressbar", { name: "Codex 5시간 한도" })).toHaveAttribute("aria-valuenow", "25");
  expect(screen.getByText("계정 전체 한도"))).toBeInTheDocument();
});
```

- [ ] **Step 3: Run the focused test and verify RED**

Run: `npm --prefix frontend test -- DashboardView.test.jsx -t 'renders each collected account-limit window'`

Expected: FAIL because DashboardView only supports legacy weekly fields.

- [ ] **Step 4: Implement the minimal window renderer**

Replace the single weekly gauge branch with a `RateLimitGauge` mapped from `usage.rate_limits`. Derive Korean labels from `window_minutes` (`300` → `5시간`, `10080` → `7일`, otherwise `N분`). Use `used_percent` with `aria-valuemin=0`, `aria-valuemax=100`, and `aria-valuenow`. Retain the existing no-data card only when the array is empty and change copy to `계정 한도를 수집하지 못했습니다.`; do not present it as a local execution count.

- [ ] **Step 5: Run the focused test and verify GREEN**

Run: `npm --prefix frontend test -- DashboardView.test.jsx -t 'renders each collected account-limit window'`

Expected: PASS.

- [ ] **Step 6: Commit Dashboard rendering**

```powershell
git add frontend/src/components/organisms/DashboardView frontend/src/components/references/organisms.md docs/component-inspector
git commit -m "feat: render provider account limit windows"
```

### Task 5: OTP submission state and explicit Dashboard redirect

**Files:**
- Modify: `personal-agent-gateway/frontend/src/hooks/useGatewayBootstrap.js`
- Modify: `personal-agent-gateway/frontend/src/components/containers/GatewayApp/index.jsx`
- Modify: `personal-agent-gateway/frontend/src/components/molecules/AuthCard/index.jsx`
- Modify: `personal-agent-gateway/frontend/src/components/containers/GatewayApp/GatewayApp.test.jsx`

**Interfaces:**
- Produces `authSubmitting: boolean` and `handleLogin(otp): Promise<boolean>` from `useGatewayBootstrap`.
- Consumes `authSubmitting` in `AuthCard` and a GatewayApp wrapper that selects `screen = "dashboard"` only for `true`.

- [ ] **Step 1: Inspect AuthCard and GatewayApp with component-inspector before source edits**

Run component-inspector in `api-state,test` mode for `frontend/src/components/containers/GatewayApp/index.jsx` and `frontend/src/components/molecules/AuthCard/index.jsx`, complete the required reviewed reports, then verify the existing catalog entries are still correct.

- [ ] **Step 2: Write the failing login integration test**

```jsx
it("disables OTP submission while authentication is pending and opens Dashboard on success", async () => {
  const login = deferred();
  installFetch({ "POST /api/auth/login": () => login.promise, /* bootstrap fixtures */ });
  await renderGatewayApp({ openChat: false });
  await userEvent.type(screen.getByPlaceholderText("000000"), "123456");
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  expect(screen.getByRole("button", { name: "Signing in…" })).toBeDisabled();
  login.resolve(jsonResponse({}));
  expect(await screen.findByRole("heading", { name: "대시보드" })).toBeInTheDocument();
});
```

- [ ] **Step 3: Run the focused test and verify RED**

Run: `npm --prefix frontend test -- GatewayApp.test.jsx -t 'disables OTP submission'`

Expected: FAIL because no pending state or explicit redirect exists.

- [ ] **Step 4: Implement the minimal login state flow**

In `handleLogin`, set `authSubmitting` before the API call, reset it in `finally`, return `false` for rejected login or bootstrap failure, and return `true` only after `loadApp` completes. Pass `authSubmitting` to AuthCard; disable its OTP input and login button while true and use `Signing in…` as its button label. In GatewayApp, wrap the hook callback so a `true` result calls `setScreen("dashboard")`; do not change setup/recovery behavior.

- [ ] **Step 5: Run the focused test and verify GREEN**

Run: `npm --prefix frontend test -- GatewayApp.test.jsx -t 'disables OTP submission'`

Expected: PASS.

- [ ] **Step 6: Commit the login UX**

```powershell
git add frontend/src/hooks/useGatewayBootstrap.js frontend/src/components/containers/GatewayApp frontend/src/components/molecules/AuthCard frontend/src/components/containers/GatewayApp/GatewayApp.test.jsx docs/component-inspector
git commit -m "fix: show login progress and open dashboard"
```

### Task 6: Full verification, frontend build, and local smoke test

**Files:**
- No production files expected.

**Interfaces:**
- Verifies Tasks 1–5 across both repositories.

- [ ] **Step 1: Run LMG verification**

Run: `go test ./...`

Expected: PASS from the LMG worktree.

- [ ] **Step 2: Run PAG backend verification**

Run: `pytest -q`

Expected: PASS from the PAG worktree virtual environment.

- [ ] **Step 3: Run PAG frontend verification and rebuild**

Run:

```powershell
npm --prefix frontend test
npm --prefix frontend run build
```

Expected: all Vitest cases pass and Vite emits the production bundle. Existing static-vendor warnings may remain only if no new warning/error is introduced.

- [ ] **Step 4: Restart local LMG and PAG from their implementation worktrees, then smoke-test authenticated usage**

Run the configured local servers and make an authenticated request to LMG `/v1/usage`, then PAG `/api/dashboard/usage`. Verify Codex and Claude rate-limit windows are displayed when their CLIs return values; otherwise verify only `미수집` appears and no raw error is exposed.

- [ ] **Step 5: Commit any test-only corrections and hand off commits separately**

```powershell
git status --short
git log --oneline main..HEAD
```

Expected: PAG and LMG retain separate commit histories; do not touch `playground/.git`.

## Plan Self-Review

- Spec coverage: Tasks 1–2 implement both official collectors and protected LMG API; Task 3 validates and merges data; Task 4 renders the account limit windows; Task 5 implements login behavior; Task 6 rebuilds and smoke-tests the deployed local services.
- Placeholder scan: no TODO/TBD or deferred implementation steps remain.
- Type consistency: LMG `RateLimit` maps to PAG `RateLimit`, then to the frontend `rate_limits` payload; both use `window_minutes`, `used_percent`, and `resets_at`.
