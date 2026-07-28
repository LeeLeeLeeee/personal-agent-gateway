# Knowledge Request Chat Marker Hiding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep Knowledge Request creation intact while replacing the internal Chat marker with the exact copy `Library에 요청되었습니다`.

**Architecture:** `ArchiveService` remains the source of truth for parsing and storing requests, and returns the Korean notice for transcript and HTTP responses. The frontend timeline independently normalizes the earlier raw LMG `message.completed` SSE event so the final live bubble matches the stored transcript without changing the LMG event contract.

**Tech Stack:** Python 3.13, FastAPI runtime services, pytest, React 19, Vite 6, Vitest

## Global Constraints

- Preserve Knowledge Request creation and its stored title, reason, outline, and source hints.
- Display the exact copy `Library에 요청되었습니다` once when at least one valid request was created.
- Remove paired `<knowledge_request>...</knowledge_request>` blocks from Chat output.
- Do not change Library request delegation, manual writing, or status transitions.
- Do not change the LMG SSE protocol.

---

### Task 1: Normalize the durable backend response

**Files:**
- Modify: `tests/test_runtime.py:135-195`
- Modify: `tests/test_team_runtime.py:1240-1270`
- Modify: `src/personal_agent_gateway/archive.py:670-695`

**Interfaces:**
- Consumes: `ArchiveService.capture_response_requests(content, persona_id, session_id, team_run_id)`
- Produces: `(clean_content, requests)` where `clean_content` ends with `Library에 요청되었습니다` when `requests` is non-empty

- [ ] **Step 1: Change the runtime contract test to the required copy**

Update the expected assistant message in
`test_runtime_uses_published_archive_and_captures_request_marker`:

```python
assert result.messages == [
    {
        "role": "assistant",
        "content": (
            "I need a reusable rollback guide from you.\n\n"
            "Library에 요청되었습니다"
        ),
    }
]
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_runtime.py::test_runtime_uses_published_archive_and_captures_request_marker -q
```

Expected: FAIL because the actual content still contains
`Knowledge request sent to Library: Rollback guide`.

- [ ] **Step 3: Implement the minimal backend copy change**

In `ArchiveService.capture_response_requests()`, replace the title-bearing
English notice with:

```python
if requests:
    notice = "Library에 요청되었습니다"
    clean = f"{clean}\n\n{notice}" if clean else notice
```

- [ ] **Step 4: Update the Team Runtime expectation**

Change the existing Team Runtime assertion to:

```python
assert "Library에 요청되었습니다" in (task.result or "")
```

Keep the existing assertions that the raw marker is absent and one request is
stored.

- [ ] **Step 5: Run backend regression tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_runtime.py::test_runtime_uses_published_archive_and_captures_request_marker tests/test_team_runtime.py::test_team_runtime_uses_archive_and_routes_knowledge_gap_to_library -q
```

Expected: 2 passed.

- [ ] **Step 6: Commit the backend contract**

```powershell
git add src/personal_agent_gateway/archive.py tests/test_runtime.py tests/test_team_runtime.py
git commit -m "fix: normalize knowledge request chat notice"
```

### Task 2: Normalize raw completed SSE messages

**Files:**
- Modify: `frontend/src/lib/timeline.test.js:240-275`
- Modify: `frontend/src/lib/timeline.js:1-155`

**Interfaces:**
- Consumes: raw `message.completed.text` from LMG
- Produces: `entryFromSse(event).text` without marker blocks and with the same Korean notice when at least one valid marker payload contains non-empty string `title` and `reason`

- [ ] **Step 1: Add a failing completed-message test**

Add this case under `describe("normalized event mapping")`:

```javascript
it("hides a knowledge request marker in a completed message", () => {
  const text = [
    "Reusable guidance is missing.",
    "<knowledge_request>{\"title\":\"Figma guide\",\"reason\":\"No team guide\",",
    "\"suggested_outline\":[\"Pages\"],\"source_hints\":[\"Current file\"]}</knowledge_request>"
  ].join("\n");

  const entry = entryFromSse({ ...base, kind: "message.completed", text });

  expect(entry.text).toBe(
    "Reusable guidance is missing.\n\nLibrary에 요청되었습니다"
  );
  expect(entry.text).not.toContain("<knowledge_request>");
});
```

- [ ] **Step 2: Run the focused frontend test to verify it fails**

Run:

```powershell
npm --prefix frontend test -- src/lib/timeline.test.js
```

Expected: FAIL because `entry.text` still contains the raw marker.

- [ ] **Step 3: Implement the display normalizer**

Add a private paired-marker regex and helper in `timeline.js`:

```javascript
const KNOWLEDGE_REQUEST_PATTERN = (
  /<knowledge_request>\s*(\{.*?\})\s*<\/knowledge_request>/gs
);
const KNOWLEDGE_REQUEST_NOTICE = "Library에 요청되었습니다";

