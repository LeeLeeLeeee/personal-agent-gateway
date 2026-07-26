# PAG-LMG Session Lifecycle Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** upstream session ID 유실, 실행 컨텍스트가 바뀐 세션의 잘못된 resume, Chat 삭제와 로컬 세션 삭제의 불일치를 제거한다.

**Architecture:** PAG는 `session.updated` event를 받는 즉시 upstream link를 기록하고, provider/model/Space/실행 옵션/persona/rules/system prompt 전체의 context fingerprint가 같은 경우만 resume한다. LMG는 소비자 상관관계를 세션 레코드에 저장하고 DELETE를 idempotent하게 처리한다. PAG 삭제는 연결된 모든 upstream 삭제를 시도한 뒤 결과를 집계하며 일부 실패 시 Chat을 보존한다.

**Tech Stack:** Python 3.11, transcript event store, SHA-256 canonical JSON, pytest, Go 1.26.5, SQLite

## Global Constraints

- 선행 조건은 local interface hardening과 terminal stream contract 계획 구현 완료다.
- `session.updated`를 받은 뒤 terminal이 실패해도 upstream link는 남아야 한다.
- legacy `options_fingerprint`만 있는 link는 안전한 context 일치를 증명할 수 없으므로 resume에 사용하지 않는다. 삭제 대상 조회에는 계속 포함한다.
- context mismatch는 기존 upstream을 재사용하지 않고 새 upstream run을 시작한다.
- DELETE는 LMG에서 없는 ID도 성공이다.
- PAG는 모든 upstream ID 삭제를 시도한다. 하나라도 실패하면 Chat transcript는 삭제하지 않고 502와 실패 ID 목록을 반환한다.
- 정합성 점검은 read-only다. LMG 장애를 모든 session 누락으로 오판하지 않고 503으로 반환한다.
- 재시도용 outbox나 자동 background delete는 추가하지 않는다.
- 이 계획은 세션 파일 scan 제거와 concurrency limit를 구현하지 않는다.

---

## File Structure

### personal-agent-gateway

- Modify: `src/personal_agent_gateway/agent_session_link.py`
- Modify: `src/personal_agent_gateway/runtime_factory.py`
- Modify: `src/personal_agent_gateway/runtime.py`
- Modify: `src/personal_agent_gateway/remote_model_client.py`
- Modify: `src/personal_agent_gateway/lmg_client.py`
- Create: `src/personal_agent_gateway/session_consistency.py`
- Modify: `src/personal_agent_gateway/api/chat_sessions.py`
- Modify: `src/personal_agent_gateway/api/audit.py`
- Modify: `tests/test_agent_session_link.py`
- Modify: `tests/test_runtime_factory_headless.py`
- Modify: `tests/test_runtime.py`
- Modify: `tests/test_remote_model_client.py`
- Modify: `tests/test_lmg_client.py`
- Modify: `tests/test_app.py`
- Create: `tests/test_session_consistency.py`
- Create: `tests/test_api_audit.py`
- Modify: `README.md`

### local-model-gateway

- Modify: `../local-model-gateway/internal/session/session.go`
- Modify: `../local-model-gateway/internal/session/session_test.go`
- Modify: `../local-model-gateway/internal/provider/provider.go`
- Modify: `../local-model-gateway/internal/httpapi/runs.go`
- Modify: `../local-model-gateway/internal/httpapi/runs_test.go`
- Modify: `../local-model-gateway/internal/httpapi/sessions.go`
- Modify: `../local-model-gateway/internal/httpapi/sessions_test.go`
- Modify: `../local-model-gateway/internal/provider/codex/codex.go`
- Modify: `../local-model-gateway/internal/provider/codex/codex_test.go`
- Modify: `../local-model-gateway/internal/provider/codex/command.go`
- Modify: `../local-model-gateway/internal/provider/codex/command_test.go`
- Modify: `../local-model-gateway/README.md`

## Task 1: 전체 실행 컨텍스트 fingerprint

**Files:**
- Modify: `src/personal_agent_gateway/agent_session_link.py`
- Modify: `tests/test_agent_session_link.py`

- [ ] **Step 1: context 모델 테스트 작성**

테스트에서 다음 필드 중 하나만 달라도 `latest()`가 `None`인지 검사한다.

- `agent_id`(실제 provider ID)
- `model`
- canonical `execution` 전체
- `persona_id`
- `persona_snapshot`
- `system_prompt`

