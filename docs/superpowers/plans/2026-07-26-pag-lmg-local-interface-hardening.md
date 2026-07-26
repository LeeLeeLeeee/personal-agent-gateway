# PAG-LMG Local Interface Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 같은 PC의 신뢰된 서비스만 LMG를 호출한다는 전제에서, loopback 바인딩·공유 토큰·엄격한 요청 검증·PAG가 결정한 Space 경로의 LMG 재검증을 적용한다.

**Architecture:** PAG는 Space 정책을 실행 값으로 변환하고 모든 LMG 요청에 하나의 로컬 공유 토큰과 추적 메타데이터를 보낸다. LMG는 `/livez`를 제외한 API에서 토큰을 검증하고, 실행 경로를 canonical path로 바꾼 뒤 존재 여부·디렉터리 여부·허용 루트 포함 여부를 확인한다. `consumer` 값은 추적용이며 권한 분리에는 사용하지 않는다.

**Tech Stack:** Python 3.11, Pydantic, httpx, pytest, Go 1.26.5, `net/http`, SQLite

## Global Constraints

- LMG는 `127.0.0.1` 또는 `::1`에만 바인딩한다. 원격 접속, TLS, 사용자별 토큰, 테넌트 ACL은 추가하지 않는다.
- `LMG_LOCAL_TOKEN`은 모든 로컬 소비자가 공유한다. 이 토큰은 브라우저·우발적 로컬 호출 방어용이며 악성 로컬 프로세스 격리 수단이 아니다.
- `/livez`만 무인증이다. `/v1/models`, `/v1/runs`, `/v1/sessions`, 이후 추가될 `/readyz`는 인증한다.
- 요청 본문 한도는 1 MiB, `Content-Type`은 `application/json`, 알 수 없는 필드와 두 번째 JSON 값은 거부한다.
- provider, message role/content처럼 스키마상 필수인 문자열은 `strings.TrimSpace` 결과가 비면 거부한다. 선택적인 consumer 필드는 부분 조합과 공백 값만 거부한다.
- PAG가 Space 소유권을 유지한다. LMG는 경로를 생성하거나 정책을 추론하지 않고 전달받은 실행 값을 검증한다.
- Codex의 `--add-dir`는 추가 writable root를 만든다. read-only를 뜻하는 외부 `read_roots`를 이 옵션으로 변환하지 않고, CLI가 read-only를 보장하지 못하는 실행은 거부한다.
- 기존 사용자 변경과 `6c12dbcf764844429cac403d3e89a2e2/` 디렉터리는 수정하거나 커밋하지 않는다.
- 이 계획은 스트림 terminal 계약, 세션 fingerprint/삭제, 동시성·상태 API를 구현하지 않는다.

---

## File Structure

### personal-agent-gateway

- Modify: `src/personal_agent_gateway/config.py`
- Modify: `src/personal_agent_gateway/lmg_client.py`
- Modify: `src/personal_agent_gateway/remote_model_client.py`
- Modify: `src/personal_agent_gateway/runtime_factory.py`
- Modify: `src/personal_agent_gateway/app.py`
- Modify: `tests/test_config_auth.py`
- Modify: `tests/test_lmg_client.py`
- Modify: `tests/test_remote_model_client.py`
- Modify: `tests/test_runtime_factory_headless.py`
- Modify: `tests/test_app_team_factory.py`
- Modify: `README.md`

### local-model-gateway

