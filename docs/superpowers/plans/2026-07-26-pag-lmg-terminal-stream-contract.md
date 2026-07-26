# PAG-LMG Terminal Stream Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 부분 출력 뒤 provider 오류가 성공으로 바뀌거나 terminal event 없이 끝난 스트림이 빈 성공으로 처리되는 문제를 제거하고, 완료·실패·중단을 일관되게 전달한다.

**Architecture:** LMG provider는 부분 결과와 오류를 함께 반환할 수 있고 오류를 숨기지 않는다. run envelope는 `run.completed`, `run.failed`, `run.aborted` 중 정확히 하나를 내보낸다. PAG는 SSE를 끝까지 검증하고 terminal event가 없거나 중복되거나 잘못된 JSON이면 protocol failure로 처리한다.

**Tech Stack:** Python 3.11, httpx streaming, pytest-asyncio, Go 1.26.5, `context`, JSONL/SSE

## Global Constraints

- 선행 조건은 `2026-07-26-pag-lmg-local-interface-hardening.md` 구현 완료다.
- 성공 terminal은 `run.completed` 하나뿐이다.
- timeout·idle timeout·호출자 취소는 `run.aborted`, provider/프로토콜 오류는 `run.failed`다.
- 오류 terminal은 `error_code`, `error`, `partial_content`, `upstream_session_id`를 보존한다.
- 출력이 한 글자라도 있었다는 이유로 runner 오류를 성공으로 바꾸지 않는다.
- PAG는 EOF 전에 terminal을 하나 받아야 하며, 첫 terminal 뒤에도 EOF까지 읽어 중복 terminal을 검출한다.
- stable error code는 `provider_unavailable`, `provider_protocol_error`, `provider_process_failed`, `run_timeout`, `run_cancelled`, `upstream_stream_incomplete` 중 하나를 사용한다. idle timeout은 메시지로 원인을 구분하되 code는 `run_timeout`이다.
- 배포는 PAG의 Task 4~5 parser/runtime를 먼저 배포한 뒤 LMG의 Task 1~3 event 변경을 배포한다. 이 순서를 지켜 구버전 PAG가 새 terminal을 성공으로 오해하는 구간을 만들지 않는다.
- 이 계획은 인증, 세션 context fingerprint, 삭제, 동시성 제한을 수정하지 않는다.

---

## File Structure

### local-model-gateway

- Modify: `../local-model-gateway/internal/event/event.go`
- Modify: `../local-model-gateway/internal/event/event_test.go`
- Modify: `../local-model-gateway/internal/provider/provider.go`
- Modify: `../local-model-gateway/internal/proc/proc.go`
- Modify: `../local-model-gateway/internal/proc/proc_test.go`
- Create: `../local-model-gateway/internal/run/failure.go`
- Modify: `../local-model-gateway/internal/run/run.go`
- Modify: `../local-model-gateway/internal/run/run_test.go`
- Modify: `../local-model-gateway/internal/provider/codex/codex.go`
- Modify: `../local-model-gateway/internal/provider/codex/codex_test.go`
- Modify: `../local-model-gateway/internal/provider/codex/normalize.go`
- Modify: `../local-model-gateway/internal/provider/codex/normalize_test.go`
- Modify: `../local-model-gateway/internal/provider/claude/claude.go`
- Modify: `../local-model-gateway/internal/provider/claude/claude_test.go`
- Modify: `../local-model-gateway/internal/provider/claude/normalize.go`
- Modify: `../local-model-gateway/internal/provider/claude/normalize_test.go`
- Modify: `../local-model-gateway/internal/provider/openai/openai.go`
- Modify: `../local-model-gateway/internal/provider/openai/openai_test.go`
- Modify: `../local-model-gateway/internal/provider/provider_test.go`
- Modify: `../local-model-gateway/internal/httpapi/runs.go`
- Modify: `../local-model-gateway/internal/httpapi/runs_test.go`
- Modify: `../local-model-gateway/README.md`