dict key 순서만 다른 경우는 같은 fingerprint여야 한다. legacy `options_fingerprint` event는 `upstream_session_ids()`에는 나오지만 `latest()`에서는 선택되지 않아야 한다.

- [ ] **Step 2: 중복 기록 회귀 테스트 작성**

같은 session/context/upstream ID를 연속 `record()`해도 transcript에 link event가 하나만 추가되는지 검사한다. upstream ID가 달라지면 새 event가 추가되어야 한다.

- [ ] **Step 3: 실패 확인**

Run: `uv run pytest tests/test_agent_session_link.py -q`

Expected: 현재 options만 hash하고 중복 link를 append해서 실패한다.

- [ ] **Step 4: `AgentSessionContext`와 canonical hash 구현**

```python
@dataclass(frozen=True)
class AgentSessionContext:
    agent_id: str
    model: str
    execution: dict[str, object]
    persona_id: str | None
    persona_snapshot: dict[str, object] | None
    system_prompt: str | None
```

`json.dumps(asdict(context), sort_keys=True, separators=(",", ":"), ensure_ascii=False)`의 UTF-8 SHA-256을 `context_fingerprint`로 사용한다. `AgentSessionLink`와 `record/latest`는 context 객체 하나를 받는다.

- [ ] **Step 5: 중복 방지 구현**

`record()` 시작 시 같은 context의 latest link가 같은 upstream ID면 기존 link를 반환한다. legacy event에는 `context_fingerprint`가 없으므로 일치로 간주하지 않는다.

- [ ] **Step 6: 테스트 통과 확인**