- Modify: `../local-model-gateway/internal/config/config.go`
- Create: `../local-model-gateway/internal/execution/validate.go`
- Create: `../local-model-gateway/internal/execution/validate_test.go`
- Create: `../local-model-gateway/internal/httpapi/middleware.go`
- Create: `../local-model-gateway/internal/httpapi/request.go`
- Modify: `../local-model-gateway/internal/httpapi/router.go`
- Modify: `../local-model-gateway/internal/httpapi/runs.go`
- Modify: `../local-model-gateway/internal/httpapi/models.go`
- Modify: `../local-model-gateway/internal/httpapi/sessions.go`
- Modify: `../local-model-gateway/internal/httpapi/runs_test.go`
- Modify: `../local-model-gateway/internal/httpapi/models_test.go`
- Modify: `../local-model-gateway/internal/httpapi/sessions_test.go`
- Modify: `../local-model-gateway/internal/config/config_test.go`
- Modify: `../local-model-gateway/internal/provider/codex/command.go`
- Modify: `../local-model-gateway/internal/provider/codex/command_test.go`
- Modify: `../local-model-gateway/internal/provider/claude/command.go`
- Modify: `../local-model-gateway/internal/provider/claude/command_test.go`
- Modify: `../local-model-gateway/cmd/lmg/main.go`
- Modify: `../local-model-gateway/README.md`

## Task 1: PAG 공유 토큰 설정과 공통 헤더

**Files:**
- Modify: `src/personal_agent_gateway/config.py`
- Modify: `src/personal_agent_gateway/lmg_client.py`
- Modify: `tests/test_config_auth.py`
- Modify: `tests/test_lmg_client.py`

- [ ] **Step 1: 실패하는 설정 테스트 작성**

`tests/test_config_auth.py`에 `LMG_LOCAL_TOKEN=local-secret`가 `AppConfig.lmg_local_token`으로 로드되는 테스트와 `from_env()`가 빈 토큰을 거부하는 테스트를 추가한다. 직접 `AppConfig`를 만드는 기존 테스트를 깨지 않도록 필드 기본값은 `str | None = None`으로 두되, 실제 환경 기반 시작 경로에서는 필수로 검증한다.

- [ ] **Step 2: 설정 테스트 실패 확인**

Run: `uv run pytest tests/test_config_auth.py -q`

Expected: `AppConfig`에 `lmg_local_token`이 없어서 실패한다.

- [ ] **Step 3: 설정 필드와 환경 변수 매핑 구현**

`AppConfig`에 다음 필드를 추가하고 `from_env()` 및 설정 매핑에 `LMG_LOCAL_TOKEN`을 연결한다. `from_env()`는 값을 공백 제거한 뒤 비어 있으면 설정 오류를 발생시킨다.

```python
lmg_local_token: str | None = None
```

- [ ] **Step 4: 동기 LMG 요청의 인증 테스트 작성**

`tests/test_lmg_client.py`에서 models, sessions, delete 요청 모두 다음 헤더를 받는지 검사한다.

```python
assert request.headers["authorization"] == "Bearer local-secret"
```

토큰이 `None`인 기존 단위 테스트에서는 헤더를 보내지 않아 기존 호출자 호환성을 유지한다.

- [ ] **Step 5: 공통 헤더 함수 구현**

`lmg_client.py`에 다음 시그니처를 추가하고 세 함수가 공통 사용하게 한다.

```python
def _lmg_headers(config: AppConfig) -> dict[str, str]:
    if config.lmg_local_token is None:
        return {}
    return {"Authorization": f"Bearer {config.lmg_local_token}"}
```

- [ ] **Step 6: PAG 설정·클라이언트 테스트 통과 확인**

Run: `uv run pytest tests/test_config_auth.py tests/test_lmg_client.py -q`

Expected: PASS

- [ ] **Step 7: PAG 변경 커밋**

```bash
git add src/personal_agent_gateway/config.py src/personal_agent_gateway/lmg_client.py tests/test_config_auth.py tests/test_lmg_client.py
git commit -m "feat: configure LMG local authentication"
```

## Task 2: 비동기 실행 요청 인증과 소비자 추적값

**Files:**
- Modify: `src/personal_agent_gateway/remote_model_client.py`
- Modify: `src/personal_agent_gateway/runtime_factory.py`
- Modify: `src/personal_agent_gateway/app.py`
- Modify: `tests/test_remote_model_client.py`
- Modify: `tests/test_runtime_factory_headless.py`
- Modify: `tests/test_app_team_factory.py`

