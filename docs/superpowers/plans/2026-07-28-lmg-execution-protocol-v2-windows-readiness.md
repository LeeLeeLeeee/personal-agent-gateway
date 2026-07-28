# LMG Execution Protocol 2.0 and Windows Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make LMG advertise and enforce protocol 2.0 execution capabilities and fail before model invocation when native Windows Codex is not ready.

**Architecture:** Provider capabilities and request-aware preflight become explicit parts of the LMG provider interface. Native Windows Codex environment, home selection, and sandbox probing live in `_windows.go` files; other platforms retain their existing Codex-home behavior.

**Tech Stack:** Go 1.26.5, `net/http`, existing LMG SSE protocol, native Codex CLI.

## Global Constraints

- Protocol `2.0` is a coordinated breaking change; do not add a protocol `1.1` translation layer.
- `run.completed` continues to mean provider-process success, not semantic task success.
- Windows-only behavior must compile through `_windows.go`; non-Windows code must not execute it.
- Do not use `bypassPermissions` to simulate provider capability.
- LMG must reject unsupported execution requirements before `Provider.Run`.
- Do not launch PAG or LMG as a long-running process from a Codex-managed command.

---

### Task 1: Define provider capabilities and request-aware preflight

**Files:**
- Modify: `../local-model-gateway/internal/provider/provider.go`
- Modify: `../local-model-gateway/internal/provider/provider_test.go`
- Modify: `../local-model-gateway/internal/provider/codex/codex.go`
- Modify: `../local-model-gateway/internal/provider/claude/claude.go`
- Modify: `../local-model-gateway/internal/provider/openai/openai.go`
- Modify: provider tests under `../local-model-gateway/internal/provider/**`

**Interfaces:**
- Produces: `provider.Capabilities`
- Produces: `Provider.Capabilities() Capabilities`
- Produces: `Provider.Preflight(context.Context, RunRequest) error`

- [ ] **Step 1: Write the failing provider-contract tests**

Add assertions equivalent to:

```go
func TestCodexCapabilitiesAreExplicit(t *testing.T) {
    got := New(config.Config{CodexBin: "codex"}, nil).Capabilities()
    if got.ExternalReadOnlyRoots || !slices.Contains(got.NetworkModes, "required") {
        t.Fatalf("capabilities = %+v", got)
    }
}
```

Also assert:

- Claude supports only `network="unspecified"` in this change.
- OpenAI supports `network="required"`.
- every provider reports resume and the supported sandbox/permission modes.
- test stubs accept the new request argument in `Preflight`.

- [ ] **Step 2: Run provider tests and confirm the interface failure**

Run:

```powershell
Set-Location ..\local-model-gateway
go test ./internal/provider/...
```

Expected: FAIL because `Capabilities` and request-aware `Preflight` do not exist.

- [ ] **Step 3: Add the exact capability types**

Add to `internal/provider/provider.go`:

```go
type Capabilities struct {
    Resume                bool     `json:"resume"`
    ExternalReadOnlyRoots bool     `json:"external_read_only_roots"`
    NetworkModes          []string `json:"network_modes"`
    SandboxModes          []string `json:"sandbox_modes,omitempty"`
    PermissionModes       []string `json:"permission_modes,omitempty"`
}

type Provider interface {
    Name() string
    Capabilities() Capabilities
    Preflight(ctx context.Context, req RunRequest) error
    Run(ctx context.Context, req RunRequest, emit Emit) (RunResult, error)
}
```

Use these initial truthful values:

```go
// Codex
Capabilities{
    Resume: true,
    ExternalReadOnlyRoots: false,
    NetworkModes: []string{"unspecified", "denied", "required"},
    SandboxModes: []string{"read-only", "workspace-write", "danger-full-access"},
}

// Claude
Capabilities{
    Resume: true,
    ExternalReadOnlyRoots: false,
    NetworkModes: []string{"unspecified"},
    PermissionModes: []string{"default", "acceptEdits", "plan"},
}

// OpenAI
Capabilities{
    Resume: false,
    ExternalReadOnlyRoots: true,
    NetworkModes: []string{"required"},
}
```

Do not claim controlled Claude web access until a separate provider test proves
the exact CLI flags.

- [ ] **Step 4: Update all implementations and stubs**

Change every `Preflight(context.Context)` implementation and test stub to
`Preflight(context.Context, provider.RunRequest)`. Binary and API-key checks
remain unchanged in this task.

- [ ] **Step 5: Run provider tests**

Run:

```powershell
go test ./internal/provider/...
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add internal/provider
git commit -m "feat: define provider execution capabilities"
```

### Task 2: Enforce execution capabilities and network modes