Run: `uv run pytest tests/test_agent_session_link.py -q`

Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add src/personal_agent_gateway/agent_session_link.py tests/test_agent_session_link.py
git commit -m "fix: fingerprint complete upstream session context"
```

## Task 2: `session.updated` 즉시 link 기록

**Files:**
- Modify: `src/personal_agent_gateway/runtime_factory.py`
- Modify: `src/personal_agent_gateway/runtime.py`
- Modify: `tests/test_runtime_factory_headless.py`
- Modify: `tests/test_runtime.py`

- [ ] **Step 1: 실패 terminal 전 link 보존 테스트 작성**

fake remote client가 `session.updated`를 callback으로 전달한 뒤 `RemoteRunFailedError`를 raise하도록 구성한다. runtime 호출 후 transcript에 `agent_session_link`가 있고 다음 runtime 생성이 해당 upstream ID를 resume하는지 검사한다.

- [ ] **Step 2: context mismatch 테스트 작성**

동일 Chat에서 Space root, read roots, sandbox/permission, persona snapshot, system prompt 중 하나를 바꾼 뒤 새 runtime을 만들면 `upstream_session_id=None`, `history_mode="full"`인지 검사한다.

명시적 session config가 없는 기본 app-config 채팅에서도 `effective_session_id`가 있으면 link가 기록되고 다음 요청에서 resume되는 사례를 추가한다. headless 실행처럼 소비자 session ID가 없는 경우에는 link를 만들지 않는다.

- [ ] **Step 3: 실패 확인**

Run: `uv run pytest tests/test_runtime_factory_headless.py tests/test_runtime.py -q`

Expected: 현재 link가 `ModelResponse` 뒤에만 기록되어 실패한다.

- [ ] **Step 4: factory에서 단일 context 구성**

`_create_runtime_for_session_id()`에서 실제 client에 전달할 `execution`을 먼저 만들고 다음 context를 한 번만 구성한다.

```python
context = AgentSessionContext(
    agent_id=agent_id,
    model=model,
    execution=execution,
    persona_id=session_config.persona_id,
    persona_snapshot=session_config.persona_snapshot,
    system_prompt=system_prompt,
)
```

`latest()`와 `record()` 모두 같은 객체를 사용한다.

명시적 session config가 없어 `_create_runtime_for_app_config()`를 사용하는 경로도 `effective_session_id`가 있으면 provider/model/execution과 기본 persona/system-prompt 값을 사용해 동일한 context를 만든다. 두 factory 경로가 같은 private context builder와 callback builder를 사용하게 한다.

- [ ] **Step 5: model event callback에서 즉시 기록**

중복된 Codex/Claude callback을 private helper로 합치고, event kind가 `session.updated`이며 유효한 `upstream_session_id`가 있으면 event bus publish 전에 link를 기록한다. callback은 동기 transcript 기록 뒤 비동기 publish를 수행한다.

- [ ] **Step 6: terminal 뒤 기록 제거**

`AgentRuntime`의 `on_upstream_session_id` 생성자 인자와 `_run_model_loop()`의 완료 후 callback을 제거한다. upstream link의 단일 기록 지점을 `session.updated` handler로 만든다. 직접 사용하는 테스트 fixture도 함께 수정한다.

- [ ] **Step 7: runtime 테스트 통과 확인**

Run: `uv run pytest tests/test_runtime_factory_headless.py tests/test_runtime.py -q`

Expected: PASS

- [ ] **Step 8: 커밋**

```bash
git add src/personal_agent_gateway/runtime_factory.py src/personal_agent_gateway/runtime.py tests/test_runtime_factory_headless.py tests/test_runtime.py
git commit -m "fix: persist upstream links on session update"
```

## Task 3: Codex resume에 실행 컨텍스트 재적용

**Files:**
- Modify: `../local-model-gateway/internal/provider/codex/codex.go`
- Modify: `../local-model-gateway/internal/provider/codex/codex_test.go`
- Modify: `../local-model-gateway/internal/provider/codex/command.go`
- Modify: `../local-model-gateway/internal/provider/codex/command_test.go`

- [ ] **Step 1: resume argv 회귀 테스트 작성**

resume 명령이 다음을 포함하는지 검사한다.

- `--model`
- `--skip-git-repo-check`
- config override로 approval policy와 effort
- `-c sandbox_mode="<mode>"` 형식의 sandbox config override

`codex exec resume --help`에 없는 `-C`, `--add-dir`, `--profile`, `--sandbox`는 resume 뒤 직접 추가하지 않는다. process `WorkDir`는 workspace root를 계속 사용한다.

profile이 있거나 workspace 밖 read root가 있는 resume 요청은 지원되지 않는 context 오류를 반환하고 subprocess를 시작하지 않는 사례도 검사한다. local interface hardening 계획이 신규 외부 read root를 먼저 차단하더라도 기존 session record를 안전하게 처리하기 위한 방어다.

- [ ] **Step 2: 실패 확인**

Run: `cd ../local-model-gateway && go test ./internal/provider/codex`

Expected: 현재 resume argv가 sandbox context를 누락하고 지원하지 않는 profile/read-root context를 조용히 생략해 실패한다.

- [ ] **Step 3: 지원되는 resume config override 구현**

초기 실행과 resume에 공통 적용할 config override builder를 추출한다. resume은 CLI help가 허용하는 `-c key=value`, `--model`, `--skip-git-repo-check`만 사용하며 process `WorkDir`는 workspace root로 고정한다.

```go
func contextConfigArgs(execution provider.Execution) []string
```

`contextConfigArgs`는 approval policy, effort, sandbox mode만 직렬화한다. profile은 이름만으로 내용을 안전하게 재구성할 수 없고 `resume`이 `--profile`을 지원하지 않으므로 `unsupported resume profile`로 거부한다. 외부 read root 역시 `resume`에서 read-only로 재적용할 방법이 없으므로 `unsupported resume read roots`로 거부한다. 지원하지 않는 context를 조용히 누락하는 resume은 금지한다.

`buildCommand`가 검증 오류를 반환할 수 있도록 시그니처를 `func buildCommand(bin string, req provider.RunRequest) ([]string, error)`로 바꾸고, `Provider.Run`은 runner를 호출하기 전에 해당 오류를 그대로 반환한다.

- [ ] **Step 4: command 테스트 통과 확인**

Run: `cd ../local-model-gateway && go test ./internal/provider/codex`

Expected: PASS

- [ ] **Step 5: 커밋**

```bash
cd ../local-model-gateway
git add internal/provider/codex/codex.go internal/provider/codex/codex_test.go internal/provider/codex/command.go internal/provider/codex/command_test.go
git commit -m "fix: preserve Codex context on resume"
```

## Task 4: LMG 세션 소비자 상관관계 저장

**Files:**
- Modify: `../local-model-gateway/internal/session/session.go`
- Modify: `../local-model-gateway/internal/session/session_test.go`
- Modify: `../local-model-gateway/internal/provider/provider.go`
- Modify: `../local-model-gateway/internal/httpapi/runs.go`
- Modify: `../local-model-gateway/internal/httpapi/runs_test.go`
- Modify: `src/personal_agent_gateway/runtime_factory.py`
- Modify: `src/personal_agent_gateway/remote_model_client.py`
- Modify: `tests/test_runtime_factory_headless.py`
- Modify: `tests/test_remote_model_client.py`

- [ ] **Step 1: SQLite migration/record 테스트 작성**

기존 5-column DB를 열어도 migration이 성공하고 다음 필드를 record/list/get에서 보존하는지 검사한다.

```go
Consumer          string `json:"consumer,omitempty"`
ConsumerSessionID string `json:"consumer_session_id,omitempty"`
ConsumerRunID     string `json:"consumer_run_id,omitempty"`
ConsumerContextFingerprint string `json:"consumer_context_fingerprint,omitempty"`
```

같은 upstream ID의 후속 run은 created_at은 보존하고 last_run_at과 소비자 값을 갱신해야 한다.

- [ ] **Step 2: 실패 확인**

Run: `cd ../local-model-gateway && go test ./internal/session ./internal/httpapi`

Expected: DB column과 record 전달이 없어 실패한다.

- [ ] **Step 3: additive migration 구현**

`Open()`에서 `PRAGMA table_info(sessions)`로 column 존재를 확인하고 없는 column만 `ALTER TABLE ... ADD COLUMN`한다. 문자열 결합에는 코드에 고정된 column 이름만 사용한다.

- [ ] **Step 4: run request metadata 전달**

local interface 계획에서 이미 정의한 top-level `consumer`, `consumer_session_id`, `consumer_run_id`에 `consumer_context_fingerprint`를 추가한다.

```json
{
  "consumer": "personal-agent-gateway",
  "consumer_session_id": "chat-session-id",
  "consumer_run_id": "per-call-uuid",
  "consumer_context_fingerprint": "sha256"
}
```

`runs.go`는 이 값을 session.Record에 전달한다. 추적 필드는 authorization 판단에 사용하지 않는다. legacy caller의 context fingerprint가 없으면 빈 필드로 저장한다.

- [ ] **Step 5: LMG 저장 테스트 통과 및 먼저 배포**

Run: `cd ../local-model-gateway && go test ./internal/session ./internal/httpapi`

Expected: PASS

- [ ] **Step 6: LMG 커밋**

```bash
cd ../local-model-gateway
git add internal/session/session.go internal/session/session_test.go internal/provider/provider.go internal/httpapi/runs.go internal/httpapi/runs_test.go
git commit -m "feat: correlate LMG sessions with local consumers"
```

- [ ] **Step 7: PAG consumer metadata 테스트와 구현**

`HttpModelClient` request body가 기존 consumer name/session/per-call UUID와 함께 context fingerprint를 전송하는 테스트를 추가한다. `runtime_factory`는 Task 1에서 만든 context fingerprint와 실제 Chat ID를 client 생성자에 전달한다. headless 실행처럼 consumer session ID가 없으면 context fingerprint도 보내지 않는다.

Run: `uv run pytest tests/test_remote_model_client.py tests/test_runtime_factory_headless.py -q`

Expected: PASS

- [ ] **Step 8: PAG 커밋**

```bash
git add src/personal_agent_gateway/runtime_factory.py src/personal_agent_gateway/remote_model_client.py tests/test_runtime_factory_headless.py tests/test_remote_model_client.py
git commit -m "feat: identify PAG sessions in LMG runs"
```

LMG commit을 배포한 뒤 PAG commit을 배포한다. 구버전 LMG가 새 top-level `consumer_context_fingerprint` 필드를 strict request validation에서 거부할 수 있으므로 순서를 뒤집지 않는다.

## Task 5: LMG idempotent DELETE

**Files:**
- Modify: `../local-model-gateway/internal/httpapi/sessions.go`
- Modify: `../local-model-gateway/internal/httpapi/sessions_test.go`
- Modify: `src/personal_agent_gateway/lmg_client.py`
- Modify: `tests/test_lmg_client.py`

- [ ] **Step 1: 없는 upstream DELETE 테스트 작성**

LMG에서 존재하지 않는 ID와 이미 삭제된 ID를 DELETE할 때 두 번 모두 204인지 검사한다. store 오류와 storage remove 오류는 계속 500이어야 한다.

- [ ] **Step 2: PAG 404 호환 테스트 작성**

구버전 LMG가 404를 반환해도 `delete_session()`이 성공으로 해석하는 테스트를 추가한다. 401/500/network failure는 false다.

- [ ] **Step 3: 실패 확인**

Run: `cd ../local-model-gateway && go test ./internal/httpapi`

Run: `uv run pytest tests/test_lmg_client.py -q`

Expected: 현재 missing ID를 실패 처리해서 실패한다.

- [ ] **Step 4: idempotent delete 구현**

LMG handler는 record가 없으면 즉시 204를 반환한다. PAG client는 404를 idempotent success로 허용하고 나머지 HTTP 오류만 false로 반환한다.

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd ../local-model-gateway && go test ./internal/httpapi`