- [ ] **Step 1: 요청 계약 테스트 작성**

`tests/test_remote_model_client.py`에 다음을 검증하는 테스트를 추가한다.

- 토큰이 있으면 `Authorization: Bearer local-secret`를 보낸다.
- 본문에 `consumer`, `consumer_session_id`, 매 호출마다 새 UUID 형식의 `consumer_run_id`가 있다.
- 토큰이 없으면 Authorization 헤더가 없다.

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/test_remote_model_client.py -q`

Expected: 새 생성자 인자와 본문 필드가 없어서 실패한다.

- [ ] **Step 3: `HttpModelClient` 계약 구현**

생성자에 아래 인자를 추가한다.

```python
local_token: str | None = None
consumer: str = "personal-agent-gateway"
consumer_session_id: str | None = None
```

`complete()`마다 `str(uuid.uuid4())`를 생성하고 본문 최상위에 `consumer`, `consumer_run_id`를 넣는다. `consumer_session_id`는 값이 있을 때만 넣고 `null`은 보내지 않는다. `local_token`이 있을 때만 stream 요청에 Authorization 헤더를 보낸다.

- [ ] **Step 4: 개인 Chat/Hook 런타임 전달 테스트 작성**

`tests/test_runtime_factory_headless.py`에서 생성된 클라이언트가 config token을 사용하고, 일반 chat은 PAG session ID, hook은 hook transcript session ID를 `consumer_session_id`로 사용하는지 검사한다.

- [ ] **Step 5: `AgentRuntimeFactory._remote_client` 전달 구현**

`_remote_client()`에 `consumer_session_id`를 받고 아래 값을 전달한다.

```python
local_token=self._config.lmg_local_token
consumer="personal-agent-gateway"
consumer_session_id=consumer_session_id
```

모든 `_remote_client()` 호출 지점에서 이미 결정된 transcript session ID를 전달한다.

- [ ] **Step 6: Team 실행 전달 테스트와 구현**

`tests/test_app_team_factory.py`에 팀 agent가 다음 값을 전송하는 테스트를 추가한 뒤 `app.py::_team_model_factory`의 Claude/Codex 생성 지점을 수정한다.

```python
local_token=config.lmg_local_token
consumer="personal-agent-gateway"
consumer_session_id=agent.team_run_id
```

- [ ] **Step 7: 비동기 클라이언트 계층 테스트 통과 확인**

Run: `uv run pytest tests/test_remote_model_client.py tests/test_runtime_factory_headless.py tests/test_app_team_factory.py -q`

Expected: PASS

- [ ] **Step 8: PAG 변경 커밋**

```bash
git add src/personal_agent_gateway/remote_model_client.py src/personal_agent_gateway/runtime_factory.py src/personal_agent_gateway/app.py tests/test_remote_model_client.py tests/test_runtime_factory_headless.py tests/test_app_team_factory.py
git commit -m "feat: identify authenticated LMG consumers"
```

## Task 3: LMG loopback·토큰 설정 검증

**Files:**
- Modify: `../local-model-gateway/internal/config/config.go`
- Modify: `../local-model-gateway/internal/config/config_test.go`

- [ ] **Step 1: 실패하는 config 테스트 작성**

다음 사례를 table test로 추가한다.

- 빈 `LMG_LOCAL_TOKEN`은 오류
- `127.0.0.1`, `::1`은 허용
- `localhost`, `0.0.0.0`, LAN IP는 거부
- `LMG_ALLOWED_ROOTS`는 `os.PathListSeparator`로 분리하고 절대 canonical 경로로 저장
- 존재하지 않거나 파일인 allowed root는 오류

- [ ] **Step 2: Go 버전 확인 및 테스트 실패 확인**

Run: `cd ../local-model-gateway && go version`

Expected: `go1.26.5` 이상. 다르면 Go 1.26.5 환경으로 전환한 뒤 계속한다.

Run: `cd ../local-model-gateway && go test ./internal/config`

Expected: 새 config 필드가 없어 실패한다.

- [ ] **Step 3: config 필드와 검증 구현**

`Config`에 다음 필드를 추가한다.

```go
LocalToken   string
AllowedRoots []string
```

`LMG_LOCAL_TOKEN`은 공백 제거 후 빈 값이면 기동을 거부한다. `LMG_ALLOWED_ROOTS`가 비어 있으면 허용 루트 제한을 사용하지 않되, 각 run의 경로 자체 검증은 Task 5에서 수행한다. 값이 있으면 `filepath.Abs`, `filepath.EvalSymlinks`, `os.Stat`을 적용한다.

- [ ] **Step 4: config 테스트 통과 확인**

Run: `cd ../local-model-gateway && go test ./internal/config`

Expected: PASS

- [ ] **Step 5: LMG 변경 커밋**

```bash
cd ../local-model-gateway
git add internal/config/config.go internal/config/config_test.go
git commit -m "feat: require loopback LMG authentication"
```

## Task 4: HTTP 인증·메서드·JSON 경계

**Files:**
- Create: `../local-model-gateway/internal/httpapi/middleware.go`
- Create: `../local-model-gateway/internal/httpapi/request.go`
- Modify: `../local-model-gateway/internal/httpapi/router.go`
- Modify: `../local-model-gateway/internal/httpapi/runs.go`
- Modify: `../local-model-gateway/internal/httpapi/models.go`
- Modify: `../local-model-gateway/internal/httpapi/sessions.go`
- Modify: `../local-model-gateway/internal/httpapi/runs_test.go`
- Modify: `../local-model-gateway/internal/httpapi/models_test.go`
- Modify: `../local-model-gateway/internal/httpapi/sessions_test.go`

- [ ] **Step 1: 인증과 요청 거부 테스트 작성**

router 수준 테스트에 다음 상태 코드를 고정한다.

| 조건 | 상태 |
|---|---:|
| `/livez` GET | 200 |
| 보호 API 토큰 없음/불일치 | 401 |
| 잘못된 메서드 | 405 + `Allow` |
| JSON 이외 Content-Type | 415 |
| 1 MiB 초과 | 413 |
| unknown field/두 번째 JSON 값 | 400 |
| 필수 문자열이 비었거나 공백뿐임 | 422 |

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd ../local-model-gateway && go test ./internal/httpapi`

