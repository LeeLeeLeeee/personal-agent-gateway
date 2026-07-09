# Agent Model Options Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update Personal Agent Gateway's curated model/option catalog so users can choose supported Codex/Claude model presets, configure Codex effort, and avoid unsupported free-form model input.

**Architecture:** Keep the existing `AgentRegistry -> /api/agents -> AgentPicker -> SessionAgentConfig -> RuntimeFactory -> ModelClient` flow. The backend remains the source of truth for supported model values and rejects models outside the curated list. Codex `effort` is exposed as an agent option and mapped to the Codex CLI config override `-c model_reasoning_effort="<value>"`; Claude keeps using `--effort`.

**Tech Stack:** Python/FastAPI/Pydantic backend, pytest, Vite React, Vitest/Testing Library.

## Global Constraints

- Do not add `allow_custom_model`.
- Do not render model selection as free-form text input.
- Use only curated model presets from `AgentRegistry.models`.
- Keep backend validation strict: reject any model not in the curated list.
- Do not reintroduce Claude `fable` in the gateway preset list.
- Keep Codex `default` behavior: do not pass `-m` when model is `default`.
- Codex effort maps to `-c model_reasoning_effort="<effort>"`, not `--effort`.
- Do not broaden unrelated CLI options in this change.

---

## File Structure

- Modify `src/personal_agent_gateway/agents.py`
  - Add Codex model presets: `default`, `gpt-5.5`, `gpt-5.4`.
  - Add Claude model presets without `fable`: `default`, `best`, `sonnet`, `opus`, `haiku`, `sonnet[1m]`, `opus[1m]`, `opusplan`.
  - Add Codex `effort` option choices.
  - Keep strict model validation against `descriptor.models`.
- Do not modify `src/personal_agent_gateway/api/agents.py` for `allow_custom_model`.
  - The existing public payload shape remains: `id`, `label`, `available`, `availability_error`, `models`, `default_model`, `options_schema`, `defaults`.
- Modify `src/personal_agent_gateway/model_client.py`
  - Add `effort` to `CodexModelClient`.
  - Add `-c model_reasoning_effort="<effort>"` when effort is set.
- Modify `src/personal_agent_gateway/runtime_factory.py`
  - Pass `options["effort"]` to `CodexModelClient`, defaulting to `high`.
- Modify `src/personal_agent_gateway/app.py`
  - Pass a safe default Codex effort for team-run Codex clients.
- Modify `frontend/src/components/organisms/AgentPicker/AgentPicker.test.jsx`
  - Add test coverage that model remains a select and includes the curated model presets.
- No production change is required in `frontend/src/components/organisms/AgentPicker/index.jsx` unless tests reveal it no longer renders model as a select.

---

### Task 1: Backend Agent Catalog And Strict Model Validation

**Files:**
- Modify: `src/personal_agent_gateway/agents.py`
- Test: `tests/test_agents.py`

**Interfaces:**
- Produces: `AgentRegistry.catalog() -> list[AgentDescriptor]` with curated model presets.
- Preserves: `AgentRegistry.validate_config(agent_id: str, model: str, options: dict[str, Any]) -> dict[str, Any]` rejects unsupported model names.

- [ ] **Step 1: Write failing registry tests**

Append these assertions to `test_registry_lists_codex_and_claude_with_safe_defaults`:

```python
    assert codex.models == ["default", "gpt-5.5", "gpt-5.4"]
    assert any(option.name == "effort" and option.choices == ["low", "medium", "high", "xhigh"] for option in codex.options_schema)
    assert codex.defaults["effort"] == "high"
    assert claude.models == ["default", "best", "sonnet", "opus", "haiku", "sonnet[1m]", "opus[1m]", "opusplan"]
    assert "fable" not in claude.models
```

Keep the existing unsupported model assertion in `test_registry_rejects_unknown_agent_model_and_option`:

```python
    with pytest.raises(ValueError, match="Unsupported model"):
        registry.validate_config("codex", "not-listed", {})
```

Append this test:

```python
def test_registry_accepts_curated_model_presets_only(tmp_path: Path) -> None:
    registry = AgentRegistry(
        make_config(tmp_path),
        probe=lambda _binary: CliProbeResult(True, None),
    )

    assert registry.validate_config("codex", "gpt-5.5", {})["model"] == "gpt-5.5"
    assert registry.validate_config("claude", "opusplan", {})["model"] == "opusplan"

    with pytest.raises(ValueError, match="Unsupported model"):
        registry.validate_config("codex", "codex-5.5", {})

    with pytest.raises(ValueError, match="Unsupported model"):
        registry.validate_config("claude", "fable", {})
```

- [ ] **Step 2: Run failing tests**

Run:

```powershell
python -m pytest tests/test_agents.py -q
```

Expected: failure because the catalog does not yet include the new curated presets or Codex `effort`.

- [ ] **Step 3: Implement catalog changes**

In `_codex`, set:

```python
            models=["default", "gpt-5.5", "gpt-5.4"],
            default_model="default",
```

Add this option before `sandbox`:

```python
                AgentOption(
                    name="effort",
                    kind="select",
                    choices=["low", "medium", "high", "xhigh"],
                ),
```

Add this default:

```python
                "effort": "high",
```

In `_claude`, set:

```python
            models=["default", "best", "sonnet", "opus", "haiku", "sonnet[1m]", "opus[1m]", "opusplan"],
            default_model="sonnet",
```

Do not change the current strict validation block:

```python
        if model not in descriptor.models:
            raise ValueError(f"Unsupported model for {agent_id}: {model}")
```

- [ ] **Step 4: Run tests**

Run:

```powershell
python -m pytest tests/test_agents.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```powershell
git add src/personal_agent_gateway/agents.py tests/test_agents.py
git commit -m "feat(agents): update curated model options"
```

---

### Task 2: Agents API Contract Stays Curated

**Files:**
- Test: `tests/test_api_agents.py`

**Interfaces:**
- Consumes: existing `/api/agents` payload with `models`, `default_model`, `options_schema`, and `defaults`.
- Preserves: no `allow_custom_model` public payload field.

- [ ] **Step 1: Write API contract assertions**

In `test_agents_returns_safe_catalog`, keep `expected_keys` as:

```python
    expected_keys = {
        "id",
        "label",
        "available",
        "availability_error",
        "models",
        "default_model",
        "options_schema",
        "defaults",
    }
```

Append:

```python
    assert "allow_custom_model" not in codex
    assert codex["models"] == ["default", "gpt-5.5", "gpt-5.4"]
    assert any(option["name"] == "effort" and option["choices"] == ["low", "medium", "high", "xhigh"] for option in codex["options_schema"])
    assert codex["defaults"]["effort"] == "high"
    assert claude["models"] == ["default", "best", "sonnet", "opus", "haiku", "sonnet[1m]", "opus[1m]", "opusplan"]
    assert "fable" not in claude["models"]
```

In `test_active_session_config_defaults_and_can_be_updated_while_empty`, append:

```python
    codex_response = client.put(
        "/api/sessions/active/config",
        json={"agent_id": "codex", "model": "gpt-5.5", "options": {"effort": "xhigh"}},
    )
    unsupported_response = client.put(
        "/api/sessions/active/config",
        json={"agent_id": "codex", "model": "codex-5.5", "options": {"effort": "xhigh"}},
    )

    assert codex_response.status_code == 200
    assert codex_response.json()["config"]["model"] == "gpt-5.5"
    assert codex_response.json()["config"]["options"] == {"effort": "xhigh"}
    assert unsupported_response.status_code == 400
```

- [ ] **Step 2: Run failing API tests**

Run:

```powershell
python -m pytest tests/test_api_agents.py -q
```

Expected: failure until Task 1 catalog changes are implemented.

- [ ] **Step 3: Confirm no API implementation change**

Do not add `allow_custom_model` to `_public_agent_payload`.

Confirm `_public_agent_payload` remains:

```python
def _public_agent_payload(agent: AgentDescriptor) -> dict[str, object]:
    return {
        "id": agent.id,
        "label": agent.label,
        "available": agent.available,
        "availability_error": agent.availability_error,
        "models": agent.models,
        "default_model": agent.default_model,
        "options_schema": [_public_option_payload(option) for option in agent.options_schema],
        "defaults": agent.defaults,
    }