### personal-agent-gateway

- Modify: `src/personal_agent_gateway/remote_model_client.py`
- Modify: `src/personal_agent_gateway/runtime.py`
- Modify: `src/personal_agent_gateway/api/chat_sessions.py`
- Modify: `tests/test_remote_model_client.py`
- Modify: `tests/test_runtime.py`
- Modify: `tests/test_app.py`
- Modify: `README.md`

## Task 1: Terminal event 스키마와 timeout 분류

**Files:**
- Modify: `../local-model-gateway/internal/provider/provider.go`
- Modify: `../local-model-gateway/internal/provider/provider_test.go`
- Modify: `../local-model-gateway/internal/event/event.go`
- Modify: `../local-model-gateway/internal/event/event_test.go`
- Modify: `../local-model-gateway/internal/proc/proc.go`
- Modify: `../local-model-gateway/internal/proc/proc_test.go`
- Create: `../local-model-gateway/internal/run/failure.go`
- Modify: `../local-model-gateway/internal/run/run_test.go`

- [ ] **Step 1: event JSON 계약 테스트 작성**

`run.aborted` kind와 다음 필드가 JSON에 직렬화되는지 검사한다.

```go
ErrorCode     string `json:"error_code,omitempty"`
PartialContent string `json:"partial_content,omitempty"`
```

- [ ] **Step 2: idle timeout 구분 테스트 작성**

`proc.Run`의 idle timer 만료가 `context.DeadlineExceeded`가 아니라 `proc.ErrIdleTimeout`을 반환하는 테스트를 추가한다. 상위 context deadline은 계속 `context.DeadlineExceeded`, 명시 취소는 `context.Canceled`여야 한다.

- [ ] **Step 3: 실패 확인**

Run: `cd ../local-model-gateway && go test ./internal/provider ./internal/event ./internal/proc ./internal/run`

Expected: 새 kind, 필드, `ErrIdleTimeout`이 없어 실패한다.

- [ ] **Step 4: 스키마와 오류 분류 구현**

`event.Event`에 필드를 추가하고 `proc`에 sentinel을 정의한다.

```go
var ErrIdleTimeout = errors.New("process idle timeout")
```

`run/failure.go`에 다음을 구현한다.

```go
func Classify(err error) (event.Kind, string)
```

`provider` package에 원인을 보존하는 typed error를 추가한다.

```go
type ErrorKind string

const (
    ErrorUnavailable ErrorKind = "unavailable"
    ErrorProtocol    ErrorKind = "protocol"
    ErrorProcess     ErrorKind = "process"
)

type RunError struct {
    Kind ErrorKind
    Err  error
}
```

`RunError`는 `Error()`와 `Unwrap()`을 구현한다. 분류표는 다음으로 고정한다.

| 오류 | kind | stable code |
|---|---|---|
| `context.Canceled` | `run.aborted` | `run_cancelled` |
| `context.DeadlineExceeded` | `run.aborted` | `run_timeout` |
| `proc.ErrIdleTimeout` | `run.aborted` | `run_timeout` |
| `provider.ErrorUnavailable` | `run.failed` | `provider_unavailable` |
| `provider.ErrorProtocol` | `run.failed` | `provider_protocol_error` |
| `provider.ErrorProcess` 또는 그 외 | `run.failed` | `provider_process_failed` |

- [ ] **Step 5: 단위 테스트 통과 확인**

Run: `cd ../local-model-gateway && go test ./internal/provider ./internal/event ./internal/proc ./internal/run`

Expected: PASS

- [ ] **Step 6: 커밋**

```bash
cd ../local-model-gateway
git add internal/provider/provider.go internal/provider/provider_test.go internal/event/event.go internal/event/event_test.go internal/proc/proc.go internal/proc/proc_test.go internal/run/failure.go internal/run/run_test.go
git commit -m "feat: define LMG terminal failure semantics"
```

## Task 2: Provider의 부분 결과와 오류 보존