Expected: 보호 API가 인증 없이 응답하거나 잘못된 JSON을 허용해서 실패한다.

- [ ] **Step 3: timing-safe bearer 인증 구현**

`middleware.go`에 다음 시그니처로 구현한다.

```go
func RequireBearer(token string, next http.Handler) http.Handler
```

`Authorization`을 정확히 `Bearer <token>` 형식으로 파싱하고 `crypto/subtle.ConstantTimeCompare`로 비교한다. 실패 응답은 토큰 값을 포함하지 않는 `401 {"code":"unauthorized_local_client"}`로 통일한다.

- [ ] **Step 4: 엄격한 JSON decoder 구현**

`request.go`에 다음을 구현한다.

```go
const maxJSONBodyBytes int64 = 1 << 20
func decodeJSON(w http.ResponseWriter, r *http.Request, dst interface{}) error
```

`http.MaxBytesReader`, `mime.ParseMediaType`, `json.Decoder.DisallowUnknownFields()`를 사용하고 첫 decode 뒤 두 번째 decode가 `io.EOF`인지 확인한다.

- [ ] **Step 5: router와 handler 메서드 경계 구현**

`Deps`에 `LocalToken string`을 추가한다. `/livez`는 인증 middleware 밖에 등록하고, `/v1/*` mux 전체를 `RequireBearer`로 감싼다. 각 handler는 허용 메서드가 아니면 `Allow` 헤더와 405를 반환한다. JSON/method/content-type/body-limit/schema 오류는 기존 상태 코드를 유지하되 body의 stable code를 `invalid_request`로 통일한다. decode 뒤 schema validator가 필수 문자열을 trim 검사하고 빈 값에는 `422 invalid_request`를 반환한다. `runRequestBody`에는 다음 추적 필드를 추가해 엄격한 decoder와 PAG 요청이 호환되게 한다.