Run: `uv run pytest tests/test_lmg_client.py -q`

Expected: PASS

- [ ] **Step 6: 양쪽 커밋**

```bash
cd ../local-model-gateway
git add internal/httpapi/sessions.go internal/httpapi/sessions_test.go
git commit -m "fix: make LMG session deletion idempotent"
cd ../personal-agent-gateway
git add src/personal_agent_gateway/lmg_client.py tests/test_lmg_client.py
git commit -m "fix: accept idempotent LMG session deletion"
```

## Task 6: PAG의 전체 삭제 시도와 원자적 Chat 보존

**Files:**
- Modify: `src/personal_agent_gateway/api/chat_sessions.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: 삭제 집계 테스트 작성**

upstream ID 세 개 중 첫째와 셋째 성공, 둘째 실패 fixture에서 다음을 검사한다.

- 세 ID 모두 호출됨
- Chat transcript와 activity는 남음
- HTTP 502 detail에 실패 ID만 포함
- 재시도 시 이미 지워진 ID의 idempotent 성공 후 남은 ID가 지워지면 Chat도 삭제됨

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_app.py -q -k delete`

Expected: 현재 첫 실패에서 loop를 중단해서 실패한다.

- [ ] **Step 3: 집계 삭제 구현**

```python
failed_upstream_ids = [
    upstream_session_id
    for upstream_session_id in upstream_session_ids
    if not delete_lmg_session(context.config, upstream_session_id)
]
if failed_upstream_ids:
    raise HTTPException(
        status_code=502,
        detail={
            "message": "Failed to delete linked local model sessions",
            "upstream_session_ids": failed_upstream_ids,
        },
    )
return context.transcript.delete(session_id)
```

