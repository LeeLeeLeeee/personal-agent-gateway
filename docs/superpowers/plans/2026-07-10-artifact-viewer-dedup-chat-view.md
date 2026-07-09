# Artifact Viewer + Dedup + Chat "보기" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the artifact experience: grid cards show real image thumbnails; the detail view becomes a centered modal with a zoom/pan image viewer and a delete action; provenance text no longer overflows; the same path cannot be registered twice (delete to re-register); and the chat transcript shows a "보기" button once a path is registered, opening the same viewer inline.

**Architecture:** Backend stores the resolved source path in the artifact's existing `metadata_json` (no schema migration) so registration can dedup by source path (HTTP 409 returning the existing artifact) and a new `DELETE /api/artifacts/{id}` removes the stored file + row. Frontend introduces one reusable centered `ArtifactModal` (with an image zoom/pan viewer) used by both the Artifacts screen and the chat transcript; a `registeredByPath` map is threaded from GatewayApp into the transcript so `PathChip` renders "보기" vs "+등록".

**Tech Stack:** FastAPI/Pydantic + pytest (backend); Vite/React 19 + Vitest/Testing Library (frontend); vanilla CSS in `src/personal_agent_gateway/static/styles.css`.

## Global Constraints

- **No external dependencies** (strict, matches project): zoom/pan must be hand-rolled (wheel + drag), no image-viewer libraries.
- **Dedup key = resolved absolute source path**, stored in the artifact's `metadata_json` as `{"source_path": <resolved abs>, "original_path": <payload.path as given>}`. `register_existing_file` already accepts a `metadata` dict — no schema migration.
- **Duplicate registration → HTTP 409**, body `{"detail": {"message": "...", "artifact": <existing payload>}}`, so the client can switch to "보기" using the existing artifact.
- **Delete removes the stored file** (and thumbnail if present) **and the DB row**; deleting frees the source path for re-registration.
- **One reusable centered modal** (`ArtifactModal`) replaces the right-side `ArtifactDrawer`; it is used by both `ArtifactsView` and the chat `PathChip`'s "보기".
- Localhost personal tool: registration is allowed from any readable path (workspace boundary already removed — do not reintroduce it).
- Match brutalist style: `var(--bd)`, `var(--font-mono)`, existing `.btn`/`.chip` classes; provenance values must wrap (`word-break: break-all`), never clip.

---

### Task 1: Backend — dedup on register + delete endpoint

**Files:**
- Modify: `src/personal_agent_gateway/artifacts.py` (add `find_by_source_path`, `delete`)
- Modify: `src/personal_agent_gateway/api/artifacts.py` (dedup in register; new DELETE route)
- Test: `tests/test_artifacts.py` (append), `tests/test_api_artifacts.py` (append)

**Interfaces:**
- Consumes: existing `ArtifactStore` (`register_existing_file(..., metadata=...)`, `get`, `list`, `_db`, `_root`), `Artifact` dataclass (has `metadata: dict`), `_artifact_payload` in the API module.
- Produces:
  - `ArtifactStore.find_by_source_path(source_path: str) -> Artifact | None` — returns the artifact whose `metadata["source_path"]` equals the given resolved path, else None.
  - `ArtifactStore.delete(artifact_id: str) -> None` — deletes the stored file (and thumbnail if any) then the row; raises `KeyError` if the id is unknown.
  - `POST /api/artifacts/register` now stores `metadata={"source_path","original_path"}` and returns 409 (with existing artifact) if the resolved source path is already registered.
  - `DELETE /api/artifacts/{artifact_id}` → `{"deleted": true}` (404 if unknown).

- [ ] **Step 1: Write failing store tests**

Append to `tests/test_artifacts.py` (it already constructs an `ArtifactStore`; follow the existing setup in that file — an `ArtifactStore(db, root)` with a temp root):