```go
Consumer          string `json:"consumer"`
ConsumerSessionID string `json:"consumer_session_id"`
ConsumerRunID     string `json:"consumer_run_id"`
```

consumer 필드는 전부 생략할 수 있다. `consumer`가 있으면 `consumer_run_id`도 필수이고, `consumer_session_id`는 선택이다. `consumer` 없이 session/run ID만 온 부분 조합과 제공된 값이 공백뿐인 경우는 `422 invalid_request`다.

- [ ] **Step 6: HTTP API 테스트 통과 확인**

Run: `cd ../local-model-gateway && go test ./internal/httpapi`

Expected: PASS

- [ ] **Step 7: LMG 변경 커밋**

```bash
cd ../local-model-gateway
git add internal/httpapi/middleware.go internal/httpapi/request.go internal/httpapi/router.go internal/httpapi/runs.go internal/httpapi/models.go internal/httpapi/sessions.go internal/httpapi/runs_test.go internal/httpapi/models_test.go internal/httpapi/sessions_test.go
git commit -m "feat: enforce LMG HTTP request boundary"
```

## Task 5: 실행 경로 canonical 검증

**Files:**
- Create: `../local-model-gateway/internal/execution/validate.go`
- Create: `../local-model-gateway/internal/execution/validate_test.go`
- Modify: `../local-model-gateway/internal/httpapi/router.go`
- Modify: `../local-model-gateway/internal/httpapi/runs.go`
- Modify: `../local-model-gateway/internal/httpapi/runs_test.go`
- Modify: `../local-model-gateway/internal/provider/codex/command.go`
- Modify: `../local-model-gateway/internal/provider/codex/command_test.go`
- Modify: `../local-model-gateway/internal/provider/claude/command.go`
- Modify: `../local-model-gateway/internal/provider/claude/command_test.go`

- [ ] **Step 1: 실행 경로 실패 테스트 작성**

다음 입력을 검증한다.