**Files:**
- Modify: `../local-model-gateway/internal/provider/provider.go`
- Create: `../local-model-gateway/internal/execution/capabilities.go`
- Create: `../local-model-gateway/internal/execution/capabilities_test.go`
- Modify: `../local-model-gateway/internal/execution/validate.go`
- Modify: `../local-model-gateway/internal/httpapi/runs.go`
- Modify: `../local-model-gateway/internal/httpapi/runs_test.go`
- Modify: `../local-model-gateway/internal/provider/codex/command.go`
- Modify: `../local-model-gateway/internal/provider/codex/command_test.go`

**Interfaces:**
- Produces: `Execution.Network string`
- Produces: `execution.ValidateCapabilities(provider.Execution, provider.Capabilities) error`
- Consumes: `Provider.Capabilities()`

- [ ] **Step 1: Add failing capability-validation tests**

Cover these exact cases:

```go
func TestValidateCapabilitiesRejectsUnsupportedNetwork(t *testing.T) {
    err := ValidateCapabilities(
        provider.Execution{Network: "required"},
        provider.Capabilities{NetworkModes: []string{"unspecified"}},
    )
    if err == nil || !strings.Contains(err.Error(), "network") {
        t.Fatalf("error = %v", err)
    }
}
```

Also assert invalid network strings, unsupported sandbox modes, unsupported
permission modes, and external CLI read roots are rejected before the recording
provider's `Run` counter increments.

- [ ] **Step 2: Confirm failures**

Run:

```powershell
go test ./internal/execution ./internal/httpapi
```

Expected: FAIL because `Network` and capability validation are absent.

- [ ] **Step 3: Implement the validator**

Add `Network string \`json:"network,omitempty"\`` to `provider.Execution`.
`ValidateCapabilities` accepts only `unspecified`, `denied`, or `required` and
requires the selected value to occur in `Capabilities.NetworkModes`.

Keep path canonicalization in `execution.Validate`. Call capability validation
after path validation and before admission/provider preflight in
`RunsHandler`.

Remove the `providerName == "codex" || providerName == "claude"` branch and the
`providerName` parameter from `execution.Validate`; its signature becomes
`Validate(input provider.Execution, allowedRoots []string)`. That function must
only canonicalize paths and enforce the configured allowed-root boundary. After
canonicalization,
`ValidateCapabilities` rejects a read root outside `WorkspaceRoot` exactly
when `Capabilities.ExternalReadOnlyRoots` is false. This makes the advertised
capability, rather than a provider-name list, the single source of truth.

Map capability validation to:

```json
{"code":"unsupported_execution_capability"}
```

with HTTP 422.

- [ ] **Step 4: Translate Codex network modes**

In `baseConfigArgs`, append exactly one config override when the request is
explicit:

```go
case "required":
    args = append(args, "-c", `sandbox_workspace_write.network_access=true`)
case "denied":
    args = append(args, "-c", `sandbox_workspace_write.network_access=false`)
```

