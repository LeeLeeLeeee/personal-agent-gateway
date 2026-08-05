# Archive Library 문서 삭제 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Library에 등록된 문서(`published`)와 보관된 문서(`archived`)도 하드 삭제할 수 있게 한다. 현재는 `draft`만 삭제 가능하다.

**Architecture:** 기존 삭제 경로의 상태 제약을 걷어내고, 지식 요청 되돌리기를 두 갈래(draft origin / fulfilled 링크)로 확장한다. 새 엔드포인트·테이블·마이그레이션은 없다. 프런트엔드는 이미 published 문서를 편집기에 열 수 있으므로, 삭제 버튼의 노출 조건만 고친다.

**Tech Stack:** Python 3 / FastAPI / SQLite, React + Vite / vitest, pytest.

**설계 문서:** `docs/superpowers/specs/2026-08-05-archive-library-entry-deletion-design.md`

## Global Constraints

- Python은 `PYTHONPATH=src` 로 실행한다. 프런트엔드 테스트는 `frontend/` 에서 `npx vitest run`.
- **백엔드 전체 스위트는 약 7~9분이다. 절대 백그라운드로 돌리지 말고 블로킹 호출로 10분 이상 여유를 두고 실행한다.** 반복 확인은 대상 파일만 돌린다.
- 백엔드 기준선(main `10c82e1`): `pytest tests -q` → **31 failed, 1329 passed, 4 skipped**. 실패 31건은 `main` 에 이미 있던 것이다. 판정은 **실패 목록이 늘지 않았는지**로 한다. 숫자만 비교하지 말 것 — `.env` 부재와 게이트웨이 실행 여부에 따라 `test_local_runtime_scripts.py` 와 skip 수가 흔들린다.
- `ruff check src` 는 **클린이 기준**이다. 새 경고는 회귀로 취급해 고친다.
- 프런트엔드 기준선: `npx vitest run` → **353 passed (40 files)**. 전부 통과가 기준이다.
- 커밋 메시지는 한국어 Conventional Commits.
- 응답 형태 `{"deleted_id": entry_id}` 를 바꾸지 않는다. 새 엔드포인트를 만들지 않는다.
- `archive_entry()`(소프트 `archived` 전이)는 건드리지 않는다. 이 작업과 무관하다.

## File Structure

| 파일 | 책임 | 태스크 |
|---|---|---|
| `src/personal_agent_gateway/archive.py` | `delete_draft` → `delete_entry`, 상태 제약 제거, 되돌리기 두 갈래 | 1 |
| `tests/test_archive.py` | 서비스 레벨 삭제 테스트 (현재 삭제 테스트가 하나도 없다) | 1 |
| `src/personal_agent_gateway/api/archive.py` | 라우트 이름·감사 이벤트 분기·404 문구 | 2 |
| `tests/test_api_archive.py` | API 레벨 테스트 + 기존 draft 테스트 회귀 유지 | 2 |
| `frontend/src/api/client.js` | `deleteArchiveDraft` → `deleteArchiveEntry` | 3 |
| `frontend/src/components/organisms/ArchiveView/index.jsx` | 삭제 버튼 노출 조건·문구 분기 | 3 |
| `frontend/src/components/organisms/ArchiveView/ArchiveView.test.jsx` | 프런트 테스트 | 3 |

태스크 1 → 2 → 3 순서로 진행한다. 2는 1의 반환값에 의존하고, 3은 2의 라우트 동작에 의존한다.

---

### Task 1: 서비스 레이어 — 상태 제약 제거와 되돌리기 두 갈래

**Files:**
- Modify: `src/personal_agent_gateway/archive.py` (`delete_draft`, 416행 부근)
- Test: `tests/test_archive.py` (추가)