**Files:**
- Modify: `../local-model-gateway/internal/provider/provider.go`
- Modify: `../local-model-gateway/internal/provider/codex/codex.go`
- Modify: `../local-model-gateway/internal/provider/codex/codex_test.go`
- Modify: `../local-model-gateway/internal/provider/codex/normalize.go`
- Modify: `../local-model-gateway/internal/provider/codex/normalize_test.go`
- Modify: `../local-model-gateway/internal/provider/claude/claude.go`
- Modify: `../local-model-gateway/internal/provider/claude/claude_test.go`
- Modify: `../local-model-gateway/internal/provider/claude/normalize.go`
- Modify: `../local-model-gateway/internal/provider/claude/normalize_test.go`
- Modify: `../local-model-gateway/internal/provider/openai/openai.go`
- Modify: `../local-model-gateway/internal/provider/openai/openai_test.go`

- [ ] **Step 1: Codex/Claude 회귀 테스트 작성**

각 provider에서 runner가 일부 content와 upstream session ID를 emit한 뒤 오류를 반환하는 fixture를 만든다. 기대값은 `RunResult{Content: partial, UpstreamSessionID: id}`와 non-nil error가 동시에 반환되는 것이다.

Codex/Claude normalizer에는 다음 사례를 추가한다.

- 잘린 JSONL과 JSON이 아닌 line은 `provider.ErrorProtocol`
- 알려진 event type인데 필수 payload가 잘못된 경우 `provider.ErrorProtocol`
- 유효하지만 지원하지 않는 event type은 하위 호환을 위해 event 없이 nil
- 정상 content/session event는 기존 event와 nil

OpenAI에는 API key가 없으면 `provider.ErrorUnavailable`, HTTP/JSON decode 실패는 각각 process/protocol typed error가 되는 테스트를 추가한다.

- [ ] **Step 2: 회귀 테스트 실패 확인**

Run: `cd ../local-model-gateway && go test ./internal/provider/...`

Expected: 현재 구현이 content가 있으면 오류를 숨기거나 오류 때 zero result를 반환해서 실패한다.

- [ ] **Step 3: 최소 provider 수정**

Codex와 Claude의 runner 오류 분기에서 누적한 `RunResult`를 반환하고 원래 오류를 유지한다.

```go
if runnerErr != nil {
    return result, runnerErr
}
if resultErr != nil {
    return result, resultErr
}
return result, nil
```

기존 parser와 정상 성공 경로는 변경하지 않는다.

두 normalizer의 시그니처는 `func normalizeLine(line string) ([]event.Event, error)`로 바꾸고 JSON decode나 알려진 event payload 검증 실패를 `provider.RunError{Kind: provider.ErrorProtocol, Err: err}`로 감싼다. provider loop는 normalize 오류를 무시하지 않고 즉시 반환한다. subprocess start/exit 오류는 `provider.ErrorUnavailable`/`provider.ErrorProcess`, OpenAI 응답 JSON decode 오류는 `provider.ErrorProtocol`로 감싼다.

- [ ] **Step 4: provider 테스트 통과 확인**

Run: `cd ../local-model-gateway && go test ./internal/provider/...`

Expected: PASS

- [ ] **Step 5: 커밋**

```bash
cd ../local-model-gateway
git add internal/provider/provider.go internal/provider/codex/codex.go internal/provider/codex/codex_test.go internal/provider/codex/normalize.go internal/provider/codex/normalize_test.go internal/provider/claude/claude.go internal/provider/claude/claude_test.go internal/provider/claude/normalize.go internal/provider/claude/normalize_test.go internal/provider/openai/openai.go internal/provider/openai/openai_test.go
git commit -m "fix: preserve provider errors after partial output"
```

## Task 3: 정확히 하나의 LMG terminal event