- 상대 `workspace_root` 거부
- 존재하지 않는 경로와 일반 파일 거부
- symlink를 canonical 경로로 변환
- allowed root 자신과 하위 경로 허용
- 문자열 prefix만 같은 sibling 경로 거부
- 각 `read_roots`에 같은 규칙 적용
- allowed roots가 비어 있어도 절대·존재·디렉터리 검증은 유지
- Codex/Claude의 workspace 밖 `read_roots`는 `external read-only roots unsupported`로 거부

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd ../local-model-gateway && go test ./internal/execution ./internal/httpapi`

Expected: `internal/execution` 패키지가 없어 실패한다.

- [ ] **Step 3: 경로 검증기 구현**

다음 공개 함수를 구현한다.

```go
func Validate(
    providerName string,
    input provider.Execution,
    allowedRoots []string,
) (provider.Execution, error)
```

포함 관계는 `filepath.Rel(root, candidate)` 결과가 `..`이 아니고 절대 경로가 아닌지로 판정한다. 반환값에는 canonical `WorkspaceRoot`와 `ReadRoots`를 넣고 나머지 실행 옵션은 그대로 보존한다.

Codex/Claude에서 `read_roots`가 workspace 밖이면 현재 CLI 계약으로 read-only를 보장할 수 없으므로 거부한다. 두 provider command의 `--add-dir` 변환도 제거한다. workspace 내부 read root는 별도 CLI flag 없이 기본 sandbox 범위로 접근한다. OpenAI provider는 PAG tool 계층이 read/write를 구분하므로 canonical read roots를 허용한다.

- [ ] **Step 4: RunsHandler에 검증기 연결**

`Deps`에 `AllowedRoots []string`을 추가하고 provider 조회 후 SSE 헤더를 쓰기 전에 검증한다. 잘못된 실행 컨텍스트는 provider를 호출하지 않고 `422 invalid_execution_path`를 반환한다.

- [ ] **Step 5: 경로 검증 테스트 통과 확인**

Run: `cd ../local-model-gateway && go test ./internal/execution ./internal/httpapi ./internal/provider/codex ./internal/provider/claude`

Expected: PASS

- [ ] **Step 6: LMG 변경 커밋**

```bash
cd ../local-model-gateway
git add internal/execution/validate.go internal/execution/validate_test.go internal/httpapi/router.go internal/httpapi/runs.go internal/httpapi/runs_test.go internal/provider/codex/command.go internal/provider/codex/command_test.go internal/provider/claude/command.go internal/provider/claude/command_test.go
git commit -m "feat: validate PAG execution paths in LMG"
```

## Task 6: 서버 조립·문서·통합 검증

**Files:**
- Modify: `../local-model-gateway/cmd/lmg/main.go`
- Modify: `../local-model-gateway/README.md`
- Modify: `README.md`

- [ ] **Step 1: main 조립**

`httpapi.Deps`에 `cfg.LocalToken`, `cfg.AllowedRoots`를 전달하고 `http.ListenAndServe`를 아래 서버로 교체한다. SSE 장기 실행을 끊는 `WriteTimeout`은 설정하지 않는다.

```go
server := &http.Server{
    Addr:              addr,
    Handler:           router,
    ReadHeaderTimeout: 5 * time.Second,
    ReadTimeout:       30 * time.Second,
    IdleTimeout:       120 * time.Second,
}
log.Fatal(server.ListenAndServe())
```

- [ ] **Step 2: 양쪽 README 갱신**

두 README에 다음을 명시한다.

- 동일한 `LMG_LOCAL_TOKEN`을 PAG와 LMG에 설정
- 선택적 `LMG_ALLOWED_ROOTS` 형식과 예시
- `/livez`만 무인증
- loopback-only와 공유 토큰의 보안 한계
- `consumer*`는 추적 정보이며 권한 근거가 아님

- [ ] **Step 3: 전체 테스트 실행**

Run: `cd ../local-model-gateway && go test ./...`

Expected: PASS

Run: `uv run pytest -q`

Expected: PASS

- [ ] **Step 4: 포맷과 diff 검사**

Run: `cd ../local-model-gateway && gofmt -w internal/config/config.go internal/config/config_test.go internal/execution/validate.go internal/execution/validate_test.go internal/httpapi/middleware.go internal/httpapi/request.go internal/httpapi/router.go internal/httpapi/runs.go internal/httpapi/models.go internal/httpapi/sessions.go internal/httpapi/runs_test.go internal/httpapi/models_test.go internal/httpapi/sessions_test.go cmd/lmg/main.go`

Run: `git diff --check && git -C ../local-model-gateway diff --check`

Expected: 출력 없음.

- [ ] **Step 5: 문서와 main 변경 커밋**

```bash
git add README.md
git commit -m "docs: describe authenticated PAG-LMG interface"
cd ../local-model-gateway
git add cmd/lmg/main.go README.md
git commit -m "feat: serve hardened loopback LMG API"
```

## Acceptance Checklist

- [ ] 인증 없는 `/livez`가 200이고 보호 API는 유효한 토큰 없이는 401이다.
- [ ] LMG는 loopback 이외 주소로 기동하지 않는다.
- [ ] malformed/unknown/oversized JSON과 잘못된 method/content type이 provider 실행 전에 거부된다.
- [ ] workspace/read roots가 canonical 기존 디렉터리이고 allowed root 정책을 만족한다.
- [ ] CLI provider가 외부 read-only root를 writable flag로 승격하지 않는다.
- [ ] 개인 Chat, Hook, Team 실행 모두 같은 토큰과 소비자 추적값을 전송한다.
- [ ] PAG 전체 pytest와 LMG 전체 Go test가 통과한다.