`unspecified` appends nothing. Add start and resume command tests for both
explicit values.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
go test ./internal/execution ./internal/httpapi ./internal/provider/codex
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add internal/execution internal/httpapi/runs.go internal/httpapi/runs_test.go internal/provider/provider.go internal/provider/codex
git commit -m "feat: enforce provider execution capabilities"
```

### Task 3: Publish protocol 2.0 capability and readiness data

**Files:**
- Modify: `../local-model-gateway/internal/models/models.go`
- Modify: `../local-model-gateway/internal/httpapi/models.go`
- Modify: `../local-model-gateway/internal/httpapi/models_test.go`
- Modify: `../local-model-gateway/internal/httpapi/router.go`
- Modify: `../local-model-gateway/internal/httpapi/runs_test.go`

**Interfaces:**
- Produces: `models.Provider.Execution provider.Capabilities`
- Produces: provider readiness fields `ready` and `readiness_error`

- [ ] **Step 1: Write failing response-contract tests**

Assert `/v1/models` includes:

```json
{
  "protocol_version": "2.0",
  "providers": {
    "codex": {
      "ready": true,
      "execution": {
        "resume": true,
        "external_read_only_roots": false,
        "network_modes": ["unspecified", "denied", "required"]
      }
    }
  }
}
```

When preflight fails, assert `ready:false` and
`readiness_error:"provider_not_ready"` without the internal error text.

- [ ] **Step 2: Run tests and confirm failure**

Run:

```powershell
go test ./internal/httpapi ./internal/models
```

Expected: FAIL on protocol `1.1` and missing fields.

- [ ] **Step 3: Extend the report**

Add to `models.Provider`:

```go
Ready          bool                  `json:"ready"`
ReadinessError string                `json:"readiness_error,omitempty"`
Execution      provider.Capabilities `json:"execution"`
```

Change `ModelsHandler` to merge detector data with registered provider
capabilities and request-free preflight:

```go
err := p.Preflight(ctx, provider.RunRequest{})
```

Advertise `ProtocolVersion = "2.0"`. Redact all preflight causes to the stable
public code.

- [ ] **Step 4: Make `/readyz` use the same readiness source**

Remove duplicate readiness interpretation. Both `/readyz` and `/v1/models`
must call the same helper and report not ready if no registered provider is
ready or admission capacity is unavailable.

- [ ] **Step 5: Run tests**

Run:

```powershell
go test ./internal/httpapi ./internal/models
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add internal/models internal/httpapi
git commit -m "feat: publish LMG protocol 2 readiness"
```

### Task 4: Make the child environment platform-aware

**Files:**
- Modify: `../local-model-gateway/internal/proc/env.go`
- Create: `../local-model-gateway/internal/proc/env_windows.go`
- Create: `../local-model-gateway/internal/proc/env_other.go`
- Modify: `../local-model-gateway/internal/proc/env_test.go`
- Create: `../local-model-gateway/internal/proc/env_windows_test.go`

**Interfaces:**
- Produces: `platformEnvironmentKeys() map[string]bool`
- Preserves: credentialed proxy filtering

- [ ] **Step 1: Write the failing Windows environment test**

In `env_windows_test.go`:

```go
func TestAllowlistedEnvironmentPreservesSystemDrive(t *testing.T) {
    got := AllowlistedEnvironment([]string{
        `SYSTEMDRIVE=C:`,
        `SYSTEMROOT=C:\Windows`,
        `LMG_LOCAL_TOKEN=secret`,
    })
    if !slices.Contains(got, `SYSTEMDRIVE=C:`) {
        t.Fatalf("SYSTEMDRIVE missing: %v", got)
    }
    if slices.Contains(got, `LMG_LOCAL_TOKEN=secret`) {
        t.Fatalf("gateway secret leaked: %v", got)
    }
}
```

- [ ] **Step 2: Confirm failure on Windows**

Run:

```powershell
go test ./internal/proc
```

Expected: FAIL because `SYSTEMDRIVE` is dropped.

- [ ] **Step 3: Split platform keys**

Keep common safe keys in `env.go`. Return
`map[string]bool{"SYSTEMDRIVE": true}` from `env_windows.go` and an empty map
from `env_other.go`. Merge the maps case-insensitively inside
`AllowlistedEnvironment`.

- [ ] **Step 4: Run tests**

Run:

```powershell
go test ./internal/proc
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add internal/proc
git commit -m "fix: preserve Windows Codex process environment"
```

### Task 5: Use the user's Codex home only on Windows

**Files:**
- Modify: `../local-model-gateway/internal/config/config.go`
- Create: `../local-model-gateway/internal/config/codexhome_windows.go`
- Create: `../local-model-gateway/internal/config/codexhome_other.go`
- Modify: `../local-model-gateway/internal/config/config_test.go`
- Modify: `../local-model-gateway/internal/provider/codex/codex.go`
- Modify: `../local-model-gateway/internal/provider/codex/codex_test.go`
- Modify: `../local-model-gateway/cmd/lmg/main.go`

**Interfaces:**
- Produces: `config.CodexHome(Config) string` with platform-specific ownership

- [ ] **Step 1: Add platform-specific failing tests**

On Windows, assert:

```go
want := filepath.Join(mustUserHome(t), ".codex")
if got := CodexHome(cfg); got != want {
    t.Fatalf("CodexHome = %q, want %q", got, want)
}
```

In a non-Windows test file, assert the existing
`filepath.Join(cfg.DataDir, "codex-home")`.

In Codex provider tests, assert the Windows child env does not contain an
LMG-owned `CODEX_HOME=` override.

- [ ] **Step 2: Run config and Codex tests**

Run:

```powershell
go test ./internal/config ./internal/provider/codex
```

Expected: FAIL on the existing always-LMG-owned home.

- [ ] **Step 3: Move `CodexHome` into platform files**

`codexhome_windows.go` returns the current user's `.codex`; if
`os.UserHomeDir()` fails, return an error through a new
`ResolveCodexHome(Config) (string, error)` used at startup.

`codexhome_other.go` preserves the LMG-owned data directory. Keep
`CodexHome(Config) string` only as a tested wrapper after startup validation.

- [ ] **Step 4: Stop overriding the Windows child environment**

Move the `CODEX_HOME=` append behind `codexHomeEnvironment(cfg)`:

- Windows returns no override.
- Other platforms return `CODEX_HOME=` plus
  `filepath.Join(cfg.DataDir, "codex-home")`.

In `cmd/lmg/main.go`, seed Codex auth only when source and destination homes
differ. This preserves non-Windows behavior and eliminates the Windows copy.

- [ ] **Step 5: Run tests**

Run:

```powershell
go test ./internal/config ./internal/provider/codex ./cmd/lmg
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add internal/config internal/provider/codex cmd/lmg/main.go
git commit -m "fix: use Windows user Codex state"
```

### Task 6: Add native Windows Codex sandbox readiness

**Files:**
- Create: `../local-model-gateway/internal/provider/codex/readiness.go`
- Create: `../local-model-gateway/internal/provider/codex/readiness_windows.go`
- Create: `../local-model-gateway/internal/provider/codex/readiness_other.go`
- Create: `../local-model-gateway/internal/provider/codex/readiness_windows_test.go`
- Create: `../local-model-gateway/internal/provider/codex/readiness_other_test.go`
- Modify: `../local-model-gateway/internal/provider/codex/codex.go`
- Modify: `../local-model-gateway/internal/provider/provider.go`
- Modify: `../local-model-gateway/internal/run/failure.go`
- Modify: `../local-model-gateway/internal/httpapi/runs.go`
- Modify: `../local-model-gateway/internal/httpapi/runs_test.go`

**Interfaces:**
- Produces: `codexReadiness.Check(context.Context) error`
- Produces: `provider.ErrorNotReady`
- Produces: public `provider_not_ready`

- [ ] **Step 1: Write readiness tests**

Use an injected probe function and assert:

- 20 concurrent `Check` calls invoke the probe exactly once.
- a probe error is returned to every caller.
- the non-Windows implementation invokes no sandbox command.
- provider preflight checks binary availability before platform readiness.
- `RunsHandler` returns HTTP 503 `provider_not_ready` before `Run`.

- [ ] **Step 2: Run focused tests and confirm failure**

Run:

```powershell
go test ./internal/provider/codex ./internal/httpapi ./internal/run
```

Expected: FAIL because readiness and `ErrorNotReady` do not exist.

- [ ] **Step 3: Implement a process-lifetime single-flight probe**

Use `sync.Once` because the approved design caches readiness for one LMG
process:

```go
type codexReadiness struct {
    once  sync.Once
    probe func(context.Context) error
    err   error
}