list comprehension이 순차 호출을 모두 수행한다는 테스트를 유지한다. Chat 삭제와 activity 삭제는 실패 목록이 비었을 때만 실행한다.

- [ ] **Step 4: 삭제 테스트 통과 확인**

Run: `uv run pytest tests/test_app.py -q -k delete`

Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/personal_agent_gateway/api/chat_sessions.py tests/test_app.py
git commit -m "fix: reconcile all linked sessions before chat deletion"
```

## Task 7: Read-only 세션 정합성 리포트

**Files:**
- Modify: `src/personal_agent_gateway/agent_session_link.py`
- Modify: `src/personal_agent_gateway/lmg_client.py`
- Create: `src/personal_agent_gateway/session_consistency.py`
- Modify: `src/personal_agent_gateway/api/audit.py`
- Modify: `tests/test_agent_session_link.py`
- Modify: `tests/test_lmg_client.py`
- Create: `tests/test_session_consistency.py`
- Create: `tests/test_api_audit.py`

- [ ] **Step 1: link inventory와 strict LMG query 테스트 작성**

`AgentSessionLinkService.inventory()`가 모든 Chat의 link를 `upstream_session_id`, `consumer_session_id`, `context_fingerprint`로 반환하며 중복 upstream ID는 최신 link 하나만 남기는지 검사한다. `fetch_sessions_strict()`는 정상 빈 목록을 그대로 반환하지만 network/401/503/invalid JSON에서는 typed `LMGQueryError`를 raise해야 한다.

- [ ] **Step 2: 세 종류 정합성 차이 테스트 작성**

`SessionConsistencyService.report()`가 다음 배열을 정확히 만드는 fixture를 추가한다.

- `missing_in_lmg`: PAG link는 있지만 LMG 목록에는 없는 upstream ID
- `unlinked_in_pag`: LMG record의 `consumer == "personal-agent-gateway"`지만 PAG link가 없는 upstream ID
- `context_mismatch`: 같은 upstream ID의 PAG/LMG `context_fingerprint`가 다른 항목

다른 consumer의 LMG record는 `unlinked_in_pag`/`context_mismatch` 비교에서 제외한다. fingerprint가 없는 legacy record도 PAG link가 없으면 `unlinked_in_pag`에는 포함하되, 양쪽 fingerprint 비교가 불가능하므로 `context_mismatch`에서만 제외한다. 이 호출 전후 transcript와 LMG session 수가 같아야 한다.

- [ ] **Step 3: API 실패/성공 테스트 작성**

인증된 `GET /api/audit/session-consistency`가 세 배열과 각 count를 200으로 반환하는지 검사한다. LMG가 unreachable/unauthorized/not-ready/protocol-error이면 빈 report로 바꾸지 않고 503을 반환하며 외부 상세 오류나 token은 노출하지 않아야 한다.

- [ ] **Step 4: 실패 확인**

Run: `uv run pytest tests/test_agent_session_link.py tests/test_lmg_client.py tests/test_session_consistency.py tests/test_api_audit.py -q`

Expected: inventory, strict query, report service와 endpoint가 없어 실패한다.

- [ ] **Step 5: inventory와 strict query 구현**

`AgentSessionLinkService.inventory()`는 `transcript.list_sessions(origin="chat")`의 ID를 순회하고 기존 link event를 읽어 최신 항목을 만든다. `lmg_client.py`에는 오류 상태를 보존하는 `LMGQueryError`와 `fetch_sessions_strict()`를 추가한다. 기존 dashboard용 `fetch_sessions()` 호환 동작은 이 계획에서 바꾸지 않는다.

- [ ] **Step 6: read-only report와 endpoint 구현**

`session_consistency.py`에 immutable report dataclass와 `SessionConsistencyService`를 만들고 두 inventory를 set/map 비교한다. 이 service에는 delete/update/write 메서드를 주입하지 않는다. `api/audit.py` endpoint는 app의 transcript store와 config로 service를 구성하고 `LMGQueryError`를 redacted 503 `{"detail":"Local model gateway consistency check unavailable"}`로 매핑한다.

- [ ] **Step 7: 테스트 통과 확인**

Run: `uv run pytest tests/test_agent_session_link.py tests/test_lmg_client.py tests/test_session_consistency.py tests/test_api_audit.py -q`

Expected: PASS

- [ ] **Step 8: 커밋**

```bash
git add src/personal_agent_gateway/agent_session_link.py src/personal_agent_gateway/lmg_client.py src/personal_agent_gateway/session_consistency.py src/personal_agent_gateway/api/audit.py tests/test_agent_session_link.py tests/test_lmg_client.py tests/test_session_consistency.py tests/test_api_audit.py
git commit -m "feat: report PAG-LMG session inconsistencies"
```

## Task 8: 문서와 전체 회귀

**Files:**
- Modify: `README.md`
- Modify: `../local-model-gateway/README.md`

- [ ] **Step 1: 세션 수명주기 문서화**

context fingerprint 구성, legacy link resume 금지, session.updated 즉시 기록, idempotent DELETE, 부분 실패 시 Chat 보존, read-only 정합성 report 규칙을 두 README에 기록한다.

- [ ] **Step 2: 전체 검증**

Run: `cd ../local-model-gateway && gofmt -w internal/session/session.go internal/session/session_test.go internal/provider/provider.go internal/httpapi/runs.go internal/httpapi/runs_test.go internal/httpapi/sessions.go internal/httpapi/sessions_test.go internal/provider/codex/codex.go internal/provider/codex/codex_test.go internal/provider/codex/command.go internal/provider/codex/command_test.go`

Run: `cd ../local-model-gateway && go test ./...`

Expected: PASS

Run: `uv run pytest -q`

Expected: PASS

Run: `git diff --check && git -C ../local-model-gateway diff --check`

Expected: 출력 없음.

- [ ] **Step 3: 문서 커밋**

```bash
git add README.md
git commit -m "docs: describe PAG upstream session lifecycle"
cd ../local-model-gateway
git add README.md
git commit -m "docs: describe LMG session correlation"
```

## Acceptance Checklist

- [ ] 첫 run이 실패해도 `session.updated`를 받았다면 upstream link가 남는다.
- [ ] 실행 Space, persona, rules, prompt, provider 옵션이 달라지면 resume하지 않는다.
- [ ] legacy link는 삭제할 수 있지만 resume에는 쓰지 않는다.
- [ ] LMG DELETE는 없는 ID에도 성공하고 PAG는 구버전 404도 성공으로 취급한다.
- [ ] PAG는 모든 연결 세션의 삭제를 시도하고 부분 실패 시 Chat을 보존한다.
- [ ] 정합성 report가 PAG-linked/LMG-missing, LMG-owned/PAG-unlinked, fingerprint mismatch를 구분한다.
- [ ] LMG 조회 실패는 정합성 차이로 위장되지 않고 503이며 report는 어떤 세션도 수정하지 않는다.
- [ ] 양쪽 전체 테스트가 통과한다.