```python
def test_find_by_source_path_and_delete(tmp_path: Path) -> None:
    from personal_agent_gateway.artifacts import ArtifactStore
    from personal_agent_gateway.db import Database

    db = Database(tmp_path / "app.sqlite")
    store = ArtifactStore(db, tmp_path / "artifacts")
    src = tmp_path / "cat.png"
    src.write_bytes(b"img")

    created = store.register_existing_file(
        artifact_type="image",
        title="cat.png",
        source_path=src,
        relative_path="files/aa/cat.png",
        mime_type="image/png",
        metadata={"source_path": str(src.resolve()), "original_path": "cat.png"},
    )

    assert store.find_by_source_path(str(src.resolve())).id == created.id
    assert store.find_by_source_path(str(tmp_path / "other.png")) is None

    stored = store.content_path(created.id)
    assert stored.exists()
    store.delete(created.id)
    assert not stored.exists()
    import pytest
    with pytest.raises(KeyError):
        store.get(created.id)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_artifacts.py::test_find_by_source_path_and_delete -q`
Expected: FAIL with `AttributeError: 'ArtifactStore' object has no attribute 'find_by_source_path'`

- [ ] **Step 3: Implement store methods**

In `src/personal_agent_gateway/artifacts.py`, add to `ArtifactStore` (near `get`/`list`):

```python
    def find_by_source_path(self, source_path: str) -> Artifact | None:
        for artifact in self.list():
            if artifact.metadata.get("source_path") == source_path:
                return artifact
        return None

    def delete(self, artifact_id: str) -> None:
        artifact = self.get(artifact_id)  # raises KeyError if unknown
        for path in (artifact.file_path, artifact.thumbnail_path):
            if path is None:
                continue
            try:
                stored = self._stored_path(path)
            except ArtifactPathError:
                continue
            stored.unlink(missing_ok=True)
        self._db.execute("delete from artifacts where id = ?", (artifact_id,))
```

- [ ] **Step 4: Run store tests to verify pass**

Run: `python -m pytest tests/test_artifacts.py -q`
Expected: PASS

- [ ] **Step 5: Write failing API tests (dedup + delete)**

Append to `tests/test_api_artifacts.py` (reuse `authenticated_client`, `make_config`; `make_config` creates `tmp_path/"workspace"`):

```python
def test_register_artifact_rejects_duplicate_source_path(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    workspace = client.app.state.app_config.workspace_root
    (workspace / "dup.png").write_bytes(b"img")

    first = client.post("/api/artifacts/register", json={"path": "dup.png"})
    assert first.status_code == 200
    first_id = first.json()["artifact"]["id"]

    second = client.post("/api/artifacts/register", json={"path": "dup.png"})
    assert second.status_code == 409
    assert second.json()["detail"]["artifact"]["id"] == first_id


def test_delete_artifact_removes_it_and_frees_reregistration(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    workspace = client.app.state.app_config.workspace_root
    (workspace / "dup.png").write_bytes(b"img")

    created = client.post("/api/artifacts/register", json={"path": "dup.png"}).json()["artifact"]

    deleted = client.delete(f"/api/artifacts/{created['id']}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}
    assert client.get(f"/api/artifacts/{created['id']}").status_code == 404

    # source path is free again
    again = client.post("/api/artifacts/register", json={"path": "dup.png"})
    assert again.status_code == 200


def test_delete_artifact_returns_404_for_unknown_id(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    assert client.delete("/api/artifacts/missing").status_code == 404
```

Note: `GET /api/artifacts/{id}` currently returns the artifact and will raise `KeyError` after delete — confirm it maps to 404. If `get_artifact` does not already catch `KeyError`, wrap it (see Step 6).

- [ ] **Step 6: Run to verify they fail**

Run: `python -m pytest tests/test_api_artifacts.py -q`
Expected: FAIL (409/DELETE routes not present)

- [ ] **Step 7: Implement dedup + delete in the API**

In `src/personal_agent_gateway/api/artifacts.py`, update `register_artifact` (after the `is_registrable` check, before calling the store):

