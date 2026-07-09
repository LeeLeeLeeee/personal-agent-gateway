# Chat Path → Artifact Registration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In the chat transcript, any file path the agent mentions becomes clickable; a "+등록" button copies that file (image/video/audio/document only) into the gateway-managed artifact store, after which it is viewable in the Artifacts screen.

**Architecture:** No auto-detection. The user explicitly registers a path via a button. Backend adds one endpoint (`POST /api/artifacts/register`) that validates the path is inside `workspace_root`, checks the extension against a whitelist, and copies the file via the existing `ArtifactStore.register_existing_file(..., source_session_id=...)`. Frontend linkifies path-like tokens inside `MarkdownContent`, renders a "+등록" button, and calls the endpoint. The Artifacts viewer already exists (`ArtifactsView` + `/api/artifacts/{id}/content`); we only add a "document" type so registered docs show up correctly.

**Tech Stack:** FastAPI/Pydantic + pytest (backend); Vite/React 19 + Vitest/Testing Library (frontend); vanilla CSS in `src/personal_agent_gateway/static/styles.css`.

## Global Constraints

- **Security (non-negotiable):** The register endpoint MUST reject any `path` that does not resolve to a real file *inside* `app_config.workspace_root`. Resolve with `.resolve()` then `.relative_to(workspace_root)`; a `ValueError` → HTTP 400. This mirrors `FfmpegRunner._source_path`. Without this the endpoint is an arbitrary-file-exfiltration hole.
- **Copy, do not move.** Registration copies the file (existing `register_existing_file` uses `shutil.copy2`). The original stays in the workspace.
- **Registrable types (exact whitelist), by extension:**
  - image: `png jpg jpeg webp gif svg bmp`
  - video: `mp4 mov webm mkv avi`
  - audio: `mp3 m4a wav ogg flac`
  - document: `pdf doc docx xls xlsx ppt pptx txt md csv hwp hwpx html htm`
- Non-whitelisted extensions → endpoint returns HTTP 415; frontend does not render a "+등록" button for them.
- **Do not refactor** `job_worker.py`'s existing `_artifact_type`/`_mime_type`. Add a new shared module instead; leave the job path untouched.
- Match the brutalist style: `var(--bd)`, `var(--font-mono)`, existing `.md-code` look for path text.
- Path tokens with spaces are out of scope (documented limitation).

---

### Task 1: Backend — extension whitelist module + register endpoint

**Files:**
- Create: `src/personal_agent_gateway/artifact_types.py`
- Modify: `src/personal_agent_gateway/api/artifacts.py`
- Test: `tests/test_api_artifacts.py` (append), `tests/test_artifact_types.py` (create)

**Interfaces:**
- Consumes: `request.app.state.app_config.workspace_root` (a `Path`), `request.app.state.artifact_store` (`ArtifactStore`), existing `_artifact_payload` in `api/artifacts.py`.
- Produces: `POST /api/artifacts/register` accepting JSON `{ "path": str, "session_id": str | null, "title": str | null }`, returning `{ "artifact": {...} }` (same payload shape as `GET /api/artifacts/{id}`). Helpers `is_registrable(path) -> bool`, `artifact_type_for(path) -> str`, `mime_type_for(path) -> str`.

- [ ] **Step 1: Write the failing test for the whitelist helpers**

Create `tests/test_artifact_types.py`:

```python
from personal_agent_gateway.artifact_types import (
    artifact_type_for,
    is_registrable,
    mime_type_for,
)


def test_is_registrable_accepts_whitelisted_and_rejects_others() -> None:
    assert is_registrable("out/cat.png") is True
    assert is_registrable("clip.MP4") is True
    assert is_registrable("report.hwpx") is True
    assert is_registrable("index.html") is True
    assert is_registrable("script.py") is False
    assert is_registrable("archive.zip") is False
    assert is_registrable("noext") is False


def test_artifact_type_for_maps_by_extension() -> None:
    assert artifact_type_for("a.png") == "image"
    assert artifact_type_for("a.mp4") == "video"
    assert artifact_type_for("a.mp3") == "audio"
    assert artifact_type_for("a.pdf") == "document"
    assert artifact_type_for("a.py") == "other"


def test_mime_type_for_known_and_unknown() -> None:
    assert mime_type_for("a.png") == "image/png"
    assert mime_type_for("a.pdf") == "application/pdf"
    assert mime_type_for("a.html") == "text/html"
    assert mime_type_for("a.py") == "application/octet-stream"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_artifact_types.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'personal_agent_gateway.artifact_types'`