**Interfaces:**
- Consumes: 없음
- Produces: `ArchiveService.delete_entry(entry_id: str) -> str` — 삭제된 문서의 삭제 직전 `status` 문자열을 반환한다. `KeyError` 는 없는 문서일 때 그대로 발생한다. Task 2가 이 반환값으로 감사 이벤트를 갈라 쓴다.

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_archive.py` 끝에 추가한다. 이 파일의 기존 관례를 따른다 — `archive_service(tmp_path)` 가 `(ArchiveService, PersonaService)` 를 돌려준다.

```python
def test_delete_entry_removes_published_document_and_its_traces(tmp_path: Path) -> None:
    archive, personas = archive_service(tmp_path)
    persona = personas.create_persona("Researcher", "Reference checking", "Checks sources.", [], [])
    entry = archive.publish_entry(
        actor_type="user",
        kind="reference",
        title="Rollback checklist",
        summary="A verified rollback sequence.",
        content_markdown="- Roll back\n- Verify",
        tags=["deployment"],
        source_urls=[],
        persona_ids=[persona.id],
    )

    status = archive.delete_entry(entry.id)

    assert status == "published"
    assert archive.list_entries(status="published") == []
    assert archive.list_entries(query="rollback", status="published") == []
    with pytest.raises(KeyError):
        archive.get_entry(entry.id)


def test_delete_entry_reopens_the_request_the_published_document_fulfilled(tmp_path: Path) -> None:
    """The fulfilled link lives on knowledge_requests.fulfilled_by_entry_id, not on
    archive_draft_origins — the pre-existing delete path never touched it, so a deleted
    document used to leave a fulfilled request with no supporting document."""
    archive, personas = archive_service(tmp_path)
    persona = personas.create_persona("Researcher", "Reference checking", "Checks sources.", [], [])
    request = archive.create_knowledge_request(
        title="Deployment rollback checklist",
        reason="Reusable guidance is missing.",
        suggested_outline=["Rollback", "Verification"],
        source_hints=[],
        requested_by_persona_id=persona.id,
    )
    entry = archive.publish_entry(
        actor_type="user",
        kind="checklist",
        title=request.title,
        summary="A verified rollback sequence.",
        content_markdown="- Roll back\n- Verify",
        tags=[],
        source_urls=[],
        persona_ids=[persona.id],
        request_id=request.id,
    )
    assert archive.get_request(request.id).status == "fulfilled"

    archive.delete_entry(entry.id)

    reopened = archive.get_request(request.id)
    assert reopened.status == "open"
    assert reopened.fulfilled_by_entry_id is None
    assert reopened.assigned_team_run_id is None


def test_delete_entry_still_reopens_an_in_progress_draft_origin(tmp_path: Path) -> None:
    """Regression: the draft-origin path must keep working exactly as before."""
    archive, _personas = archive_service(tmp_path)
    request = archive.create_knowledge_request(
        title="Rollback guidance",
        reason="Missing.",
        suggested_outline=[],
        source_hints=[],
        requested_by_persona_id=None,
    )
    draft = archive.save_draft(
        actor_type="team",
        origin_source_type="hook",
        origin_source_id="hook-1",
        kind="reference",
        title="Draft",
        summary="",
        content_markdown="# Draft",
        tags=[],
        source_urls=[],
        persona_ids=[],
        origin_request_id=request.id,
    )

    status = archive.delete_entry(draft.id)

    assert status == "draft"
    assert archive.get_request(request.id).status == "open"


def test_delete_entry_removes_an_archived_document(tmp_path: Path) -> None:
    archive, _personas = archive_service(tmp_path)
    entry = archive.publish_entry(
        actor_type="user",
        kind="reference",
        title="Old guidance",
        summary="Superseded.",
        content_markdown="# Old",
        tags=[],
        source_urls=[],
        persona_ids=[],
    )
    archive.archive_entry(entry.id)

    status = archive.delete_entry(entry.id)

    assert status == "archived"
    with pytest.raises(KeyError):
        archive.get_entry(entry.id)


def test_delete_entry_rejects_an_unknown_id(tmp_path: Path) -> None:
    archive, _personas = archive_service(tmp_path)

    with pytest.raises(KeyError):
        archive.delete_entry("nope")