```

- [ ] **Step 4: Run API tests**

Run:

```powershell
python -m pytest tests/test_api_agents.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```powershell
git add tests/test_api_agents.py
git commit -m "test(api): lock curated agent model contract"
```

---

### Task 3: Codex Effort Command Mapping

**Files:**
- Modify: `src/personal_agent_gateway/model_client.py`
- Modify: `src/personal_agent_gateway/runtime_factory.py`
- Modify: `src/personal_agent_gateway/app.py`
- Test: `tests/test_model_client.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Produces: `CodexModelClient(..., effort: str = "high", ...)`
- Produces: Codex command includes `-c model_reasoning_effort="<effort>"`

- [ ] **Step 1: Write failing model-client test**

Rename `test_codex_client_includes_profile_flag_when_configured` to `test_codex_client_includes_effort_and_profile_flags_when_configured`, and construct the client with:

```python
    client = CodexModelClient(
        binary="codex",
        model="default",
        workspace_root=tmp_path,
        effort="xhigh",
        profile="local-dev",
    )
```

Expected command:

```python
    assert client._command() == [
        "codex",
        "exec",
        "--json",
        "-c",
        'approval_policy="never"',
        "-c",
        'model_reasoning_effort="xhigh"',
        "--sandbox",
        "workspace-write",
        "-C",
        str(tmp_path),
        "--skip-git-repo-check",
        "--profile",
        "local-dev",
        "-",
    ]
```

- [ ] **Step 2: Write failing runtime wiring test**

In `tests/test_app.py`, add a focused variant next to `test_chat_passes_codex_profile_from_session_config`:

```python
def test_chat_passes_codex_effort_from_session_config(tmp_path: Path, monkeypatch) -> None:
    captured: list[dict[str, object]] = []

    class FakeCodexModelClient:
        def __init__(self, **kwargs):
            captured.append(kwargs)

        async def complete(self, _messages):
            from personal_agent_gateway.model_client import ModelResponse

            return ModelResponse(content="ok", tool_calls=[])

    monkeypatch.setattr("personal_agent_gateway.runtime_factory.CodexModelClient", FakeCodexModelClient)
    client = TestClient(create_app(make_config(tmp_path)))
    client.cookies.set("agent_session", "test-session")

    client.put(
        "/api/sessions/active/config",
        json={"agent_id": "codex", "model": "gpt-5.5", "options": {"effort": "xhigh"}},
    )
    response = client.post("/api/chat", json={"message": "hello"})

    assert response.status_code == 200
    assert captured[-1]["effort"] == "xhigh"
```

- [ ] **Step 3: Run failing tests**

Run:

```powershell
python -m pytest tests/test_model_client.py::test_codex_client_includes_effort_and_profile_flags_when_configured tests/test_app.py::test_chat_passes_codex_effort_from_session_config -q
```

Expected: failures because `effort` is not accepted/passed.

- [ ] **Step 4: Implement Codex effort support**

In `CodexModelClient.__init__`, add parameter:

```python
        effort: str = "high",
```

Set:

```python
        self._effort = effort
```

In `_command`, after approval policy config, add:

```python
        if self._effort:
            command.extend(["-c", f"model_reasoning_effort={json.dumps(self._effort)}"])
```

In `runtime_factory.py`, pass:

```python
                    effort=str(options.get("effort") or "high"),
```

In `_create_runtime_for_app_config`, pass:

```python
                    effort="high",
```

In `app.py` `_team_model_factory`, pass:

```python
            effort="high",
```

- [ ] **Step 5: Run tests**

Run:

```powershell
python -m pytest tests/test_model_client.py tests/test_app.py::test_chat_passes_codex_effort_from_session_config -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```powershell
git add src/personal_agent_gateway/model_client.py src/personal_agent_gateway/runtime_factory.py src/personal_agent_gateway/app.py tests/test_model_client.py tests/test_app.py
git commit -m "feat(codex): pass reasoning effort to CLI"
```

---

### Task 4: AgentPicker Select-Only Model UI

**Files:**
- Modify: `frontend/src/components/organisms/AgentPicker/AgentPicker.test.jsx`
- Modify only if needed: `frontend/src/components/organisms/AgentPicker/index.jsx`