function isValidKnowledgeRequest(raw) {
  try {
    const payload = JSON.parse(raw);
    return payload != null
      && typeof payload === "object"
      && !Array.isArray(payload)
      && typeof payload.title === "string"
      && payload.title.trim().length > 0
      && typeof payload.reason === "string"
      && payload.reason.trim().length > 0;
  } catch (_error) {
    return false;
  }
}

function knowledgeRequestDisplayText(content) {
  const source = String(content || "");
  let matched = false;
  let requested = false;
  const withoutMarkers = source.replace(
    KNOWLEDGE_REQUEST_PATTERN,
    (_marker, raw) => {
      matched = true;
      requested = requested || isValidKnowledgeRequest(raw);
      return "";
    }
  );
  if (!matched) return source;
  const clean = withoutMarkers.trim().replace(/\n{3,}/g, "\n\n");
  if (!requested) return clean;
  return clean
    ? `${clean}\n\n${KNOWLEDGE_REQUEST_NOTICE}`
    : KNOWLEDGE_REQUEST_NOTICE;
}
```

Apply the helper only to the `message.completed` branch:

```javascript
case "message.completed":
  return {
    type: "agent",
    key: `agent:${sid}:${runId}:c${event.event_seq}`,
    text: knowledgeRequestDisplayText(event.text || ""),
    streaming: false,
    time,
    serverOrder: event.event_seq,
    createdAtMs
  };
```

- [ ] **Step 4: Add boundary assertions**

Add tests proving:

- Multiple valid markers produce one notice.
- A paired marker with invalid JSON is hidden but does not claim that a Library
  request was created.
- A normal completed message remains unchanged.

- [ ] **Step 5: Run frontend unit tests and build**

Run:

```powershell
npm --prefix frontend test -- src/lib/timeline.test.js
npm --prefix frontend run build
```

Expected: timeline tests pass and Vite build exits 0.

- [ ] **Step 6: Commit the frontend normalization**

```powershell
git add frontend/src/lib/timeline.js frontend/src/lib/timeline.test.js
git commit -m "fix: hide knowledge request marker in chat"
```

### Task 3: Verify the integrated local runtime

**Files:**
- Runtime build output: `src/personal_agent_gateway/frontend_dist/` (ignored)
- Runtime logs: `data/pag-runtime.out.log`, `data/pag-runtime.err.log`

**Interfaces:**
- Consumes: built frontend assets and the existing PAG `.env`
- Produces: PAG on `127.0.0.1:8787` serving the updated frontend while LMG remains on `127.0.0.1:8788`

- [ ] **Step 1: Run the complete relevant backend suites**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_archive.py tests/test_runtime.py tests/test_team_runtime.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run the complete frontend suite**

Run:

```powershell
npm --prefix frontend test
```

Expected: all Vitest tests pass.

- [ ] **Step 3: Restart only PAG**

Resolve the current PID listening on `127.0.0.1:8787`, stop that exact process,
then start `scripts/run_local.ps1` hidden with the existing runtime log paths.
Do not restart LMG.

```powershell
$listenLine = netstat -ano | Select-String (
  '^\s*TCP\s+127\.0\.0\.1:8787\s+0\.0\.0\.0:0\s+LISTENING\s+(\d+)$'
) | Select-Object -First 1
if (-not $listenLine) { throw "PAG listener not found on port 8787" }
$pagProcessId = [int](
  [regex]::Match($listenLine.Line, 'LISTENING\s+(\d+)$').Groups[1].Value
)
Stop-Process -Id $pagProcessId
Start-Process -FilePath 'powershell.exe' `
  -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
    '.\scripts\run_local.ps1' `
  -WorkingDirectory 'C:\Users\Administrator\playground\personal-agent-gateway' `
  -RedirectStandardOutput '.\data\pag-runtime.out.log' `
  -RedirectStandardError '.\data\pag-runtime.err.log' `
  -WindowStyle Hidden
```

- [ ] **Step 4: Verify service health and built asset selection**

Run HTTP checks for:

```text
GET http://127.0.0.1:8787
GET http://127.0.0.1:8788/livez
```

Expected: both return HTTP 200, and the PAG HTML references a Vite-built
`/assets/` script.

```powershell
$pag = Invoke-WebRequest 'http://127.0.0.1:8787' -UseBasicParsing
$lmg = Invoke-WebRequest 'http://127.0.0.1:8788/livez' -UseBasicParsing
if ($pag.StatusCode -ne 200 -or $pag.Content -notmatch '/assets/') {
  throw "PAG did not serve the Vite build"
}
if ($lmg.StatusCode -ne 200) { throw "LMG is not live" }
```

- [ ] **Step 5: Confirm the final worktree**

Run:

```powershell
git status --short --branch
git log -3 --oneline
```

Expected: the worktree is clean after the implementation plan is committed as
the final documentation checkpoint.
