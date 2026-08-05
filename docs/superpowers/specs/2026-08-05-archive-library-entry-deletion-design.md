# Archive Library 문서 삭제 설계

## 문제

Archive의 문서는 `draft` → `published` → `archived` 상태를 거친다. 삭제는 `draft`만
가능하다:

```python
# src/personal_agent_gateway/archive.py, delete_draft()
if row["status"] != "draft":
    raise ValueError("Only private Archive drafts can be deleted")
```

그래서 Library에 등록된 문서(`published`)와 보관 처리된 문서(`archived`)는 지울 수
없다. `archive_entry()`가 상태만 `archived`로 바꾸는 소프트 처리를 제공하지만, 행은
남고 검색 인덱스에서도 사라지지 않는다.

## 목표

Library에 등록된 문서도 지울 수 있게 한다. 하드 삭제 — 행과 연관 데이터를 실제로
제거하며, 되돌릴 수 없다.

## 하지 않는 것

- 삭제 취소·휴지통·보존 기간
- 상태를 `archived`로 먼저 보내야만 삭제를 허용하는 2단계 절차
- 삭제 전용 별도 엔드포인트
- 권한 분리 (이 게이트웨이는 단일 사용자)

## 접근

기존 삭제 경로의 **상태 제약만 걷어낸다.** 새 개념을 만들지 않는다.

근거: 지식 요청 되돌리기와 연관 테이블 정리는 이미 상태를 보지 않고 동작하도록
작성돼 있다. 따라서 검사 3줄을 제거하면 `published`·`archived`도 같은 경로로 올바르게
삭제된다. 별도 엔드포인트를 만들면 같은 로직이 두 곳에 살게 되고, 2단계 절차는
사용자가 원한 "지우기"를 두 번 조작으로 바꾼다.

## 변경 사항

### 1. `src/personal_agent_gateway/archive.py`

`delete_draft(entry_id) -> None` 을 `delete_entry(entry_id) -> str` 로 바꾼다.

- 상태 검사(`if row["status"] != "draft": raise ValueError(...)`)를 제거한다.
- 없는 문서에 대한 `KeyError`는 유지한다.
- 이미 읽은 `row["status"]`를 반환한다. 라우트가 감사 이벤트를 상태별로 갈라 쓰는 데
  필요하고, 이 값을 반환하면 DB를 두 번 조회하지 않는다.
- 본문의 나머지는 변경하지 않는다: 연결된 `knowledge_requests`를 `status='open'`,
  `assigned_team_run_id=null` 로 되돌리고, `archive_entries_fts`·`archive_bindings`·
  `archive_revisions`·`archive_draft_origins`·`archive_entries` 행을 지운다.

### 2. `src/personal_agent_gateway/api/archive.py`

`DELETE /api/archive/entries/{entry_id}` 핸들러를 `delete_entry`로 이름을 바꾸고,
서비스가 반환한 상태로 감사 이벤트를 갈라 기록한다.

| 삭제된 문서의 상태 | `event_type` | `action` |
| --- | --- | --- |
| `draft` | `archive.draft_deleted` | `archive.delete_draft` |
| 그 외 (`published`, `archived`) | `archive.entry_deleted` | `archive.delete_entry` |

`resource_type="archive_entry"`, `resource_id=entry_id`는 그대로 둔다.

draft 쪽 이벤트 이름을 바꾸지 않는 이유: 이미 쌓인 감사 로그와의 연속성을 끊지 않기
위해서다. 두 타입을 갈라두면 "누가 공유 문서를 지웠나"를 나중에 구분할 수 있다.

`KeyError → 404`는 유지한다. `ValueError → 409` 핸들러도 남긴다 — 지금은 발생하지
않지만, 앞으로 다른 거부 조건이 생길 자리다.

응답 형태(`{"deleted_id": entry_id}`)는 바꾸지 않는다.