**Interfaces:**
- Consumes: agent payload field `models: string[]`
- Produces: model select options from `current.models`

- [ ] **Step 1: Write component coverage for curated select**

In the test fixture, update Codex to:

```js
    models: ["default", "gpt-5.5", "gpt-5.4"],
    default_model: "default",
    defaults: { effort: "high", sandbox: "workspace-write", approval_policy: "never" },
    options_schema: [
      { name: "effort", kind: "select", choices: ["low", "medium", "high", "xhigh"] },
      { name: "sandbox", kind: "select", choices: ["read-only", "workspace-write"] },
      { name: "approval_policy", kind: "select", choices: ["never", "on-request"] }
    ]
```

Update Claude to:

```js
    models: ["default", "best", "sonnet", "opus", "haiku", "sonnet[1m]", "opus[1m]", "opusplan"],
    default_model: "sonnet",
```

In the first test, replace the existing model select interaction with:

```js
    expect(screen.getByLabelText("Model").tagName).toBe("SELECT");
    expect(screen.queryByRole("option", { name: "fable" })).not.toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Model"), "gpt-5.5");
    await user.selectOptions(screen.getByLabelText(/effort/i), "xhigh");

    expect(onChange).toHaveBeenLastCalledWith({
      agent_id: "codex",
      model: "gpt-5.5",
      options: { effort: "xhigh" },
      editable: true
    });
```

- [ ] **Step 2: Run component test**

Run:

```powershell
cd frontend
npm test -- AgentPicker.test.jsx --run
```

Expected: pass if `AgentPicker` already renders select-only model UI correctly.

- [ ] **Step 3: Keep production UI select-only**

If Task 4 Step 2 fails because `AgentPicker` is not rendering a select, restore this block in `AgentPicker`:

```jsx
            <InputField
              as="select"
              aria-label="Model"
              value={config.model}
              onChange={(event) => emit({ model: event.target.value })}
            >
              {(current.models || []).map((model) => (
                <option key={model} value={model}>{model}</option>
              ))}
            </InputField>
```

- [ ] **Step 4: Run frontend tests**

Run:

```powershell
cd frontend
npm test -- AgentPicker.test.jsx --run
```

Expected: pass.

- [ ] **Step 5: Commit**

```powershell
git add frontend/src/components/organisms/AgentPicker/index.jsx frontend/src/components/organisms/AgentPicker/AgentPicker.test.jsx
git commit -m "test(ui): keep agent models select-only"
```

---

### Task 5: Verification

**Files:**
- Verify only; no source changes unless a previous task left a regression.

- [ ] **Step 1: Run focused backend suite**

```powershell
python -m pytest tests/test_agents.py tests/test_api_agents.py tests/test_model_client.py -q
```

Expected: pass.

- [ ] **Step 2: Run frontend focused suite**

```powershell
cd frontend
npm test -- AgentPicker.test.jsx client.test.js --run
```

Expected: pass.

- [ ] **Step 3: Run full backend suite**

```powershell
python -m pytest -q
```

Expected: pass.

- [ ] **Step 4: Run frontend build**

```powershell
cd frontend
npm run build
```

Expected: build succeeds.

- [ ] **Step 5: Manual browser check**

Start the app with the existing local run script. In Chat, verify:

- Codex model field is a select, not a free-form input.
- Codex model select includes `default`, `gpt-5.5`, `gpt-5.4`.
- Codex effort select includes `low`, `medium`, `high`, `xhigh`.
- Claude model select does not include `fable`.
- Saving unsupported model names through the API returns `400`.
- Starting a Codex chat with `model=gpt-5.5` and `effort=xhigh` reaches the runtime without API validation failure.

## Self-Review

- Spec coverage: `allow_custom_model` removal is in Global Constraints, Tasks 2 and 4; curated model presets and `fable` removal are Task 1; strict validation is Tasks 1 and 2; Codex effort is Tasks 1 and 3; verification is Task 5.
- Placeholder scan: no `TBD`, vague "handle errors", or unspecified tests remain.
- Type consistency: `effort`, `model_reasoning_effort`, `models`, and `options_schema` names are consistent across backend, API, runtime, and UI.