**Files:**
- Modify: `../local-model-gateway/internal/provider/provider.go`
- Modify: `../local-model-gateway/internal/provider/provider_test.go`
- Modify: `../local-model-gateway/internal/run/run.go`
- Modify: `../local-model-gateway/internal/run/run_test.go`
- Modify: `../local-model-gateway/internal/httpapi/runs.go`
- Modify: `../local-model-gateway/internal/httpapi/runs_test.go`
- Modify: `../local-model-gateway/internal/provider/codex/codex.go`
- Modify: `../local-model-gateway/internal/provider/codex/codex_test.go`
- Modify: `../local-model-gateway/internal/provider/claude/claude.go`
- Modify: `../local-model-gateway/internal/provider/claude/claude_test.go`
- Modify: `../local-model-gateway/internal/provider/openai/openai.go`
- Modify: `../local-model-gateway/internal/provider/openai/openai_test.go`

- [ ] **Step 1: run envelope 테스트 작성**

table test로 정상, provider 오류, timeout, idle timeout, 취소를 실행해 terminal kind가 정확히 하나인지 검사한다. 오류 사례에서는 `PartialContent`와 `UpstreamSessionID`가 보존되는지도 확인한다.

provider typed error별 stable code를 검사하고, terminal을 emit한 뒤 provider가 추가 event를 emit해도 무시되는지 검사한다. 실행 전에 선택한 provider가 없거나 명백히 구성되지 않은 경우에는 SSE header를 쓰기 전 HTTP 503과 `{"code":"provider_unavailable"}`를 반환하는 handler 테스트를 추가한다. CLI provider의 binary lookup 실패와 OpenAI API key 누락도 같은 preflight 실패로 검사한다.

- [ ] **Step 2: SSE writer 오류 테스트 작성**

쓰기 실패를 주입할 수 있는 emitter를 사용해 첫 write 실패 뒤 추가 event를 기록하지 않는지 검사한다.

- [ ] **Step 3: 실패 확인**

Run: `cd ../local-model-gateway && go test ./internal/run ./internal/httpapi`

Expected: aborted가 없고 emit 오류를 전달할 수 없어 실패한다.

- [ ] **Step 4: emitter 오류 계약 구현**

`provider.Emit`과 process line callback을 다음으로 변경하고 모든 provider emit 호출이 오류를 상위로 전달하게 한다.

```go
type Emit func(event.Event) error

type Provider interface {
    Name() string
    Preflight(context.Context) error
    Run(context.Context, RunRequest, Emit) (RunResult, error)
}

type Options struct {
    OnLine func(string) error
}
```

`run.Execute`는 아래 시그니처로 바꾸고 emit 실패 즉시 반환한다.

```go
func Execute(
    ctx context.Context,
    p provider.Provider,
    req provider.RunRequest,
    runID string,
    emit provider.Emit,
) error
```

provider 결과 오류는 `Classify`하여 `run.failed` 또는 `run.aborted` 하나를 emit한다. `RunResult.Content`는 `partial_content`로, session ID는 terminal에도 넣는다. terminal이 한 번 결정된 뒤 들어오는 provider event는 전달하지 않는다. `proc.Run`은 `OnLine` 오류를 받으면 process tree를 종료하고 bounded wait 후 그 오류를 반환한다. Codex, Claude, OpenAI provider와 provider test fake가 새 callback 계약을 사용하게 수정한다.

- [ ] **Step 5: HTTP SSE emitter 구현**

`runs.go`의 emitter는 각 `Write` 반환 오류를 확인하고 첫 오류를 저장해 반환한다. flush 뒤 request context가 취소되면 provider가 종료되도록 `context.WithCancel`을 사용한다. JSON marshal 오류도 무시하지 않는다. provider lookup 실패와 `Provider.Preflight()` 오류는 SSE header 작성 전 503 `provider_unavailable` JSON 오류로 종료한다. Codex/Claude preflight는 configured binary의 `exec.LookPath`, OpenAI preflight는 nonblank API key를 검사하며 network 호출은 하지 않는다.

- [ ] **Step 6: terminal 테스트 통과 확인**