### 3. `frontend/src/api/client.js`

`deleteArchiveDraft(id)` 를 `deleteArchiveEntry(id)` 로 이름만 바꾼다. URL과 메서드는
동일하다.

### 4. `frontend/src/components/organisms/ArchiveView/index.jsx`

편집기는 이미 `published` 문서를 열고 수정할 수 있다(`onEditEntry` → `editingId` →
`reviseArchiveEntry`). 삭제 버튼만 draft로 한정돼 있다:

```javascript
const editingDraft = drafts.find((entry) => entry.id === editingId) || null;
{editingDraft ? <Button ...>Delete draft</Button> : null}
```

`editingEntry` 를 `[...entries, ...drafts]` 에서 찾아 버튼의 노출 조건으로 쓴다.
`editingDraft` 는 draft 전용 분기가 필요한 곳에만 남긴다.

문구는 상태에 따라 갈라 쓴다.

| | 버튼 | 확인 |
| --- | --- | --- |
| `draft` | `Delete draft` | `Delete the private draft "<title>"?` |
| 그 외 | `Delete document` | `Permanently delete the Library document "<title>"? This cannot be undone.` |

삭제 후 안내:

- `draft`: `Draft deleted. Its linked Knowledge Request can be worked again.` (기존 유지)
- 그 외: `Document deleted. Any linked Knowledge Request can be worked again.`

확인은 기존과 같이 `window.confirm` 을 쓴다. 취소하면 API를 호출하지 않는다.

## 삭제가 남기는 것과 지우는 것

| 대상 | 결과 |
| --- | --- |
| `archive_entries` 행 | 삭제 |
| `archive_revisions` | 삭제 |
| `archive_bindings` | 삭제 |
| `archive_entries_fts` | 삭제 (검색 결과에서 사라짐) |
| `archive_draft_origins` | 삭제 |
| 연결된 `knowledge_requests` | **행 보존**, `status='open'`·`assigned_team_run_id=null` 로 되돌림 |

지식 요청을 `open`으로 되돌리는 이유: 문서가 사라지면 그 지식 수요는 다시 미해결
상태다. 링크만 끊고 `fulfilled` 로 남기면 근거 없는 충족 기록이 된다. 이는 draft
삭제가 이미 하는 동작과 같다.

## 오류 처리

| 상황 | 결과 |
| --- | --- |
| 없는 `entry_id` | 404, `"Archive draft not found"` → `"Archive entry not found"` 로 문구 수정 |
| 프런트에서 확인 취소 | API 호출 없음, 상태 변화 없음 |
| API 실패 | 기존 오류 표시 경로 유지 (`setError`) |

## 테스트

**백엔드** (`tests/test_archive.py`, `tests/test_api_archive.py`)

- `published` 문서 삭제 → 행·revisions·bindings·FTS 항목이 모두 사라진다
- `archived` 문서 삭제 → 같은 결과
- 삭제된 문서로 충족돼 있던 지식 요청이 `open` 이 되고 `assigned_team_run_id` 가
  `null` 이 된다
- 없는 id → 404
- 감사 이벤트가 상태별로 갈린다: `draft` 삭제는 `archive.draft_deleted`,
  `published` 삭제는 `archive.entry_deleted`
- 기존 draft 삭제 테스트는 **회귀 테스트로 유지** — 문구와 이벤트 타입이 바뀌지
  않았음을 확인한다

**프런트엔드** (`frontend/src/components/organisms/ArchiveView/ArchiveView.test.jsx`)

- `published` 문서를 편집기에 열면 삭제 버튼이 보이고 라벨이 `Delete document` 다
- 확인을 취소하면 삭제 API가 호출되지 않는다
- draft를 열면 라벨과 확인 문구가 기존과 같다 (회귀)

## 영향 범위

변경 파일 4개. 새 엔드포인트·새 테이블·새 마이그레이션 없음. 응답 형태 불변.