```python
    if not is_registrable(candidate.name):
        raise HTTPException(status_code=415, detail="Unsupported file type")
    source_path = str(candidate)
    existing = request.app.state.artifact_store.find_by_source_path(source_path)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={"message": "Already registered", "artifact": _artifact_payload(existing)},
        )
    artifact = request.app.state.artifact_store.register_existing_file(
        artifact_type=artifact_type_for(candidate.name),
        title=payload.title or candidate.name,
        source_path=candidate,
        relative_path=f"files/{uuid4().hex[:8]}/{candidate.name}",
        mime_type=mime_type_for(candidate.name),
        source_session_id=payload.session_id,
        metadata={"source_path": source_path, "original_path": payload.path},
    )
    return {"artifact": _artifact_payload(artifact)}
```

Add the DELETE route (place after `get_artifact` or near the other routes):

```python
@router.delete("/{artifact_id}")
def delete_artifact(
    request: Request,
    artifact_id: str,
    _session: None = session_dependency,
) -> dict[str, object]:
    try:
        request.app.state.artifact_store.delete(artifact_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Artifact not found") from exc
    return {"deleted": True}
```

Ensure `get_artifact` returns 404 after delete — if it does not already, wrap its body:

```python
@router.get("/{artifact_id}")
def get_artifact(request: Request, artifact_id: str, _session: None = session_dependency) -> dict[str, object]:
    try:
        return {"artifact": _artifact_payload(request.app.state.artifact_store.get(artifact_id))}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Artifact not found") from exc
```

- [ ] **Step 8: Run the full artifact suites to verify pass**

Run: `python -m pytest tests/test_artifacts.py tests/test_api_artifacts.py -q`
Expected: PASS (all)

- [ ] **Step 9: Commit**

```bash
git add src/personal_agent_gateway/artifacts.py src/personal_agent_gateway/api/artifacts.py tests/test_artifacts.py tests/test_api_artifacts.py
git commit -m "feat: dedup artifact registration by source path + delete endpoint"
```

---

### Task 2: Frontend — centered ArtifactModal (zoom/pan), grid thumbnails, delete, overflow fix

**Files:**
- Create: `frontend/src/components/organisms/ArtifactModal/index.jsx`
- Modify: `frontend/src/components/organisms/ArtifactsView/index.jsx`
- Modify: `frontend/src/api/client.js`
- Modify: `src/personal_agent_gateway/static/styles.css`
- Test: `frontend/src/components/organisms/ArtifactsView/ArtifactsView.test.jsx` (append)