Run: `cd ../local-model-gateway && go test ./internal/run ./internal/httpapi ./internal/provider/...`

Expected: PASS

- [ ] **Step 7: 커밋**

```bash
cd ../local-model-gateway
git add internal/provider/provider.go internal/provider/provider_test.go internal/provider/codex/codex.go internal/provider/codex/codex_test.go internal/provider/codex/normalize.go internal/provider/codex/normalize_test.go internal/provider/claude/claude.go internal/provider/claude/claude_test.go internal/provider/claude/normalize.go internal/provider/claude/normalize_test.go internal/provider/openai/openai.go internal/provider/openai/openai_test.go internal/proc/proc.go internal/proc/proc_test.go internal/run/run.go internal/run/run_test.go internal/httpapi/runs.go internal/httpapi/runs_test.go
git commit -m "feat: enforce one terminal event per LMG run"
```

## Task 4: PAG의 엄격한 SSE terminal parser

**Files:**
- Modify: `src/personal_agent_gateway/remote_model_client.py`
- Modify: `tests/test_remote_model_client.py`

- [ ] **Step 1: 실패하는 protocol 테스트 작성**

다음 사례를 각각 테스트한다.

- terminal 없이 EOF: `RemoteRunProtocolError`와 `upstream_stream_incomplete`
- malformed `data:` JSON: `RemoteRunProtocolError`와 `provider_protocol_error`
- JSON object가 아닌 event: `provider_protocol_error`
- terminal 두 개: `provider_protocol_error`
- `run.failed`: `RemoteRunFailedError`, code/error/partial/session 보존
- `run.aborted`: `RemoteRunAbortedError`, code/error/partial/session 보존
- 정상 `run.completed` 뒤 EOF: `ModelResponse`
- `session.updated`는 terminal 전에 `on_event`로 즉시 전달

- [ ] **Step 2: parser 테스트 실패 확인**

Run: `uv run pytest tests/test_remote_model_client.py -q`

Expected: 현재 parser가 잘못된 줄을 무시하고 EOF를 빈 성공으로 반환해서 실패한다.

- [ ] **Step 3: typed exception 구현**

같은 파일에 다음 계층을 추가한다.

```python
class RemoteRunError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        partial_content: str = "",
        upstream_session_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.partial_content = partial_content
        self.upstream_session_id = upstream_session_id

class RemoteRunFailedError(RemoteRunError):
    pass

class RemoteRunAbortedError(RemoteRunError):
    pass

class RemoteRunProtocolError(RemoteRunError):
    pass
```

- [ ] **Step 4: EOF까지 검증하는 state machine 구현**

`complete()`에서 terminal event를 저장하고 EOF까지 읽는다. terminal 전에 malformed event가 오거나 terminal이 두 번 오면 `provider_protocol_error`다. EOF에서 terminal이 없으면 `upstream_stream_incomplete`, completed면 response 반환, failed/aborted면 대응 exception을 raise한다. 상세 원인(`invalid_event_json`, `invalid_event_shape`, `duplicate_terminal`, `missing_terminal`)은 exception의 diagnostic message에만 남긴다. non-terminal event는 기존대로 `on_event`에 전달한다.

- [ ] **Step 5: parser 테스트 통과 확인**

Run: `uv run pytest tests/test_remote_model_client.py -q`

Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add src/personal_agent_gateway/remote_model_client.py tests/test_remote_model_client.py
git commit -m "fix: reject incomplete LMG event streams"
```

## Task 5: PAG runtime 종료 상태 노출

**Files:**
- Modify: `src/personal_agent_gateway/runtime.py`
- Modify: `src/personal_agent_gateway/api/chat_sessions.py`
- Modify: `tests/test_runtime.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: runtime 결과 테스트 작성**

`RuntimeResult`가 `termination`을 가지며 다음으로 매핑되는지 검사한다.

| 원인 | termination |
|---|---|
| 정상 완료 | `completed` |
| `run_cancelled` | `cancelled` |
| `run_timeout` | `timed_out` |
| provider/protocol failure | `failed` |