- [ ] **Step 3: Implement the module**

Create `src/personal_agent_gateway/artifact_types.py`:

```python
from pathlib import Path

_IMAGE = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".bmp"}
_VIDEO = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
_AUDIO = {".mp3", ".m4a", ".wav", ".ogg", ".flac"}
_DOCUMENT = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".md", ".csv", ".hwp", ".hwpx", ".html", ".htm",
}

_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".bmp": "image/bmp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".hwp": "application/x-hwp",
    ".hwpx": "application/haansofthwpx",
    ".html": "text/html",
    ".htm": "text/html",
}


def _ext(path: str) -> str:
    return Path(path).suffix.lower()


def is_registrable(path: str) -> bool:
    return _ext(path) in _IMAGE | _VIDEO | _AUDIO | _DOCUMENT


def artifact_type_for(path: str) -> str:
    ext = _ext(path)
    if ext in _IMAGE:
        return "image"
    if ext in _VIDEO:
        return "video"
    if ext in _AUDIO:
        return "audio"
    if ext in _DOCUMENT:
        return "document"
    return "other"


def mime_type_for(path: str) -> str:
    return _MIME.get(_ext(path), "application/octet-stream")
```

- [ ] **Step 4: Run the helper test to verify it passes**

Run: `python -m pytest tests/test_artifact_types.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Write the failing endpoint tests**

Append to `tests/test_api_artifacts.py` (reuse existing `authenticated_client`, `make_config`; note `make_config` creates `tmp_path/"workspace"`):

```python
def test_register_artifact_copies_workspace_file(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    workspace = client.app.state.app_config.workspace_root
    (workspace / "out").mkdir()
    (workspace / "out" / "cat.png").write_bytes(b"img-bytes")

    response = client.post(
        "/api/artifacts/register",
        json={"path": "out/cat.png", "session_id": "sess-1"},
    )

    assert response.status_code == 200
    artifact = response.json()["artifact"]
    assert artifact["type"] == "image"
    assert artifact["title"] == "cat.png"
    assert artifact["source_session_id"] == "sess-1"
    # original stays in the workspace (copy, not move)
    assert (workspace / "out" / "cat.png").exists()
    # content is retrievable through the existing content endpoint
    content = client.get(f"/api/artifacts/{artifact['id']}/content")
    assert content.content == b"img-bytes"


def test_register_artifact_rejects_path_outside_workspace(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"nope")

    response = client.post(
        "/api/artifacts/register",
        json={"path": "../secret.png"},
    )

    assert response.status_code == 400


def test_register_artifact_rejects_unknown_extension(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    workspace = client.app.state.app_config.workspace_root
    (workspace / "script.py").write_text("print('x')")

    response = client.post("/api/artifacts/register", json={"path": "script.py"})

    assert response.status_code == 415


def test_register_artifact_returns_404_for_missing_file(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)

    response = client.post("/api/artifacts/register", json={"path": "gone.png"})

    assert response.status_code == 404


def test_register_artifact_requires_session(tmp_path: Path) -> None:
    client = TestClient(create_app(make_config(tmp_path)))

    response = client.post("/api/artifacts/register", json={"path": "x.png"})

    assert response.status_code == 401
```

- [ ] **Step 6: Run to verify they fail**

Run: `python -m pytest tests/test_api_artifacts.py -q`
Expected: FAIL (new tests 404/405 on unknown route; existing tests still pass)

- [ ] **Step 7: Implement the endpoint**

Modify `src/personal_agent_gateway/api/artifacts.py`. Add imports at the top (keep existing imports):

```python
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel

from personal_agent_gateway.artifact_types import (
    artifact_type_for,
    is_registrable,
    mime_type_for,
)
```

Add the request model just below the `router = APIRouter(...)` line:

```python
class RegisterArtifactRequest(BaseModel):
    path: str
    session_id: str | None = None
    title: str | None = None
```

Add the route (place it BEFORE the `@router.get("/{artifact_id}")` route so `register` is not captured as an `artifact_id`):

```python
@router.post("/register")
def register_artifact(
    request: Request,
    payload: RegisterArtifactRequest,
    _session: None = session_dependency,
) -> dict[str, object]:
    workspace_root = request.app.state.app_config.workspace_root.resolve()
    candidate = (workspace_root / payload.path).resolve()
    try:
        candidate.relative_to(workspace_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Path is outside workspace") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if not is_registrable(candidate.name):
        raise HTTPException(status_code=415, detail="Unsupported file type")
    artifact = request.app.state.artifact_store.register_existing_file(
        artifact_type=artifact_type_for(candidate.name),
        title=payload.title or candidate.name,
        source_path=candidate,
        relative_path=f"files/{uuid4().hex[:8]}/{candidate.name}",
        mime_type=mime_type_for(candidate.name),
        source_session_id=payload.session_id,
    )
    return {"artifact": _artifact_payload(artifact)}
```

Note: joining an absolute `payload.path` with `workspace_root` discards the left side (pathlib), so absolute paths outside the workspace still fail the `relative_to` check — the guard covers both relative `../` traversal and absolute paths.

- [ ] **Step 8: Run the full artifact test suite to verify pass**

Run: `python -m pytest tests/test_api_artifacts.py tests/test_artifact_types.py -q`
Expected: PASS (all)

- [ ] **Step 9: Commit**

```bash
git add src/personal_agent_gateway/artifact_types.py src/personal_agent_gateway/api/artifacts.py tests/test_api_artifacts.py tests/test_artifact_types.py
git commit -m "feat: register workspace file paths as artifacts via /api/artifacts/register"
```

---

### Task 2: Frontend — path detection + "+등록" button in the transcript

**Files:**
- Create: `frontend/src/lib/artifactTypes.js`
- Modify: `frontend/src/api/client.js`
- Modify: `frontend/src/components/organisms/MarkdownContent/index.jsx`
- Modify: `frontend/src/components/organisms/Timeline/index.jsx`
- Modify: `frontend/src/components/organisms/ChatView/index.jsx`
- Modify: `src/personal_agent_gateway/static/styles.css`
- Test: `frontend/src/components/organisms/MarkdownContent/MarkdownContent.test.jsx` (append)

**Interfaces:**
- Consumes: `useToast()` (already imported in MarkdownContent), `api` from `../../../api/client.js`.
- Produces: `api.registerArtifact({ path, session_id })`; `MarkdownContent({ source, sessionId })`; path tokens matching a registrable extension render as a `PathChip` with a "+등록" button. `Timeline({ entries, busy, sessionId })` forwards `sessionId` to agent messages.
- `frontend/src/lib/artifactTypes.js` exports `REGISTRABLE_EXTENSIONS`, `PATH_RE` (global-flagged regex), `isRegistrablePath(text)`.

- [ ] **Step 1: Write the FE whitelist lib**

Create `frontend/src/lib/artifactTypes.js`:

```js
export const REGISTRABLE_EXTENSIONS = [
  "png", "jpg", "jpeg", "webp", "gif", "svg", "bmp",
  "mp4", "mov", "webm", "mkv", "avi",
  "mp3", "m4a", "wav", "ogg", "flac",
  "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "md", "csv", "hwp", "hwpx", "html", "htm"
];

const EXT_ALT = REGISTRABLE_EXTENSIONS.join("|");

// A path-like token: word chars, dots, slashes, backslashes, colon, hyphen, ending in a whitelisted extension.
// Paths containing spaces are intentionally not matched.
export function makePathRe() {
  return new RegExp(`[\\w./\\\\:-]*\\.(?:${EXT_ALT})\\b`, "gi");
}

export function isRegistrablePath(text) {
  return new RegExp(`^[\\w./\\\\:-]+\\.(?:${EXT_ALT})$`, "i").test(String(text || "").trim());
}
```

- [ ] **Step 2: Add the API client method**

In `frontend/src/api/client.js`, add after the `artifactText` method (around line 98), inside the `api` object:

```js
  async registerArtifact(body) {
    return jsonOrNull(await fetch("/api/artifacts/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }));
  },
```

- [ ] **Step 3: Write the failing MarkdownContent test**

Append to `frontend/src/components/organisms/MarkdownContent/MarkdownContent.test.jsx`:

```jsx
import { fireEvent, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { api } from "../../../api/client.js";

describe("MarkdownContent path registration", () => {
  it("renders a +등록 button for a registrable path and registers on click", async () => {
    const spy = vi.spyOn(api, "registerArtifact").mockResolvedValue({ artifact: { id: "a1" } });
    render(<MarkdownContent source={"저장했습니다: `out/cat.png`"} sessionId="sess-9" />);

    const button = screen.getByRole("button", { name: "+등록" });
    expect(button).toBeInTheDocument();

    fireEvent.click(button);
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ path: "out/cat.png", session_id: "sess-9" }));
    spy.mockRestore();
  });

  it("does not render a +등록 button for a non-registrable path", () => {
    render(<MarkdownContent source={"실행했습니다: `scripts/run.py`"} />);
    expect(screen.queryByRole("button", { name: "+등록" })).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 4: Run to verify it fails**

Run (from `frontend/`): `npx vitest run src/components/organisms/MarkdownContent`
Expected: FAIL (no "+등록" button rendered)

- [ ] **Step 5: Implement path detection in MarkdownContent**

In `frontend/src/components/organisms/MarkdownContent/index.jsx`:

Update the React import to add `createContext` and `useContext` (the file already imports `useEffect`, `useState`), and add lib + api imports:

```jsx
import { createContext, useContext, useEffect, useState } from "react";
import { api } from "../../../api/client.js";
import { useToast } from "../../providers/UiProvider/index.jsx";
import { isRegistrablePath, makePathRe } from "../../../lib/artifactTypes.js";
```

Add the session-id context and the `PathChip` component near the top (after the imports, before `hashStr`):

```jsx
const SessionIdContext = createContext(null);

function PathChip({ path }) {
  const sessionId = useContext(SessionIdContext);
  const toast = useToast();
  const [state, setState] = useState("idle"); // idle | saving | done

  async function register() {
    if (state !== "idle") return;
    setState("saving");
    const result = await api.registerArtifact({ path, session_id: sessionId });
    if (result) {
      setState("done");
      toast("아티팩트로 등록되었습니다", "success");
    } else {
      setState("idle");
      toast("등록에 실패했습니다", "error");
    }
  }

  return (
    <span className="path-chip">
      <code className="md-code path-chip-text">{path}</code>
      <button
        type="button"
        className="path-chip-add"
        onClick={register}
        disabled={state !== "idle"}
      >
        {state === "done" ? "등록됨" : state === "saving" ? "등록 중…" : "+등록"}
      </button>
    </span>
  );
}

// Split a plain-text segment into strings and <PathChip> nodes for registrable paths.
function splitPaths(text, keyPrefix) {
  const re = makePathRe();
  const out = [];
  let last = 0;
  let match;
  while ((match = re.exec(text))) {
    if (match.index > last) out.push(text.slice(last, match.index));
    out.push(<PathChip key={`${keyPrefix}-path-${match.index}`} path={match[0]} />);
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
```

Change `inlineNodes` so plain-text pieces run through `splitPaths`, and a code span whose whole content is a registrable path becomes a `PathChip`:

```jsx
function inlineNodes(text) {
  const out = [];
  const re = /`([^`]+)`|\*\*([^*]+)\*\*|\*([^*\n]+)\*|\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g;
  let last = 0;
  let match;
  while ((match = re.exec(text))) {
    if (match.index > last) out.push(...splitPaths(text.slice(last, match.index), `${match.index}-pre`));
    if (match[1] !== undefined) {
      out.push(
        isRegistrablePath(match[1])
          ? <PathChip key={`${match.index}-path`} path={match[1].trim()} />
          : <code className="md-code" key={`${match.index}-code`}>{match[1]}</code>
      );
    } else if (match[2] !== undefined) out.push(<strong key={`${match.index}-strong`}>{match[2]}</strong>);
    else if (match[3] !== undefined) out.push(<em key={`${match.index}-em`}>{match[3]}</em>);
    else out.push(<a key={`${match.index}-link`} href={match[5]} target="_blank" rel="noopener noreferrer">{match[4]}</a>);
    last = re.lastIndex;
  }
  if (last < text.length) out.push(...splitPaths(text.slice(last), "tail"));
  return out;
}
```

Update the `MarkdownContent` signature and wrap its output in the provider:

```jsx
export function MarkdownContent({ source, sessionId = null }) {
  const lines = String(source || "").replace(/\r\n/g, "\n").split("\n");
  // ... existing body unchanged, building `nodes` ...
  return (
    <SessionIdContext.Provider value={sessionId}>
      <div className="md">{nodes}</div>
    </SessionIdContext.Provider>
  );
}
```

(Only the `return` line changes — wrap the existing `<div className="md">{nodes}</div>` in the provider.)

- [ ] **Step 6: Run the MarkdownContent tests to verify pass**

Run (from `frontend/`): `npx vitest run src/components/organisms/MarkdownContent`
Expected: PASS (original render test + 2 new path tests)

- [ ] **Step 7: Thread sessionId through Timeline and ChatView**

In `frontend/src/components/organisms/Timeline/index.jsx`:

Change `AgentMessage` to accept and pass `sessionId`:

```jsx
function AgentMessage({ entry, sessionId }) {
  const label = entry.streaming ? "AGENT RESPONSE" : "FINAL ANSWER";
  return (
    <div className={`msg-agent${entry.streaming ? " msg-agent-streaming" : " msg-agent-final"}`}>
      <div className="msg-agent-head">
        <span>{label}</span>
        {entry.time ? <span>{entry.time}</span> : null}
      </div>
      <div className="bubble">
        <MarkdownContent source={entry.text || ""} sessionId={sessionId} />
        {entry.streaming ? <span className="agent-cursor" /> : null}
      </div>
    </div>
  );
}
```

Change `Timeline` signature to accept `sessionId` and pass it where `AgentMessage` is created:

```jsx
export function Timeline({ entries, busy, sessionId = null }) {
```

and update the agent line inside the loop:

```jsx
    if (entry.type === "agent") nodes.push(<AgentMessage key={`a-${nodes.length}`} entry={entry} sessionId={sessionId} />);
```

In `frontend/src/components/organisms/ChatView/index.jsx`, pass the already-computed `activeSessionId` (line 93) to Timeline (line 127):

```jsx
          <Timeline entries={entries} busy={busy} sessionId={activeSessionId} />
```

- [ ] **Step 8: Add styles**

In `src/personal_agent_gateway/static/styles.css`, add near the `.md code.md-code` rule:

```css
.path-chip{display:inline-flex;align-items:center;gap:4px;vertical-align:baseline}
.path-chip-add{font-family:var(--font-body);font-weight:700;font-size:10px;letter-spacing:.3px;background:var(--c-black);color:var(--c-white);border:var(--bd-sm);padding:1px 6px;cursor:pointer;line-height:1.5;white-space:nowrap}
.path-chip-add:hover:not(:disabled){background:var(--c-white);color:var(--c-black)}
.path-chip-add:disabled{opacity:.55;cursor:default}
```

(If `--bd-sm` is not defined in this file, use `1px solid var(--c-black)`.)

- [ ] **Step 9: Run the frontend suite + build**

Run (from `frontend/`): `npx vitest run` then `npm run build`
Expected: all tests PASS; build succeeds.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/lib/artifactTypes.js frontend/src/api/client.js frontend/src/components/organisms/MarkdownContent/index.jsx frontend/src/components/organisms/MarkdownContent/MarkdownContent.test.jsx frontend/src/components/organisms/Timeline/index.jsx frontend/src/components/organisms/ChatView/index.jsx src/personal_agent_gateway/static/styles.css
git commit -m "feat: clickable transcript paths with +등록 to register artifacts"
```

---

### Task 3: Frontend — Artifacts viewer "document" support

**Files:**
- Modify: `frontend/src/components/organisms/ArtifactsView/index.jsx`
- Modify: `src/personal_agent_gateway/static/styles.css`
- Test: `frontend/src/components/organisms/ArtifactsView/ArtifactsView.test.jsx` (append)

**Interfaces:**
- Consumes: artifact objects whose `type` may now be `"document"` (from Task 1). No API change.
- Produces: a "Documents" filter chip, a document glyph, and a drawer preview (PDF inline via `<iframe>`; other documents show a download-oriented card).

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/components/organisms/ArtifactsView/ArtifactsView.test.jsx`:

```jsx
it("shows documents under the Documents filter", () => {
  render(<ArtifactsView artifacts={[
    { id: "d1", type: "document", title: "spec.pdf", relative_path: "files/x/spec.pdf", mime_type: "application/pdf", size_bytes: 2048, created_at: "2026-07-09T00:00:00Z" }
  ]} />);
  fireEvent.click(screen.getByRole("button", { name: "Documents" }));
  expect(screen.getByRole("button", { name: "Open spec.pdf" })).toBeInTheDocument();
});
```

(Ensure `fireEvent` is imported in this test file; add it to the existing `@testing-library/react` import if missing.)

- [ ] **Step 2: Run to verify it fails**

Run (from `frontend/`): `npx vitest run src/components/organisms/ArtifactsView`
Expected: FAIL (no "Documents" filter button)

- [ ] **Step 3: Implement document support**

In `frontend/src/components/organisms/ArtifactsView/index.jsx`:

Add the filter and glyph:

```jsx
const TYPE_FILTERS = [
  ["all", "All"],
  ["image", "Images"],
  ["video", "Videos"],
  ["audio", "Audio"],
  ["document", "Documents"],
  ["log", "Logs"],
  ["report", "Reports"],
  ["archive", "Archives"]
];

const GLYPH = { image: "▦", video: "▶", audio: "♪", document: "▤", log: "≣", report: "¶", archive: "◫" };
```

In `ArtifactPreview`, add a document branch before the final archive fallback:

```jsx
  if (artifact.type === "document") {
    if (artifact.mime_type === "application/pdf") {
      return <iframe className="artifact-preview-doc" src={contentUrl} title={artifact.title} />;
    }
    return (
      <div className="artifact-preview-archive">
        <span className="artifact-preview-archive-glyph" aria-hidden="true">{GLYPH.document}</span>
        <span className="mono">{fmtSize(artifact.size_bytes)}</span>
      </div>
    );
  }
```

- [ ] **Step 4: Add style for the pdf preview**

In `src/personal_agent_gateway/static/styles.css`, near the other `.artifact-preview-*` rules:

```css
.artifact-preview-doc{width:100%;height:420px;border:var(--bd);background:var(--c-white)}
```

- [ ] **Step 5: Run to verify pass**

Run (from `frontend/`): `npx vitest run src/components/organisms/ArtifactsView`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/organisms/ArtifactsView/index.jsx frontend/src/components/organisms/ArtifactsView/ArtifactsView.test.jsx src/personal_agent_gateway/static/styles.css
git commit -m "feat: document artifact type in Artifacts viewer (filter + pdf preview)"
```

---

## Self-Review

- **Spec coverage:** all-paths detection (Task 2 `splitPaths` + code-span check) ✅; register only image/video/audio/document incl. html (Task 1 whitelist + Task 2 lib mirror) ✅; "+등록" button not right-click (Task 2 `PathChip`) ✅; copy not move (`register_existing_file`, test asserts original remains) ✅; workspace containment security (Task 1 `relative_to` guard + test) ✅; viewable in project (existing viewer + Task 3 document type) ✅.
- **Type consistency:** backend `artifact_type_for` returns `image|video|audio|document|other`; FE `GLYPH`/`TYPE_FILTERS` include `document`; `PathChip` path prop is a trimmed string in both call sites.
- **Placeholder scan:** every code step contains full code; no TBD/TODO.
- **Known limitation (documented):** paths containing spaces are not detected; non-PDF documents preview as a download card only.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-09-chat-path-artifact-registration.md`.