**Interfaces:**
- Consumes: `useConfirm`/`useToast` from `../../providers/UiProvider/index.jsx`, `api`.
- Produces:
  - `ArtifactModal({ artifact, onClose, onDeleted })` — centered overlay; image type gets zoom (wheel + buttons) and pan (drag); provenance rows wrap; DELETE button (confirm → `api.deleteArtifact` → `onDeleted(id)`), Download, Copy path.
  - `api.deleteArtifact(id)` → boolean; `api.registerArtifact(body)` returns `{ status, ok, data }` (so 409's existing artifact is readable).
  - `ArtifactsView` grid image cards render a real `<img>` thumbnail and open `ArtifactModal`; deleting refetches.

- [ ] **Step 1: Extend the API client**

In `frontend/src/api/client.js`:

Replace the existing `registerArtifact` with a status-aware version:

```js
  async registerArtifact(body) {
    const res = await fetch("/api/artifacts/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    const data = (res.ok || res.status === 409) ? await res.json().catch(() => null) : null;
    return { status: res.status, ok: res.ok, data };
  },
  async deleteArtifact(id) {
    const res = await fetch(`/api/artifacts/${encodeURIComponent(id)}`, { method: "DELETE" });
    return res.ok;
  },
```

(Note: `PathChip` in Task 3 depends on this new `registerArtifact` return shape.)

- [ ] **Step 2: Write the ArtifactModal component**

Create `frontend/src/components/organisms/ArtifactModal/index.jsx`:

```jsx
import { useEffect, useRef, useState } from "react";
import { api } from "../../../api/client.js";
import { useConfirm, useToast } from "../../providers/UiProvider/index.jsx";

const GLYPH = { image: "▦", video: "▶", audio: "♪", document: "▤", log: "≣", report: "¶", archive: "◫" };

function fmtSize(bytes) {
  if (!bytes) return "0 KB";
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function ImageViewer({ src, alt }) {
  const [scale, setScale] = useState(1);
  const [pos, setPos] = useState({ x: 0, y: 0 });
  const drag = useRef(null);

  function reset() { setScale(1); setPos({ x: 0, y: 0 }); }
  function zoom(delta) { setScale((s) => Math.min(8, Math.max(1, +(s + delta).toFixed(2)))); }

  function onWheel(e) {
    e.preventDefault();
    zoom(e.deltaY < 0 ? 0.2 : -0.2);
  }
  function onDown(e) { drag.current = { x: e.clientX - pos.x, y: e.clientY - pos.y }; }
  function onMove(e) {
    if (!drag.current) return;
    setPos({ x: e.clientX - drag.current.x, y: e.clientY - drag.current.y });
  }
  function onUp() { drag.current = null; }

  return (
    <div className="viewer">
      <div
        className="viewer-stage"
        onWheel={onWheel}
        onMouseDown={onDown}
        onMouseMove={onMove}
        onMouseUp={onUp}
        onMouseLeave={onUp}
        style={{ cursor: scale > 1 ? "grab" : "default" }}
      >
        <img
          className="viewer-img"
          src={src}
          alt={alt}
          draggable="false"
          style={{ transform: `translate(${pos.x}px, ${pos.y}px) scale(${scale})` }}
        />
      </div>
      <div className="viewer-controls">
        <button type="button" onClick={() => zoom(-0.2)} aria-label="Zoom out">−</button>
        <span className="mono">{Math.round(scale * 100)}%</span>
        <button type="button" onClick={() => zoom(0.2)} aria-label="Zoom in">+</button>
        <button type="button" onClick={reset} aria-label="Reset">RESET</button>
      </div>
    </div>
  );
}

function Preview({ artifact }) {
  const contentUrl = api.artifactContentUrl(artifact.id);
  const [text, setText] = useState("");

  useEffect(() => {
    if (artifact.type !== "log" && artifact.type !== "report") return undefined;
    let alive = true;
    api.artifactText(artifact.id).then((v) => { if (alive) setText(v); });
    return () => { alive = false; };
  }, [artifact.id, artifact.type]);

  if (artifact.type === "image") return <ImageViewer src={contentUrl} alt={artifact.title} />;
  if (artifact.type === "video") return <video className="modal-media" controls src={contentUrl} />;
  if (artifact.type === "audio") return <audio className="modal-media" controls src={contentUrl} />;
  if (artifact.type === "log" || artifact.type === "report") return <pre className="mono modal-text">{text}</pre>;
  if (artifact.type === "document" && artifact.mime_type === "application/pdf") {
    return <iframe className="modal-doc" src={contentUrl} title={artifact.title} />;
  }
  return (
    <div className="modal-fallback">
      <span aria-hidden="true">{GLYPH[artifact.type] || "◫"}</span>
      <span className="mono">{fmtSize(artifact.size_bytes)}</span>
    </div>
  );
}

export function ArtifactModal({ artifact, onClose, onDeleted }) {
  const toast = useToast();
  const confirm = useConfirm();
  const contentUrl = api.artifactContentUrl(artifact.id);

  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function handleCopyPath() {
    await navigator.clipboard.writeText(artifact.relative_path);
    toast("경로가 복사되었습니다", "success");
  }

  async function handleDelete() {
    const ok = await confirm({
      title: "DELETE ARTIFACT",
      message: `"${artifact.title}" 를 삭제할까요? 삭제하면 다시 등록할 수 있습니다.`,
      confirmLabel: "Delete",
      danger: true
    });
    if (!ok) return;
    if (await api.deleteArtifact(artifact.id)) {
      toast("삭제되었습니다", "success");
      onDeleted?.(artifact.id);
      onClose();
    } else {
      toast("삭제에 실패했습니다", "error");
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" role="dialog" aria-modal="true" aria-label={artifact.title} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="mono">ARTIFACT · {artifact.type}</span>
          <button type="button" className="modal-close" aria-label="Close" onClick={onClose}>✕</button>
        </div>
        <div className="modal-preview"><Preview artifact={artifact} /></div>
        <div className="modal-title">{artifact.title}</div>
        <div className="settings-block modal-provenance">
          <div className="settings-row"><span className="settings-k mono">PATH</span><span className="settings-v mono modal-v">{artifact.relative_path}</span></div>
          <div className="settings-row"><span className="settings-k mono">SIZE</span><span className="settings-v mono modal-v">{fmtSize(artifact.size_bytes)} · {artifact.mime_type}</span></div>
          <div className="settings-row"><span className="settings-k mono">SESSION</span><span className="settings-v mono modal-v">{artifact.source_session_id || "-"}</span></div>
        </div>
        <div className="modal-actions">
          <a className="btn btn-primary btn-sm" href={contentUrl} download>Download</a>
          <button type="button" className="btn btn-sm" onClick={handleCopyPath}>Copy path</button>
          <button type="button" className="btn btn-sm btn-danger" onClick={handleDelete}>Delete</button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Write the failing ArtifactsView test**

Append to `frontend/src/components/organisms/ArtifactsView/ArtifactsView.test.jsx` (ensure `fireEvent` and `waitFor` are imported):

```jsx
it("renders an image thumbnail in the grid card", () => {
  render(<ArtifactsView artifacts={[
    { id: "i1", type: "image", title: "cat.png", relative_path: "files/x/cat.png", mime_type: "image/png", size_bytes: 2048, created_at: "2026-07-10T00:00:00Z" }
  ]} />);
  const img = screen.getByAltText("cat.png");
  expect(img).toHaveAttribute("src", "/api/artifacts/i1/content");
});

it("opens a centered modal (dialog) when a card is clicked", () => {
  render(<ArtifactsView artifacts={[
    { id: "i1", type: "image", title: "cat.png", relative_path: "files/x/cat.png", mime_type: "image/png", size_bytes: 2048, created_at: "2026-07-10T00:00:00Z" }
  ]} />);
  fireEvent.click(screen.getByRole("button", { name: "Open cat.png" }));
  expect(screen.getByRole("dialog", { name: "cat.png" })).toBeInTheDocument();
});
```

- [ ] **Step 4: Run to verify it fails**

Run (from `frontend/`): `npx vitest run src/components/organisms/ArtifactsView`
Expected: FAIL (no thumbnail img; no dialog role)

- [ ] **Step 5: Update ArtifactsView — grid thumbnail + centered modal + delete refresh**

In `frontend/src/components/organisms/ArtifactsView/index.jsx`:

- Replace the `import` line to add the modal, and drop the now-unused `useToast` if only the drawer used it:

```jsx
import { useState } from "react";
import { api } from "../../../api/client.js";
import { ArtifactModal } from "../ArtifactModal/index.jsx";
```

- Delete the old `ArtifactPreview` and `ArtifactDrawer` functions (the modal owns preview now).
- In the grid card thumb, render an image for image-type artifacts:

```jsx
                <div className="artifact-card-thumb">
                  {a.type === "image" ? (
                    <img
                      className="artifact-card-img"
                      src={api.artifactContentUrl(a.id)}
                      alt={a.title}
                      onError={(e) => { e.currentTarget.style.display = "none"; }}
                    />
                  ) : (
                    <span className="artifact-card-glyph" aria-hidden="true">{GLYPH[a.type] || "◫"}</span>
                  )}
                  <span className="artifact-card-type mono">{a.type}</span>
                </div>
```

- Change the component body to accept an `onChange` refetch and use the modal:

```jsx
export function ArtifactsView({ artifacts = [], onChange }) {
  const [type, setType] = useState("all");
  const [selectedId, setSelectedId] = useState(null);

  const grid = artifacts.filter((a) => type === "all" || a.type === type);
  const selected = artifacts.find((a) => a.id === selectedId) || null;

  return (
    <div className="artifacts-view">
      {/* ...existing header/filters/grid... */}
      {selected ? (
        <ArtifactModal
          artifact={selected}
          onClose={() => setSelectedId(null)}
          onDeleted={() => { setSelectedId(null); onChange?.(); }}
        />
      ) : null}
    </div>
  );
}
```

- In `frontend/src/components/containers/GatewayApp/index.jsx`, pass a refetch to the view (find the `<ArtifactsView artifacts={artifacts} />` render at the `screen === "artifacts"` branch):

```jsx
          <ArtifactsView artifacts={artifacts} onChange={() => api.artifacts().then(setArtifacts)} />
```

- [ ] **Step 6: Add styles**

In `src/personal_agent_gateway/static/styles.css` add (centered modal, viewer, overflow fix, card image):

```css
/* centered artifact modal */
.modal-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;z-index:60;padding:24px}
.modal-card{background:var(--c-white);border:var(--bd);width:min(760px,96vw);max-height:92vh;overflow:auto;display:flex;flex-direction:column}
.modal-head{display:flex;align-items:center;justify-content:space-between;background:var(--c-black);color:var(--c-white);padding:8px 12px}
.modal-close{background:transparent;border:none;color:var(--c-white);font-size:16px;cursor:pointer;line-height:1}
.modal-preview{padding:14px;border-bottom:var(--bd-in)}
.modal-title{font-family:var(--font-mono);font-weight:700;font-size:13px;padding:10px 14px 0;word-break:break-all}
.modal-provenance{margin:12px 14px}
.modal-v{word-break:break-all;white-space:normal;text-align:right}
.modal-actions{display:flex;gap:8px;padding:0 14px 16px;flex-wrap:wrap}
.btn-danger{border-color:var(--c-danger);color:var(--c-danger)}
.btn-danger:hover{background:var(--c-danger);color:var(--c-white)}
.modal-media{max-width:100%;display:block;margin:0 auto}
.modal-doc{width:100%;height:60vh;border:var(--bd)}
.modal-text{max-height:60vh;overflow:auto;font-size:12px}
.modal-fallback{display:flex;flex-direction:column;align-items:center;gap:8px;padding:32px;font-size:32px}
/* image zoom/pan viewer */
.viewer{display:flex;flex-direction:column;gap:8px}
.viewer-stage{overflow:hidden;background:#111;border:var(--bd);height:56vh;display:flex;align-items:center;justify-content:center}
.viewer-img{max-width:100%;max-height:100%;transform-origin:center center;user-select:none;-webkit-user-drag:none}
.viewer-controls{display:flex;align-items:center;gap:8px;justify-content:center}
.viewer-controls button{font-family:var(--font-mono);border:var(--bd-sm);background:var(--c-white);cursor:pointer;padding:2px 10px;line-height:1.4}
.viewer-controls button:hover{background:var(--c-black);color:var(--c-white)}
/* grid card image thumbnail */
.artifact-card-img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
```

Note: `.artifact-card-thumb` must be `position:relative` for the absolute image to fill it — verify that rule exists; if not, add `.artifact-card-thumb{position:relative}`.

- [ ] **Step 7: Run ArtifactsView tests + full suite + build**

Run (from `frontend/`): `npx vitest run src/components/organisms/ArtifactsView` then `npx vitest run` then `npm run build`
Expected: target tests PASS, full suite PASS, build succeeds.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/organisms/ArtifactModal/index.jsx frontend/src/components/organisms/ArtifactsView/index.jsx frontend/src/api/client.js frontend/src/components/containers/GatewayApp/index.jsx src/personal_agent_gateway/static/styles.css
git commit -m "feat: centered artifact modal with zoom/pan, grid thumbnails, delete, overflow fix"
```

---

### Task 3: Frontend — chat "보기" button + dedup-aware PathChip

**Files:**
- Modify: `frontend/src/components/organisms/MarkdownContent/index.jsx`
- Modify: `frontend/src/components/organisms/Timeline/index.jsx`
- Modify: `frontend/src/components/organisms/ChatView/index.jsx`
- Modify: `frontend/src/components/containers/GatewayApp/index.jsx`
- Test: `frontend/src/components/organisms/MarkdownContent/MarkdownContent.test.jsx` (append)

**Interfaces:**
- Consumes: `ArtifactModal` (Task 2), `api.registerArtifact` new `{status, ok, data}` shape (Task 2), `api.artifacts()`.
- Produces: `PathChip` shows "보기" when its path is already registered (opens `ArtifactModal` inline) and "+등록" otherwise; a `registeredByPath` map (original_path → artifact) is threaded `GatewayApp → ChatView → Timeline → MarkdownContent → context → PathChip`; registering (or a 409) flips the chip to "보기".

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/components/organisms/MarkdownContent/MarkdownContent.test.jsx`:

```jsx
it("shows 보기 (not +등록) when the path is already registered", () => {
  const registered = new Map([["out/cat.png", { id: "a1", type: "image", title: "cat.png", relative_path: "files/x/cat.png", mime_type: "image/png", size_bytes: 10, created_at: "2026-07-10T00:00:00Z" }]]);
  render(<MarkdownContent source={"저장: `out/cat.png`"} sessionId="s1" registeredByPath={registered} />);
  expect(screen.getByRole("button", { name: "보기" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "+등록" })).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify it fails**

Run (from `frontend/`): `npx vitest run src/components/organisms/MarkdownContent`
Expected: FAIL (no 보기 button; prop unsupported)

- [ ] **Step 3: Make PathChip dedup-aware and open the modal**

In `frontend/src/components/organisms/MarkdownContent/index.jsx`:

- Add imports: `import { ArtifactModal } from "../ArtifactModal/index.jsx";` and keep the existing `createContext/useContext`.
- Add a second context carrying the registered map + a refresh callback:

```jsx
const SessionIdContext = createContext(null);
const RegistryContext = createContext({ registeredByPath: null, onRegistered: null });
```

- Rewrite `PathChip`:

```jsx
function PathChip({ path }) {
  const sessionId = useContext(SessionIdContext);
  const { registeredByPath, onRegistered } = useContext(RegistryContext);
  const toast = useToast();
  const [saving, setSaving] = useState(false);
  const [localArtifact, setLocalArtifact] = useState(null);
  const [open, setOpen] = useState(false);

  const artifact = localArtifact || registeredByPath?.get(path) || null;

  async function register() {
    if (saving) return;
    setSaving(true);
    const res = await api.registerArtifact({ path, session_id: sessionId });
    setSaving(false);
    if (res.status === 200 && res.data?.artifact) {
      setLocalArtifact(res.data.artifact);
      toast("아티팩트로 등록되었습니다", "success");
      onRegistered?.();
    } else if (res.status === 409 && res.data?.detail?.artifact) {
      setLocalArtifact(res.data.detail.artifact);
      toast("이미 등록되어 있습니다", "info");
      onRegistered?.();
    } else {
      toast("등록에 실패했습니다", "error");
    }
  }

  return (
    <span className="path-chip">
      <code className="md-code">{path}</code>
      {artifact ? (
        <button type="button" className="path-chip-add" onClick={() => setOpen(true)}>보기</button>
      ) : (
        <button type="button" className="path-chip-add" onClick={register} disabled={saving}>
          {saving ? "등록 중…" : "+등록"}
        </button>
      )}
      {open && artifact ? <ArtifactModal artifact={artifact} onClose={() => setOpen(false)} onDeleted={() => { setLocalArtifact(null); onRegistered?.(); }} /> : null}
    </span>
  );
}
```

- Update `MarkdownContent` signature and providers:

```jsx
export function MarkdownContent({ source, sessionId = null, registeredByPath = null, onRegistered = null }) {
  // ...existing body building `nodes`...
  return (
    <SessionIdContext.Provider value={sessionId}>
      <RegistryContext.Provider value={{ registeredByPath, onRegistered }}>
        <div className="md">{nodes}</div>
      </RegistryContext.Provider>
    </SessionIdContext.Provider>
  );
}
```

- [ ] **Step 4: Run to verify pass**

Run (from `frontend/`): `npx vitest run src/components/organisms/MarkdownContent`
Expected: PASS (existing tests + the new 보기 test)

- [ ] **Step 5: Thread the map through Timeline → ChatView → GatewayApp**

In `frontend/src/components/organisms/Timeline/index.jsx`:

```jsx
function AgentMessage({ entry, sessionId, registeredByPath, onRegistered }) {
  // ...
      <MarkdownContent source={entry.text || ""} sessionId={sessionId} registeredByPath={registeredByPath} onRegistered={onRegistered} />
  // ...
}

export function Timeline({ entries, busy, sessionId = null, registeredByPath = null, onRegistered = null }) {
  // ... in the loop:
  if (entry.type === "agent") nodes.push(<AgentMessage key={`a-${nodes.length}`} entry={entry} sessionId={sessionId} registeredByPath={registeredByPath} onRegistered={onRegistered} />);
}
```

In `frontend/src/components/organisms/ChatView/index.jsx` — add props and forward to `<Timeline>` (line ~127):

```jsx
export function ChatView({ /* ...existing props..., */ registeredByPath, onArtifactChange }) {
  // ...
          <Timeline entries={entries} busy={busy} sessionId={activeSessionId} registeredByPath={registeredByPath} onRegistered={onArtifactChange} />
  // ...
}
```

In `frontend/src/components/containers/GatewayApp/index.jsx`:

- Ensure artifacts are loaded for the chat screen too. In the screen-effect (near line 220 where `screen === "artifacts"` fetches), also fetch on chat:

```jsx
    } else if (screen === "artifacts") {
      api.artifacts().then(setArtifacts);
    } else if (screen === "chat") {
      api.artifacts().then(setArtifacts);
    }
```

- Build the map and pass to ChatView:

```jsx
  const registeredByPath = useMemo(() => {
    const map = new Map();
    for (const a of artifacts) {
      const key = a.metadata?.original_path;
      if (key) map.set(key, a);
    }
    return map;
  }, [artifacts]);
```

(Add `useMemo` to the React import if missing.) Then on the `<ChatView ... />` render add:

```jsx
          registeredByPath={registeredByPath}
          onArtifactChange={() => api.artifacts().then(setArtifacts)}
```

Note: the artifact payload already includes `metadata` (see `_artifact_payload`), so `a.metadata.original_path` is available after Task 1.

- [ ] **Step 6: Run full frontend suite + build**

Run (from `frontend/`): `npx vitest run` then `npm run build`
Expected: all PASS; build succeeds.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/organisms/MarkdownContent/index.jsx frontend/src/components/organisms/MarkdownContent/MarkdownContent.test.jsx frontend/src/components/organisms/Timeline/index.jsx frontend/src/components/organisms/ChatView/index.jsx frontend/src/components/containers/GatewayApp/index.jsx
git commit -m "feat: chat transcript shows 보기 for registered paths, opening the artifact modal"
```

---

## Self-Review

- **Spec coverage:** grid image thumbnail (Task 2 Step 5) ✅; provenance overflow fix (`.modal-v word-break`, Task 2 Step 6) ✅; image zoom/pan (Task 2 `ImageViewer`) ✅; centered modal (Task 2 `ArtifactModal` + `.modal-backdrop`) ✅; chat 보기 button opening viewer (Task 3 `PathChip`) ✅; dedup + delete-to-reregister (Task 1 409 + DELETE, Task 2 delete button) ✅.
- **Type consistency:** `registerArtifact` returns `{status, ok, data}` (Task 2) and PathChip consumes exactly that (Task 3); dedup metadata keys `source_path`/`original_path` written in Task 1 and read as `metadata.original_path` in Task 3.
- **Reuse:** one `ArtifactModal` used by both ArtifactsView and PathChip — no duplicate viewer.
- **Placeholder scan:** each code step carries full code; `{/* ...existing... */}` markers only denote unchanged surrounding code, not missing implementation.
- **Known limitations (documented):** a path registered under a different token string than shown in the transcript won't pre-resolve to 보기 on reload (first click returns 409 and then flips to 보기); pan has no bounds clamping (free drag).

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-10-artifact-viewer-dedup-chat-view.md`.