오류 결과에도 기존 사용자용 redacted `Error:` message와 transcript `runtime_error`가 남아야 한다.

- [ ] **Step 2: API 응답 테스트 작성**

`_runtime_response`와 session chat endpoint가 `termination`을 반환하고 `_runtime_audit_status`는 completed만 success로 기록하는지 검사한다. `_compat_chat_response`에도 같은 필드를 포함한다.

- [ ] **Step 3: 실패 확인**

Run: `uv run pytest tests/test_runtime.py tests/test_app.py -q`

Expected: `RuntimeResult.termination`이 없어 실패한다.

- [ ] **Step 4: runtime 매핑 구현**

`RuntimeResult`에 기본값을 추가해 직접 생성하는 기존 테스트 호환성을 유지한다.

```python
termination: Literal["completed", "failed", "cancelled", "timed_out"] = "completed"
```

`handle_user_message()`에서 `RemoteRunAbortedError`를 generic exception보다 먼저 처리한다. timeout code와 cancel code를 분리하고, 나머지 `RemoteRunError`는 failed로 처리한다. runtime event와 transcript payload에 `error_code`를 기록한다.

- [ ] **Step 5: API 응답 구현**

`_runtime_response`, `_compat_chat_response`, `_runtime_audit_status`가 `termination`을 사용하도록 수정한다. 기존 `messages`와 `pending_approval` 키는 유지한다.

- [ ] **Step 6: runtime/API 테스트 통과 확인**

Run: `uv run pytest tests/test_runtime.py tests/test_app.py -q`

Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add src/personal_agent_gateway/runtime.py src/personal_agent_gateway/api/chat_sessions.py tests/test_runtime.py tests/test_app.py
git commit -m "feat: expose remote run termination state"
```

## Task 6: 문서와 전체 회귀

**Files:**
- Modify: `README.md`
- Modify: `../local-model-gateway/README.md`

- [ ] **Step 1: 프로토콜 문서 갱신**

두 README에 terminal 표, error code, partial content의 진단 전용 의미, EOF-without-terminal 실패 규칙을 기록한다.

- [ ] **Step 2: 포맷과 전체 테스트**

Run: `cd ../local-model-gateway && gofmt -w internal/event/event.go internal/event/event_test.go internal/proc/proc.go internal/proc/proc_test.go internal/run/failure.go internal/run/run.go internal/run/run_test.go internal/provider/provider.go internal/provider/provider_test.go internal/provider/codex/codex.go internal/provider/codex/codex_test.go internal/provider/codex/normalize.go internal/provider/codex/normalize_test.go internal/provider/claude/claude.go internal/provider/claude/claude_test.go internal/provider/claude/normalize.go internal/provider/claude/normalize_test.go internal/provider/openai/openai.go internal/provider/openai/openai_test.go internal/httpapi/runs.go internal/httpapi/runs_test.go`

Run: `cd ../local-model-gateway && go test ./...`

Expected: PASS

Run: `uv run pytest -q`

Expected: PASS

Run: `git diff --check && git -C ../local-model-gateway diff --check`

Expected: 출력 없음.

- [ ] **Step 3: 문서 커밋**

```bash
git add README.md
git commit -m "docs: define PAG handling of LMG terminals"
cd ../local-model-gateway
git add README.md
git commit -m "docs: define LMG terminal stream contract"
```

## Acceptance Checklist

- [ ] 부분 출력 뒤 provider 오류는 완료가 아니라 failed/aborted다.
- [ ] 모든 LMG run은 쓰기 가능한 연결에서 terminal 하나만 보낸다.
- [ ] PAG는 malformed event, terminal 없는 EOF, 중복 terminal을 실패 처리한다.
- [ ] 실패와 중단에 partial output, upstream session ID, stable error code가 남는다.
- [ ] Chat API가 completed/failed/cancelled/timed_out을 구분한다.
- [ ] 양쪽 전체 테스트가 통과한다.