```

> `save_draft` 의 `origin_request_id` 인자명과 `create_knowledge_request` 의 인자는 이 파일의 기존 테스트에서 쓰이는 그대로다. `get_entry` 는 없는 문서에 `KeyError` 를 던진다(확인함: `archive.py:410-414`).

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `cd personal-agent-gateway && PYTHONPATH=src python -m pytest tests/test_archive.py -q -k delete_entry`
Expected: 5건 모두 FAIL — `AttributeError: 'ArchiveService' object has no attribute 'delete_entry'`.

- [ ] **Step 3: 구현**

`archive.py` 의 `delete_draft` 를 다음으로 교체한다. 이름·반환값·되돌리기 두 갈래가 변경점이고, 연관 테이블 삭제 5줄은 그대로다.

```python
    def delete_entry(self, entry_id: str) -> str:
        """Hard-delete an Archive entry in any state and return the status it had.

        Returning the prior status lets the route split its audit event without a
        second query.
        """
        with self._db.connection() as connection:
            row = connection.execute(
                "select status from archive_entries where id = ?", (entry_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Archive entry not found: {entry_id}")
            status = str(row["status"])
            now = _now()

            # A document reaches a knowledge request through two unrelated links, and a
            # single document can hold both: a draft records its origin request, while a
            # published document is recorded as the request's fulfiller. Reset both —
            # deleting the document makes that knowledge need open again either way.
            origin = connection.execute(
                "select knowledge_request_id from archive_draft_origins where entry_id = ?",
                (entry_id,),
            ).fetchone()
            if origin is not None and origin["knowledge_request_id"]:
                connection.execute(
                    """
                    update knowledge_requests
                    set status = 'open', assigned_team_run_id = null, updated_at = ?
                    where id = ? and status = 'in_progress'
                    """,
                    (now, origin["knowledge_request_id"]),
                )
            # Must run before the entry row is deleted: the foreign key is
            # `on delete set null`, so deleting first would erase the link.
            connection.execute(
                """
                update knowledge_requests
                set status = 'open',
                    fulfilled_by_entry_id = null,
                    assigned_team_run_id = null,
                    updated_at = ?
                where fulfilled_by_entry_id = ? and status = 'fulfilled'
                """,
                (now, entry_id),
            )

            connection.execute("delete from archive_entries_fts where entry_id = ?", (entry_id,))
            connection.execute("delete from archive_bindings where entry_id = ?", (entry_id,))
            connection.execute("delete from archive_revisions where entry_id = ?", (entry_id,))
            connection.execute("delete from archive_draft_origins where entry_id = ?", (entry_id,))
            connection.execute("delete from archive_entries where id = ?", (entry_id,))
            return status
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=src python -m pytest tests/test_archive.py -q`
Expected: 신규 5건 PASS, 기존 테스트 전부 PASS.

- [ ] **Step 5: 호출자가 남아 있지 않은지 확인**

Run: `grep -rn "delete_draft" src/ tests/ frontend/src/`
Expected: `api/archive.py` 의 호출 한 곳만 남는다(Task 2에서 고친다). 그 외에 남아 있으면 보고하라.

- [ ] **Step 6: 커밋**

```bash
git add src/personal_agent_gateway/archive.py tests/test_archive.py
git commit -m "feat: Archive 문서를 상태와 무관하게 삭제하고 충족된 지식 요청을 되돌림"
```

---

### Task 2: API 레이어 — 라우트 이름과 감사 이벤트 분기

**Files:**
- Modify: `src/personal_agent_gateway/api/archive.py` (222~243행 부근)
- Test: `tests/test_api_archive.py` (추가 + 기존 유지)

**Interfaces:**
- Consumes: Task 1의 `ArchiveService.delete_entry(entry_id) -> str`
- Produces: `DELETE /api/archive/entries/{entry_id}` 가 모든 상태에서 200 + `{"deleted_id": ...}` 를 반환하고, 감사 이벤트를 상태별로 기록한다.

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_api_archive.py` 끝에 추가한다. 이 파일의 `authenticated_client(tmp_path)` 헬퍼를 쓴다.

```python
def test_delete_removes_a_published_library_document(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    entry = client.app.state.archive_service.publish_entry(
        actor_type="user",
        kind="reference",
        title="Library doc",
        summary="Shared guidance.",
        content_markdown="# Doc",
        tags=[],
        source_urls=[],
        persona_ids=[],
    )

    response = client.delete(f"/api/archive/entries/{entry.id}")

    assert response.status_code == 200
    assert response.json() == {"deleted_id": entry.id}
    assert client.get("/api/archive/entries", params={"status": "published"}).json()["entries"] == []


def test_delete_audits_a_library_document_apart_from_a_draft(tmp_path: Path) -> None:
    """The two events must stay distinguishable: a shared Library document being
    deleted is a different act from discarding a private draft."""
    client = authenticated_client(tmp_path)
    service = client.app.state.archive_service
    published = service.publish_entry(
        actor_type="user",
        kind="reference",
        title="Library doc",
        summary="Shared.",
        content_markdown="# Doc",
        tags=[],
        source_urls=[],
        persona_ids=[],
    )
    draft = service.save_draft(
        actor_type="team",
        origin_source_type="hook",
        origin_source_id="hook-1",
        kind="reference",
        title="Private draft",
        summary="",
        content_markdown="# Draft",
        tags=[],
        source_urls=[],
        persona_ids=[],
    )

    assert client.delete(f"/api/archive/entries/{published.id}").status_code == 200
    assert client.delete(f"/api/archive/entries/{draft.id}").status_code == 200

    def resource_ids(event_type: str) -> list[str]:
        response = client.get("/api/audit/events", params={"event_type": event_type})
        assert response.status_code == 200
        return [event["resource_id"] for event in response.json()["events"]]

    assert resource_ids("archive.entry_deleted") == [published.id]
    assert resource_ids("archive.draft_deleted") == [draft.id]


def test_delete_unknown_entry_returns_404(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)

    response = client.delete("/api/archive/entries/nope")

    assert response.status_code == 404
```

> 감사 API 형태는 확인해 둔 값이다: `GET /api/audit/events` (라우터 prefix `/api/audit`), `event_type` 쿼리 필터를 지원하고, 응답은 `{"events": [...], "next_cursor": ...}` 이며 각 항목에 `event_type` 과 `resource_id` 가 있다 (`src/personal_agent_gateway/api/audit.py`). `resource_id` 까지 단정하는 이유는, 이벤트 타입만 확인하면 두 삭제가 서로 뒤바뀌어 기록돼도 통과하기 때문이다.

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `PYTHONPATH=src python -m pytest tests/test_api_archive.py -q`
Expected: `test_delete_removes_a_published_library_document` 가 409로 FAIL(현재 라우트가 `ValueError` 를 409로 바꾼다). 감사 분기 테스트도 FAIL. `test_delete_unknown_entry_returns_404` 는 이미 PASS해도 된다.

- [ ] **Step 3: 구현**

`api/archive.py` 의 삭제 핸들러를 교체한다.

```python
@router.delete("/entries/{entry_id}")
def delete_entry(
    request: Request,
    entry_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, str]:
    try:
        status = request.app.state.archive_service.delete_entry(entry_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Archive entry not found") from exc
    except ValueError as exc:
        # No condition raises this today. Kept so a future refusal returns 409
        # rather than a 500.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # Draft deletion keeps its original event name so existing audit history stays
    # continuous; deleting a shared Library document is recorded separately.
    draft = status == "draft"
    record_domain_audit(
        request,
        principal,
        event_type="archive.draft_deleted" if draft else "archive.entry_deleted",
        action="archive.delete_draft" if draft else "archive.delete_entry",
        resource_type="archive_entry",
        resource_id=entry_id,
    )
    return {"deleted_id": entry_id}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=src python -m pytest tests/test_api_archive.py tests/test_archive.py -q`
Expected: 전부 PASS. 특히 기존 `test_delete_draft_removes_only_private_team_draft` 가 그대로 PASS해야 한다 — draft 동작과 응답 형태는 바뀌지 않았다.

- [ ] **Step 5: 회귀 확인**

Run: `PYTHONPATH=src python -m pytest tests -q --tb=no 2>&1 | tail -3` (블로킹, 10분 이상 허용)
그리고 `python -m ruff check src`
Expected: 실패 목록이 기준선 31건보다 늘지 않았다. ruff 클린.

- [ ] **Step 6: 커밋**

```bash
git add src/personal_agent_gateway/api/archive.py tests/test_api_archive.py
git commit -m "feat: Library 문서 삭제 라우트와 감사 이벤트 분기"
```

---

### Task 3: 프런트엔드 — 삭제 버튼 노출 조건과 문구

**Files:**
- Modify: `frontend/src/api/client.js` (267행 부근)
- Modify: `frontend/src/components/organisms/ArchiveView/index.jsx` (744행 `editingDraft`, 848행 `deleteDraft`, 1296행 버튼)
- Test: `frontend/src/components/organisms/ArchiveView/ArchiveView.test.jsx` (추가)

**Interfaces:**
- Consumes: Task 2의 `DELETE /api/archive/entries/{id}` (모든 상태 200)
- Produces: `client.deleteArchiveEntry(id)` — 기존 `deleteArchiveDraft` 의 이름만 바뀐 것. URL·메서드·반환값(`response.ok`) 동일.

- [ ] **Step 1: 실패 테스트 작성**

`ArchiveView.test.jsx` 에 추가한다. 이 파일의 기존 관례(fetch 목킹, `jsonResponse` 헬퍼, `render(<ArchiveView />)`)를 그대로 따르고, **published 문서를 편집기에 여는 기존 테스트의 조작 흐름을 재사용한다** — 이 파일에서 `Open in Library` 버튼을 눌러 편집기를 여는 테스트를 찾아 그 패턴을 그대로 쓴다.

검증할 것 3가지:

1. published 문서를 편집기에 열면 삭제 버튼이 보이고 라벨이 `Delete document` 다
2. `window.confirm` 을 거부하면 삭제 요청이 나가지 않는다
3. draft를 열면 라벨이 `Delete draft` 다 (회귀)

`window.confirm` 은 `vi.spyOn(window, "confirm")` 으로 제어한다. 확인을 거부한 경우 `fetch` 가 `DELETE` 로 호출되지 않았음을 단정한다.

> 기준으로 삼을 테스트는 확인해 두었다: **`ArchiveView.test.jsx:416` `"lets the user delete a private Team draft after confirmation"`** 이 이미 `vi.spyOn(window, "confirm").mockReturnValue(true)` 로 확인을 제어하고 `Delete draft` 버튼을 눌러 삭제한다. 그 셋업을 복사해 문서를 `published` 로 바꿔 쓴다. published 문서를 편집기에 여는 기존 테스트는 **없으므로** 편집기 진입 조작(`Open in Library` 버튼 등)은 이 파일의 draft 진입 흐름을 따라 새로 구성해야 한다. 그 테스트(416행)는 회귀 감시용으로 **수정하지 말고 그대로 둔다.**

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `cd frontend && npx vitest run src/components/organisms/ArchiveView/ArchiveView.test.jsx`
Expected: published 관련 신규 테스트 FAIL — 삭제 버튼이 렌더되지 않는다. draft 회귀 테스트는 PASS.

> worktree에서 작업 중이라면 `frontend/node_modules` 가 없어 `ERR_MODULE_NOT_FOUND` 가 난다. 본 체크아웃의 것을 재사용하라:
> `cmd //c "mklink /J node_modules C:\Users\Administrator\playground\personal-agent-gateway\frontend\node_modules"`

- [ ] **Step 3: 구현**

`frontend/src/api/client.js` — 이름만 변경:

```javascript
  async deleteArchiveEntry(id) {
    const response = await fetch(`/api/archive/entries/${encodeURIComponent(id)}`, { method: "DELETE" });
    return response.ok;
  },
```

`ArchiveView/index.jsx` — 744행 부근에 `editingEntry` 를 추가한다. `editingDraft` 는 draft 전용 분기가 남아 있다면 유지한다:

```javascript
  const editingDraft = drafts.find((entry) => entry.id === editingId) || null;
  // The editor already opens published documents (Open in Library -> reviseArchiveEntry);
  // only the delete button was still scoped to drafts.
  const editingEntry = [...entries, ...drafts].find((entry) => entry.id === editingId) || null;
```

848행의 `deleteDraft` 를 `deleteEntry` 로 바꾸고 문구를 상태로 갈라 쓴다:

```javascript
  async function deleteEntry() {
    if (!editingEntry) return;
    const isDraft = editingEntry.status === "draft";
    const confirmMessage = isDraft
      ? `Delete the private draft "${editingEntry.title}"?`
      : `Permanently delete the Library document "${editingEntry.title}"? This cannot be undone.`;
    if (!window.confirm(confirmMessage)) return;
    setSaving(true);
    setError(null);
    try {
      if (!await client.deleteArchiveEntry(editingEntry.id)) {
        throw new Error(isDraft ? "The draft could not be deleted." : "The document could not be deleted.");
      }
      setEditingId(null);
      setForm(EMPTY_FORM);
      setRevisions([]);
      setNotice(
        isDraft
          ? "Draft deleted. Its linked Knowledge Request can be worked again."
          : "Document deleted. Any linked Knowledge Request can be worked again."
      );
      await loadData();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setSaving(false);
    }
  }
```

1296행 버튼:

```javascript
              {editingEntry ? (
                <Button type="button" variant="destructive" disabled={saving} onClick={deleteEntry}>
                  {editingEntry.status === "draft" ? "Delete draft" : "Delete document"}
                </Button>
              ) : null}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd frontend && npx vitest run`
Expected: 전체 통과. 기준선은 353 passed (40 files) — 신규 테스트만큼 늘어난다.

- [ ] **Step 5: 남은 호출자 확인**

Run: `grep -rn "deleteArchiveDraft\|deleteDraft" frontend/src/`
Expected: 아무것도 남지 않는다.

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/api/client.js frontend/src/components/organisms/ArchiveView/index.jsx frontend/src/components/organisms/ArchiveView/ArchiveView.test.jsx
git commit -m "feat: Library 문서 삭제 버튼 노출과 확인 문구 분기"
```

---

## Self-Review

**1. 스펙 커버리지**

| 스펙 항목 | 태스크 |
|---|---|
| 상태 제약 제거, `delete_draft` → `delete_entry`, 상태 반환 | 1 |
| 되돌리기 두 갈래 (origin / fulfilled) | 1 |
| 연관 테이블 정리 유지 | 1 (변경 없음, 테스트로 확인) |
| 감사 이벤트 상태별 분기 | 2 |
| 404 문구 수정, 409 핸들러 유지 | 2 |
| 응답 형태 불변 | 2 (기존 테스트가 회귀 감시) |
| `client.deleteArchiveEntry` 이름 변경 | 3 |
| 버튼 노출 조건·문구·안내 분기 | 3 |
| 확인 취소 시 API 미호출 | 3 |

**2. 플레이스홀더 점검** — 모든 코드 단계에 실제 코드가 있다. 초안에는 "먼저 읽고 실제 형태에 맞추라"는 지시가 세 곳 있었는데, 그중 둘은 실측해 값으로 대체했다.

- `get_entry` 는 없는 문서에 `KeyError` 를 던진다 (`archive.py:410-414`) — 확인 완료, 단정 확정
- 감사 API는 `GET /api/audit/events`, `event_type` 필터 지원, 응답 `{"events":[…]}` 의 각 항목에 `event_type`·`resource_id` (`api/audit.py:11,31,57,63`) — 확인 완료, 테스트를 `resource_id` 까지 단정하도록 강화

남은 한 곳(프런트 편집기 진입 조작)은 값으로 확정하지 못했다. published 문서를 편집기에 여는 기존 테스트가 **존재하지 않기** 때문이다. 대신 기준으로 삼을 테스트의 정확한 위치(`ArchiveView.test.jsx:416`)와 그 테스트를 수정하지 말라는 제약을 명시했다.

**3. 타입 일관성 확인**
- `delete_entry(entry_id: str) -> str` — Task 1 정의, Task 2가 반환값을 `status` 로 받아 분기 ✓
- `KeyError` → 404 — Task 1이 던지고 Task 2가 잡는다 ✓
- `deleteArchiveEntry(id)` — Task 3에서 정의·사용, 반환값 `response.ok` 불변 ✓
- `editingEntry` — Task 3에서 정의, 같은 태스크의 버튼·핸들러가 사용 ✓
- 감사 이벤트 문자열 — Task 2의 구현과 테스트에서 `archive.entry_deleted`·`archive.draft_deleted` 로 동일 ✓