func (r *codexReadiness) Check(_ context.Context) error {
    r.once.Do(func() {
        probeCtx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
        defer cancel()
        r.err = r.probe(probeCtx)
    })
    return r.err
}
```

Do not cache cancellation from the first HTTP caller as process readiness; the
probe has its own bounded process-lifetime context as shown above. Keep `ctx`
in the interface for provider consistency and future shutdown wiring.

The Windows probe runs, through the existing process runner and safe child env:

```text
codex sandbox -- cmd.exe /d /c exit 0
```

It uses an existing absolute LMG data directory as its working directory and a
finite 30-second timeout. The `windows` word is not a subcommand in Codex CLI
0.145.0; a static test locks the exact argv above. The non-Windows probe
returns nil.

- [ ] **Step 4: Classify readiness failures**

Add `ErrorNotReady` and map it to `provider_not_ready`. Do not expose sandbox
logs or the internal helper error in HTTP/SSE output; keep the cause in server
logs.

- [ ] **Step 5: Run the LMG suite**

Run:

```powershell
go test ./...
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add internal/provider internal/run internal/httpapi
git commit -m "feat: gate Windows Codex on sandbox readiness"
```

### Task 7: Document and statically verify the LMG boundary

**Files:**
- Modify: `../local-model-gateway/README.md`
- Modify: `../local-model-gateway/AGENTS.md`

**Interfaces:**
- Consumes: all interfaces from Tasks 1–6

- [ ] **Step 1: Update operational documentation**

Document:

- protocol `2.0`;
- exact readiness codes;
- Windows user `CODEX_HOME`;
- `SYSTEMDRIVE` preservation;
- native sandbox canary;
- Claude `network=required` rejection in this release;
- the normal-PowerShell launch rule.

- [ ] **Step 2: Run final static verification**

Run:

```powershell
gofmt -w (Get-ChildItem -Path internal,cmd -Recurse -Filter *.go | ForEach-Object { $_.FullName })
go test ./...
go vet ./...
git diff --check
```

Expected: all commands exit 0; `git diff --check` prints nothing.

- [ ] **Step 3: Commit**

```powershell
git add README.md AGENTS.md
git commit -m "docs: describe LMG execution protocol 2"
```
