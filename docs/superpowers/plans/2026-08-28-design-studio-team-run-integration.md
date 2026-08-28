# 팀런–design-studio 연결 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 팀런 워커가 design-studio에 디자인을 맡겨 초안을 받아 쓰고, 그 산출물이 PAG의 요청함 모달에 preview로 올라와 사장님이 승인·반려하며, 반려 이유가 다음 사이클 워커에게 되돌아간다.

**Architecture:** 워커가 design-studio HTTP(`127.0.0.1:7777`)를 직접 부르고, 결과에 ` ```design-review ` 블록을 적어 승인을 청한다. PAG는 그 블록을 읽어 `team_design_reviews` 행을 만들고, 기존 아티팩트 preview로 화면에 띄운다. 승인은 PAG 백엔드의 얇은 HTTP 클라이언트가 design-studio의 `layout/accept`로 중계한다. 표는 `team_decision_requests`와 합치지 않는다 — 그쪽은 런을 재우는 것과 얽혀 있고 디자인 승인은 런을 재우지 않는다.

**Tech Stack:** Python 3.13 · FastAPI · SQLite · httpx · React + Vite + Vitest

**Spec:** `docs/superpowers/specs/2026-08-28-design-studio-team-run-integration-design.md`

## Global Constraints

- **design-studio 저장소를 고치지 않는다.** 이 계획은 `playground/design-studio` 아래 파일을 하나도 수정하지 않는다.
- **블록 읽기 함수는 어떤 입력에도 예외를 던지지 않는다.** 읽을 수 없으면 없는 것으로 본다 (`team_note_report.py`와 같은 계약).
- **`status='rejected'`이면서 `reason`이 비면 저장을 거부한다.** 이유가 없으면 다음 사이클이 같은 것을 다시 만든다.
- **design-studio POST에 `Origin` 헤더를 붙이지 않는다.** 안내서 4장: 헤더가 셋 다 없는 요청은 통과하고, 남의 `Origin`은 403이다.
- **PAG는 design-studio를 자동으로 띄우지 않는다.** `LMG_LOCAL_TOKEN`이 필요하고 그건 사람 몫이다.
- **PAG 쪽 직렬화를 만들지 않는다.** design-studio가 이미 자물쇠를 가지고 409를 준다.
- 프롬프트 관련 테스트는 **렌더된 프롬프트**를 검사한다. 모듈 상수를 보면 `.format()`이 안 된 것도 통과한다.
- 파이썬 시험: `PYTHONPATH=src python -m pytest -q -p no:randomly <경로>`
- 화면 시험: `cd frontend && npx vitest run <경로>`
- 린트: `ruff check src/ tests/`

---

## 파일 구조

| 파일 | 책임 |
|---|---|
| `src/personal_agent_gateway/team_design_review_report.py` (신규) | 워커 결과에서 ` ```design-review ` 블록을 떼어내는 **순수 함수**. I/O 없음 |
| `src/personal_agent_gateway/team_design_reviews.py` (신규) | `team_design_reviews` 표의 저장·조회. DB만 만진다 |
| `src/personal_agent_gateway/design_studio_client.py` (신규) | design-studio HTTP 얇은 클라이언트. 프로젝트 전환 + 배치 승인 |
| `src/personal_agent_gateway/migrations.py` (수정) | 마이그레이션 37 — 표 하나 |
| `src/personal_agent_gateway/config.py` (수정) | design-studio 주소 설정값 |
| `src/personal_agent_gateway/team_runtime.py` (수정) | 블록 추출 배선 · 항목 생성 · 반려 블록 · 워커 프롬프트 사용법 |
| `src/personal_agent_gateway/api/team_runs.py` (수정) | 요청함 읽기 · 승인/반려 |
| `frontend/src/api/client.js` (수정) | 요청함 API 두 개 |
| `frontend/src/components/organisms/TeamRunDetail/InboxModal.jsx` (신규) | 요청함 모달 — 목록 + 상세, 종류에 따라 본문 분기 |
| `frontend/src/components/organisms/TeamRunDetail/index.jsx` (수정) | 인라인 패널 자리를 버튼으로 |
| `src/personal_agent_gateway/static/styles.css` (수정) | 모달 스타일 |

---

## Task 1: 블록 읽기 (순수 함수)

**Files:**
- Create: `src/personal_agent_gateway/team_design_review_report.py`
- Test: `tests/test_team_design_review_report.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `@dataclass(frozen=True) class DesignReview: screen: str; stage: str; file: str`
  - `def extract_design_review(text: str) -> tuple[str, DesignReview | None]`

- [ ] **Step 1: Write the failing test**

`tests/test_team_design_review_report.py`:

```python
"""워커가 승인을 청하는 블록을 결과에서 떼어낸다.

team_note_report 와 같은 계약이다 -- 아무것도 던지지 않고, 읽을 수 없는 것은
없는 것으로 본다. 승인 요청은 선택이므로, 선택인 것이 필수인 것(작업 결과)을
죽일 수 있으면 안 된다.
"""

from personal_agent_gateway.team_design_review_report import (
    DesignReview,
    extract_design_review,
)


def test_a_block_is_lifted_out_of_the_summary():
    text = (
        "home 배치를 잡았습니다.\n"
        '```design-review\n'
        '{"screen":"home","stage":"layout","file":"out/home.layout.html"}\n'
        "```\n"
        "다음은 검색 화면입니다."
    )

    summary, review = extract_design_review(text)

    assert review == DesignReview(
        screen="home", stage="layout", file="out/home.layout.html"
    )
    assert "design-review" not in summary
    assert "home 배치를 잡았습니다." in summary
    assert "다음은 검색 화면입니다." in summary


def test_no_block_leaves_the_text_alone():
    summary, review = extract_design_review("그냥 끝냈습니다.")
    assert review is None
    assert summary == "그냥 끝냈습니다."


def test_broken_json_never_raises():
    # 워커가 형식을 틀렸다고 작업 결과가 통째로 죽으면 안 된다.
    summary, review = extract_design_review(
        "했습니다.\n```design-review\n{screen: home\n```"
    )
    assert review is None
    assert "했습니다." in summary


def test_a_missing_field_is_not_a_review():
    # screen 이 없으면 어느 화면인지 모르고, file 이 없으면 보여줄 것이 없다.
    for payload in ('{"stage":"layout","file":"out/a.html"}', '{"screen":"home"}'):
        _summary, review = extract_design_review(
            f"했습니다.\n```design-review\n{payload}\n```"
        )
        assert review is None


def test_an_unknown_stage_is_refused():
    # design-studio 는 layout 과 design 둘뿐이고, 그 밖은 400 이다.
    _summary, review = extract_design_review(
        '했습니다.\n```design-review\n{"screen":"home","stage":"deck","file":"out/a.html"}\n```'
    )
    assert review is None


def test_an_absolute_or_escaping_path_is_refused():
    # 경로는 프로젝트 안이어야 한다. 밖을 가리키면 남의 파일을 preview 로 띄운다.
    for bad in ("C:\\\\Windows\\\\win.ini", "/etc/passwd", "../../secret.html"):
        _summary, review = extract_design_review(
            f'했습니다.\n```design-review\n{{"screen":"home","stage":"layout","file":"{bad}"}}\n```'
        )
        assert review is None


def test_a_non_string_input_is_handled():
    summary, review = extract_design_review(None)
    assert (summary, review) == ("", None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python -m pytest -q -p no:randomly tests/test_team_design_review_report.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'personal_agent_gateway.team_design_review_report'`

- [ ] **Step 3: Write minimal implementation**

`src/personal_agent_gateway/team_design_review_report.py`:

```python
"""워커가 승인을 청하며 남기는 design-review 블록을 결과에서 꺼내는 순수 함수.

`extract_team_note` 와 같은 자리, 같은 규칙이다. 워커 결과 검증 단계에서
불리므로 여기서 예외가 나면 작업 하나가 통째로 죽는다. 승인 요청은 선택이므로,
선택인 것이 필수인 것을 죽일 수 있으면 안 된다 -- 그래서 이 함수는 아무것도
던지지 않고, 읽을 수 없는 것은 없는 것으로 본다.
"""

import json
import ntpath
import posixpath
import re
from dataclasses import dataclass

_BLOCK = re.compile(r"```design-review\s*\n(.*?)\n?```", re.DOTALL)
#: design-studio 가 받는 단계는 둘뿐이다. 그 밖은 저쪽이 400 으로 막으므로
#: 여기서 걸러 헛수고를 없앤다.
_STAGES = frozenset({"layout", "design"})


@dataclass(frozen=True)
class DesignReview:
    screen: str
    stage: str
    file: str


def extract_design_review(text: str) -> tuple[str, DesignReview | None]:
    """결과에서 블록을 떼어내고, 남은 글과 승인 요청을 돌려준다.

    블록이 없으면 (결과, None). 디자인을 맡기지 않은 작업이고 그것이 보통이다.

    첫 블록만 읽는다. 뒤에 더 있으면 결과에 그대로 남는데, 그 편이 조용히
    지우는 것보다 낫다 -- 사람이 보면 워커가 형식을 잘못 지켰다는 것을 안다.
    """
    if not isinstance(text, str):
        return "", None
    match = _BLOCK.search(text or "")
    if match is None:
        return (text or "").strip(), None
    summary = (text[: match.start()] + text[match.end() :]).strip()
    try:
        payload = json.loads(match.group(1))
    except (ValueError, TypeError):
        return summary, None
    if not isinstance(payload, dict):
        return summary, None
    screen = _text(payload.get("screen"))
    stage = _text(payload.get("stage"))
    file = _text(payload.get("file"))
    if not screen or not file or stage not in _STAGES:
        return summary, None
    if not _is_inside_project(file):
        return summary, None
    return summary, DesignReview(screen=screen, stage=stage, file=file)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _is_inside_project(path: str) -> bool:
    """프로젝트 안을 가리키는 상대 경로인가.

    밖을 가리키는 경로를 받으면 남의 파일을 preview 로 띄우게 된다. 워커가
    악의적이지 않아도 절대 경로를 적는 실수는 흔하다. 윈도우와 posix 를 둘 다
    본다 -- 워커 셸이 어느 쪽인지 이 함수는 모른다.
    """
    if ntpath.isabs(path) or posixpath.isabs(path):
        return False
    parts = re.split(r"[\\/]+", path)
    return bool(parts) and ".." not in parts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python -m pytest -q -p no:randomly tests/test_team_design_review_report.py`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/team_design_review_report.py tests/test_team_design_review_report.py
git commit -m "feat: 워커가 승인을 청하는 design-review 블록을 결과에서 꺼낸다"
```

---

## Task 2: 표와 저장소

**Files:**
- Modify: `src/personal_agent_gateway/migrations.py` (`MIGRATIONS` 끝, 현재 마지막은 `(36, "auto-series-zero-interval", …)`)
- Create: `src/personal_agent_gateway/team_design_reviews.py`
- Test: `tests/test_team_design_reviews.py`

**Interfaces:**
- Consumes: `DesignReview` (Task 1)
- Produces:
  - `@dataclass(frozen=True) class TeamDesignReview` — 필드: `id, team_run_id, cycle_id, task_id, artifact_id, screen, project_root, stage, status, reason, created_at, answered_at`
  - `class TeamDesignReviewService:`
    - `__init__(self, db)`
    - `create(self, *, team_run_id, cycle_id, task_id, artifact_id, screen, project_root, stage) -> TeamDesignReview`
    - `list_pending(self, team_run_id: str) -> list[TeamDesignReview]`
    - `list_rejected_unresolved(self, team_run_id: str) -> list[TeamDesignReview]`
    - `get(self, review_id: str) -> TeamDesignReview`  (없으면 `KeyError`)
    - `answer(self, review_id: str, *, status: str, reason: str | None = None) -> TeamDesignReview`

- [ ] **Step 1: Write the failing test**

`tests/test_team_design_reviews.py`:

```python
"""디자인 승인 항목의 저장과 조회.

team_decision_requests 와 합치지 않는다 -- 그쪽은 런을 재우는 것과 얽혀 있고
(defer_run_for_user_decision), 디자인 승인은 런을 재우지 않는다. 한 표에 넣으면
status 를 읽는 기존 코드가 디자인 항목에도 반응한다.
"""

import pytest

from personal_agent_gateway.team_design_reviews import TeamDesignReviewService
from tests.helpers_team import make_operation_runtime_with_completed_worker


def _service(tmp_path):
    setup = make_operation_runtime_with_completed_worker(tmp_path)
    return TeamDesignReviewService(setup.db), setup


def _create(service, setup, **overrides):
    task = setup.teams.list_tasks(setup.run.id, setup.cycle.id)[0]
    fields = {
        "team_run_id": setup.run.id,
        "cycle_id": setup.cycle.id,
        "task_id": task.id,
        "artifact_id": "artifact-1",
        "screen": "home",
        "project_root": r"C:\proj",
        "stage": "layout",
    }
    fields.update(overrides)
    return service.create(**fields)


def test_a_new_review_starts_pending(tmp_path):
    service, setup = _service(tmp_path)

    review = _create(service, setup)

    assert review.status == "pending"
    assert review.reason is None
    assert review.answered_at is None
    assert [row.id for row in service.list_pending(setup.run.id)] == [review.id]


def test_accepting_removes_it_from_pending(tmp_path):
    service, setup = _service(tmp_path)
    review = _create(service, setup)

    answered = service.answer(review.id, status="accepted")

    assert answered.status == "accepted"
    assert answered.answered_at is not None
    assert service.list_pending(setup.run.id) == []


def test_rejecting_without_a_reason_is_refused(tmp_path):
    """이유가 없으면 다음 사이클이 같은 것을 다시 만든다.

    화면에서 막는 것만으로는 부족하다 -- API 를 직접 부르는 길이 있다.
    """
    service, setup = _service(tmp_path)
    review = _create(service, setup)

    with pytest.raises(ValueError):
        service.answer(review.id, status="rejected", reason="   ")

    assert service.get(review.id).status == "pending"


def test_a_rejected_review_is_carried_to_the_next_cycle(tmp_path):
    service, setup = _service(tmp_path)
    review = _create(service, setup)

    service.answer(review.id, status="rejected", reason="여백이 너무 넓다")

    carried = service.list_rejected_unresolved(setup.run.id)
    assert [row.reason for row in carried] == ["여백이 너무 넓다"]


def test_a_newer_review_for_the_same_screen_clears_the_old_rejection(tmp_path):
    """다시 만들었으면 옛 반려는 더 이상 들고 갈 것이 아니다.

    그러지 않으면 고친 뒤에도 같은 지적이 계속 프롬프트에 실린다.
    """
    service, setup = _service(tmp_path)
    first = _create(service, setup)
    service.answer(first.id, status="rejected", reason="여백이 너무 넓다")

    _create(service, setup)  # 같은 screen 으로 다시

    assert service.list_rejected_unresolved(setup.run.id) == []


def test_an_unknown_status_is_refused(tmp_path):
    service, setup = _service(tmp_path)
    review = _create(service, setup)

    with pytest.raises(ValueError):
        service.answer(review.id, status="maybe")


def test_a_missing_review_raises(tmp_path):
    service, _setup = _service(tmp_path)

    with pytest.raises(KeyError):
        service.get("nope")
```

> **실행자 참고:** `make_operation_runtime_with_completed_worker` 는 이미 있는 도우미다.
> `tests/test_team_runtime.py` 가 같은 이름으로 임포트하니 그 임포트 경로를 그대로 따라 쓴다.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python -m pytest -q -p no:randomly tests/test_team_design_reviews.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'personal_agent_gateway.team_design_reviews'`

- [ ] **Step 3: Add the migration**

`src/personal_agent_gateway/migrations.py` — `_migration_36_auto_series_zero_interval` 아래에 더한다:

```python
def _migration_37_team_design_reviews(
    connection: sqlite3.Connection,
) -> None:
    """디자인 승인 항목을 담을 표.

    team_decision_requests 와 따로 둔다. 그 표는 런을 재우는 것과 얽혀 있고
    (defer_run_for_user_decision), 디자인 승인은 런을 재우지 않는다. 사장님이
    보는 목록은 하나지만, 합치는 자리는 읽기 API 하나다.
    """
    connection.executescript(
        """
        create table if not exists team_design_reviews (
            id text primary key,
            team_run_id text not null,
            cycle_id text,
            task_id text,
            artifact_id text not null,
            screen text not null,
            project_root text not null,
            stage text not null check (stage in ('layout', 'design')),
            status text not null check (status in ('pending', 'accepted', 'rejected')),
            reason text,
            created_at text not null,
            answered_at text
        );
        create index if not exists idx_team_design_reviews_run
            on team_design_reviews (team_run_id, status);
        create index if not exists idx_team_design_reviews_screen
            on team_design_reviews (team_run_id, screen);
        """
    )
```

그리고 `MIGRATIONS` 끝에 한 줄 더한다:

```python
    (36, "auto-series-zero-interval", _migration_36_auto_series_zero_interval),
    (37, "team-design-reviews", _migration_37_team_design_reviews),
)
```

- [ ] **Step 4: Write the service**

`src/personal_agent_gateway/team_design_reviews.py`:

```python
"""디자인 승인 항목의 저장과 조회.

표를 team_decision_requests 와 합치지 않은 이유는 마이그레이션 37 의 설명에 있다.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

_STATUSES = frozenset({"pending", "accepted", "rejected"})


@dataclass(frozen=True)
class TeamDesignReview:
    id: str
    team_run_id: str
    cycle_id: str | None
    task_id: str | None
    artifact_id: str
    screen: str
    project_root: str
    stage: str
    status: str
    reason: str | None
    created_at: str
    answered_at: str | None


def _from_row(row) -> TeamDesignReview:
    return TeamDesignReview(
        id=row["id"],
        team_run_id=row["team_run_id"],
        cycle_id=row["cycle_id"],
        task_id=row["task_id"],
        artifact_id=row["artifact_id"],
        screen=row["screen"],
        project_root=row["project_root"],
        stage=row["stage"],
        status=row["status"],
        reason=row["reason"],
        created_at=row["created_at"],
        answered_at=row["answered_at"],
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TeamDesignReviewService:
    def __init__(self, db) -> None:
        self._db = db

    def create(
        self,
        *,
        team_run_id: str,
        cycle_id: str | None,
        task_id: str | None,
        artifact_id: str,
        screen: str,
        project_root: str,
        stage: str,
    ) -> TeamDesignReview:
        review_id = uuid.uuid4().hex
        self._db.execute(
            """
            insert into team_design_reviews (
                id, team_run_id, cycle_id, task_id, artifact_id, screen,
                project_root, stage, status, reason, created_at, answered_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, 'pending', null, ?, null)
            """,
            (
                review_id,
                team_run_id,
                cycle_id,
                task_id,
                artifact_id,
                screen,
                project_root,
                stage,
                _now(),
            ),
        )
        return self.get(review_id)

    def get(self, review_id: str) -> TeamDesignReview:
        row = self._db.fetchone(
            "select * from team_design_reviews where id = ?", (review_id,)
        )
        if row is None:
            raise KeyError(f"Design review not found: {review_id}")
        return _from_row(row)

    def list_pending(self, team_run_id: str) -> list[TeamDesignReview]:
        rows = self._db.fetchall(
            """
            select * from team_design_reviews
            where team_run_id = ? and status = 'pending'
            order by created_at
            """,
            (team_run_id,),
        )
        return [_from_row(row) for row in rows]

    def list_rejected_unresolved(self, team_run_id: str) -> list[TeamDesignReview]:
        """반려됐고 그 뒤로 같은 화면을 다시 만들지 않은 것만.

        다시 만들었으면 옛 지적은 더 이상 들고 갈 것이 아니다. 그러지 않으면
        고친 뒤에도 같은 지적이 계속 프롬프트에 실린다.
        """
        rows = self._db.fetchall(
            """
            select * from team_design_reviews as rejected
            where rejected.team_run_id = ?
              and rejected.status = 'rejected'
              and not exists (
                select 1 from team_design_reviews as newer
                where newer.team_run_id = rejected.team_run_id
                  and newer.screen = rejected.screen
                  and newer.created_at > rejected.created_at
              )
            order by rejected.created_at
            """,
            (team_run_id,),
        )
        return [_from_row(row) for row in rows]

    def answer(
        self, review_id: str, *, status: str, reason: str | None = None
    ) -> TeamDesignReview:
        if status not in _STATUSES or status == "pending":
            raise ValueError(f"Unsupported design review status: {status}")
        cleaned = (reason or "").strip()
        # 이유 없는 반려는 다음 사이클이 같은 것을 다시 만들게 한다. 화면에서만
        # 막으면 API 를 직접 부르는 길이 남는다.
        if status == "rejected" and not cleaned:
            raise ValueError("A rejected design review needs a reason")
        self.get(review_id)
        self._db.execute(
            """
            update team_design_reviews
            set status = ?, reason = ?, answered_at = ?
            where id = ?
            """,
            (status, cleaned or None, _now(), review_id),
        )
        return self.get(review_id)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH=src python -m pytest -q -p no:randomly tests/test_team_design_reviews.py tests/test_migrations.py`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/team_design_reviews.py src/personal_agent_gateway/migrations.py tests/test_team_design_reviews.py
git commit -m "feat: 디자인 승인 항목을 담는 표와 저장소"
```

---

## Task 3: 워커 결과에서 항목 만들기

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py` — 임포트(파일 상단 33행 근처), `_validate_worker_result`(4698행 근처), 워커 결과 적용부(3798–3801행 근처)
- Test: `tests/test_team_runtime.py`

**Interfaces:**
- Consumes: `extract_design_review`, `DesignReview` (Task 1) · `TeamDesignReviewService` (Task 2)
- Produces: 워커가 블록을 내면 `team_design_reviews` 행 하나. 파일이 없으면 안 만든다

- [ ] **Step 1: Write the failing test**

`tests/test_team_runtime.py` 끝에 붙인다:

```python
def test_a_worker_asking_for_review_creates_a_pending_item(tmp_path):
    """조각을 따로 부르지 않고 사이클을 끝까지 돌린다.

    앞서 한 번, 화면 코드와 서버 코드가 둘 다 멀쩡한데 중간 배선이 빠져 기능이
    조용히 죽은 적이 있다. 이 기능도 워커 결과에서 표까지 두 군데를 지난다.
    """
    from personal_agent_gateway.team_design_reviews import TeamDesignReviewService

    block = (
        '```design-review\n'
        '{"screen":"home","stage":"layout","file":"out/home.layout.html"}\n'
        "```"
    )
    setup = make_operation_runtime_with_completed_worker(
        tmp_path, worker_summary="배치를 잡았습니다.\n" + block
    )
    working_root = Path(setup.run.working_root or setup.run.workspace_root)
    (working_root / "out").mkdir(parents=True, exist_ok=True)
    (working_root / "out" / "home.layout.html").write_text("<div>x</div>", encoding="utf-8")
    # 산출물로 올라와야 아티팩트가 생기고, 아티팩트가 preview 의 원본이다.
    setup.declare_deliverable("out/home.layout.html", kind="document")

    await_resume(setup)

    reviews = TeamDesignReviewService(setup.db).list_pending(setup.run.id)
    assert [(row.screen, row.stage) for row in reviews] == [("home", "layout")]
    assert reviews[0].project_root == str(working_root)


def test_a_review_is_not_created_when_the_file_was_not_delivered(tmp_path):
    """빈 preview 를 만들지 않는다.

    블록만 적고 그 파일을 deliverables 에 넣지 않으면 preview 로 띄울 아티팩트가
    없다. 항목을 만들면 열었을 때 빈 화면이고, 그것은 "확인했는데 아무것도 없다"
    로 읽힌다.
    """
    from personal_agent_gateway.team_design_reviews import TeamDesignReviewService

    block = (
        '```design-review\n'
        '{"screen":"home","stage":"layout","file":"out/home.layout.html"}\n'
        "```"
    )
    setup = make_operation_runtime_with_completed_worker(
        tmp_path, worker_summary="배치를 잡았습니다.\n" + block
    )

    await_resume(setup)

    assert TeamDesignReviewService(setup.db).list_pending(setup.run.id) == []


def test_the_block_is_stripped_from_what_the_leader_reads(tmp_path):
    """승인 요청은 배선이지 작업 결과가 아니다.

    남겨 두면 리드의 수용 판단과 합성 프롬프트에 JSON 이 섞여 들어간다.
    """
    block = (
        '```design-review\n'
        '{"screen":"home","stage":"layout","file":"out/home.layout.html"}\n'
        "```"
    )
    setup = make_operation_runtime_with_completed_worker(
        tmp_path, worker_summary="배치를 잡았습니다.\n" + block
    )

    await_resume(setup)

    task = setup.teams.list_tasks(setup.run.id, setup.cycle.id)[0]
    stored = json.dumps(setup.teams.get_task(task.id).outcome_json, ensure_ascii=False)
    assert "design-review" not in stored
    assert "배치를 잡았습니다." in stored
```

> **실행자 참고:** `await_resume` 는 이 파일에서 쓰는 기존 방식(`await setup.runtime.resume(setup.run.id, setup.cycle.id)`)을
> 감싼 이름이다. 파일에 그런 도우미가 없으면 각 테스트를 `@pytest.mark.asyncio` 로 바꾸고
> `await setup.runtime.resume(setup.run.id, setup.cycle.id)` 를 그 자리에 직접 쓴다.
> `make_operation_runtime_with_completed_worker` 에 `worker_summary` 인자가 없으면,
> 도우미에 기본값 있는 인자로 더한다 — 기존 호출부는 바뀌지 않는다.
> `setup.declare_deliverable(...)` 도 마찬가지다: 그 도우미가 워커 결과의 `deliverables` 를
> 어떻게 만드는지 보고, 같은 방식으로 한 줄 더하는 도우미를 붙인다. `Deliverable(path, kind)` 이고
> 정의는 `team_outcomes.py:14` 에 있다. **아티팩트는 수용된 작업에만 올라간다**
> (`team_runtime.py:3840`) — 도우미가 만드는 런이 수용까지 가는지 확인한다.

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest -q -p no:randomly tests/test_team_runtime.py -k "asking_for_review or file_is_missing or stripped_from_what"`
Expected: FAIL — 항목이 안 생긴다 (`assert [] == [("home", "layout")]`)

- [ ] **Step 3: Wire the extraction into the validator**

`src/personal_agent_gateway/team_runtime.py` 상단 임포트에 더한다 (33행 `from personal_agent_gateway.team_note_report import extract_team_note` 옆):

```python
from personal_agent_gateway.team_design_review_report import extract_design_review
```

4695–4707행의 `_validate_worker_result` 를 고친다:

```python
        query = _parse_needs_info(response.content)
        if query is not None:
            return ValidatedOperationResult("worker_query", query)
        # 승인 요청은 배선이지 작업 결과가 아니다. 남겨 두면 리드의 수용 판단과
        # 합성 프롬프트에 JSON 이 섞여 들어간다.
        without_review, review = extract_design_review(response.content)
        outcome = parse_task_outcome(without_review)
        summary = self._finalize_persona_content(
            outcome.summary,
            persona_id=worker.persona_id,
            team_run_id=run.id,
        )
        payload = asdict(replace(outcome, summary=summary))
        if review is not None:
            payload["design_review"] = asdict(review)
        return ValidatedOperationResult("task_outcome", payload)
```

- [ ] **Step 4: Create the row where the artifacts are published**

승인 항목은 **아티팩트가 만들어진 뒤에만** 만들 수 있다. 아티팩트가 preview 의 원본이기 때문이다.
`TeamArtifactPublisher.publish` 는 `outcome.deliverables` 를 올리고, **수용된 작업에만** 돈다
(3840행 `if acceptance.accepted and outcome.deliverables:`). 그러니 그 자리에서 만든다 —
수용되지 않은 작업의 디자인을 사장님에게 올리지 않는 것도 옳다.

3844행의 `publish` 호출이 반환값을 버리고 있다. 받도록 고친다:

```python
                published = self._artifact_publisher.publish(
                    run.id,
                    task.cycle_id,
                    task,
                    outcome,
                    working_root,
                )
```

그리고 `except ArtifactPublicationError:` 블록이 끝난 **다음**, `acceptance` 가 여전히 수용일 때 부른다:

```python
            if acceptance.accepted:
                self._record_design_review(operation, run, task, working_root, published)
```

메서드는 `_team_note_block` 근처에 둔다:

```python
    def _record_design_review(
        self,
        operation,
        run: TeamRun,
        task: TeamTask,
        working_root: Path,
        published: tuple,
    ) -> None:
        """워커가 청한 승인을 요청함에 올린다.

        블록이 가리키는 파일이 실제로 산출물로 올라왔을 때만 만든다. 워커가
        블록만 적고 그 파일을 deliverables 에 넣지 않았으면 preview 로 띄울
        아티팩트가 없다. 그때 항목을 만들면 사장님이 열었을 때 빈 화면이고,
        그것은 "확인했는데 아무것도 없다" 로 읽힌다.
        """
        stored = operation.result_json if isinstance(operation.result_json, dict) else {}
        raw = stored.get("design_review")
        if not isinstance(raw, dict):
            return
        screen = raw.get("screen")
        stage = raw.get("stage")
        relative = raw.get("file")
        if not (
            isinstance(screen, str) and isinstance(stage, str) and isinstance(relative, str)
        ):
            return
        artifact = _artifact_for_path(published, relative)
        if artifact is None:
            return
        self._design_reviews.create(
            team_run_id=run.id,
            cycle_id=task.cycle_id,
            task_id=task.id,
            artifact_id=artifact.id,
            screen=screen,
            project_root=str(working_root),
            stage=stage,
        )
```

그리고 모듈 수준 함수 하나를 더한다 (다른 `_` 함수들 옆):

```python
def _artifact_for_path(published: tuple, relative: str):
    """올라온 산출물 중 이 경로를 가리키는 것.

    구분자만 다른 같은 경로를 놓치지 않는다 -- 워커 셸이 윈도우인지 posix 인지
    이 코드는 모르고, 블록에 적히는 표기는 그때그때 다르다.
    """
    wanted = _path_parts(relative)
    for artifact in published:
        metadata = artifact.metadata_json if isinstance(artifact.metadata_json, dict) else {}
        source = metadata.get("source_path")
        if isinstance(source, str) and _path_parts(source) == wanted:
            return artifact
    return None


def _path_parts(path: str) -> tuple[str, ...]:
    return tuple(part for part in re.split(r"[\/]+", path) if part)
```

> **실행자 참고:** `artifact.metadata_json` 의 실제 속성 이름은 `Artifact` 데이터클래스에서 확인한다
> (`artifacts` 표의 컬럼은 `metadata_json` 이다). `publish` 가 metadata 에 `"source_path": deliverable.path`
> 를 넣는 것은 `team_artifact_publisher.py:19` 이하에서 확인할 수 있다.
> `re` 가 이 모듈에 임포트돼 있는지 보고, 없으면 더한다.
> `TeamDesignReviewService` 는 생성자에 새 인자로 더하고, `app.py` 에서 만드는 자리에 함께 넘긴다.

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH=src python -m pytest -q -p no:randomly tests/test_team_runtime.py`
Expected: PASS (기존 것 포함 전부)

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py src/personal_agent_gateway/app.py tests/test_team_runtime.py
git commit -m "feat: 워커가 청한 디자인 승인을 요청함 항목으로 만든다"
```

---

## Task 4: design-studio 클라이언트

**Files:**
- Create: `src/personal_agent_gateway/design_studio_client.py`
- Modify: `src/personal_agent_gateway/config.py` (78행 `claude_permission_mode` 근처, 244행 env 읽는 자리)
- Test: `tests/test_design_studio_client.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `@dataclass(frozen=True) class AcceptResult: ok: bool; status: str; message: str | None`
    - `status` 는 `"accepted" | "unreachable" | "busy" | "refused"`
  - `class DesignStudioClient:`
    - `__init__(self, base_url: str, *, timeout_seconds: float = 15.0)`
    - `accept_layout(self, *, project_root: str, screen: str) -> AcceptResult`

- [ ] **Step 1: Write the failing test**

`tests/test_design_studio_client.py`:

```python
"""design-studio 로 승인을 중계하는 얇은 클라이언트.

design-studio 는 띄우지 않는다. 가짜 서버 하나로 요청의 모양만 본다 --
저쪽이 도는지는 이 계획의 책임이 아니다.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from personal_agent_gateway.design_studio_client import DesignStudioClient


class _Recorder:
    def __init__(self):
        self.calls = []
        self.responses = {}


@pytest.fixture
def studio():
    recorder = _Recorder()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            recorder.calls.append((self.path, body, dict(self.headers)))
            code, payload = recorder.responses.get(self.path, (200, {"ok": True}))
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    recorder.base_url = f"http://127.0.0.1:{server.server_port}"
    yield recorder
    server.shutdown()


def test_accepting_switches_the_project_then_accepts_the_layout(studio):
    """순서가 중요하다.

    design-studio 는 "지금 프로젝트" 를 하나만 기억한다. 전환하지 않고 승인하면
    다른 프로젝트의 배치를 승인한다.
    """
    client = DesignStudioClient(studio.base_url)

    result = client.accept_layout(project_root=r"C:\proj", screen="home")

    assert result.ok is True
    assert result.status == "accepted"
    assert [path for path, _body, _headers in studio.calls] == [
        "/api/project",
        "/api/layout/accept",
    ]
    assert studio.calls[0][1] == {"root": r"C:\proj"}
    assert studio.calls[1][1] == {"screen": "home"}


def test_no_origin_header_is_sent(studio):
    """안내서 4장: 헤더가 셋 다 없는 요청은 통과하고, 남의 Origin 은 403 이다.

    붙이면 오히려 막힌다.
    """
    DesignStudioClient(studio.base_url).accept_layout(
        project_root=r"C:\proj", screen="home"
    )

    for _path, _body, headers in studio.calls:
        assert "Origin" not in headers
        assert "Sec-Fetch-Site" not in headers


def test_a_409_is_reported_as_busy(studio):
    """긴 실행은 동시에 하나뿐이다. 409 는 실패가 아니라 "지금은 안 된다" 다."""
    studio.responses["/api/layout/accept"] = (
        409,
        {"error": "이미 실행이 진행 중이다", "running": "home 화면 만들기"},
    )
    client = DesignStudioClient(studio.base_url)

    result = client.accept_layout(project_root=r"C:\proj", screen="home")

    assert (result.ok, result.status) == (False, "busy")
    assert "home 화면 만들기" in (result.message or "")


def test_a_400_is_reported_as_refused(studio):
    studio.responses["/api/layout/accept"] = (400, {"error": "어느 화면에 쓸지 고른다"})
    client = DesignStudioClient(studio.base_url)

    result = client.accept_layout(project_root=r"C:\proj", screen="home")

    assert (result.ok, result.status) == (False, "refused")
    assert "어느 화면" in (result.message or "")


def test_a_dead_server_is_reported_not_raised():
    """design-studio 는 사장님이 띄우는 것이다. 안 떠 있는 것은 예외가 아니라 상태다."""
    client = DesignStudioClient("http://127.0.0.1:1", timeout_seconds=0.3)

    result = client.accept_layout(project_root=r"C:\proj", screen="home")

    assert (result.ok, result.status) == (False, "unreachable")


def test_the_layout_is_not_accepted_when_the_project_switch_fails(studio):
    """전환이 실패했는데 승인을 보내면 남의 프로젝트를 승인한다."""
    studio.responses["/api/project"] = (400, {"error": "root 가 레지스트리에 없다"})
    client = DesignStudioClient(studio.base_url)

    result = client.accept_layout(project_root=r"C:\proj", screen="home")

    assert result.ok is False
    assert [path for path, _body, _headers in studio.calls] == ["/api/project"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest -q -p no:randomly tests/test_design_studio_client.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'personal_agent_gateway.design_studio_client'`

- [ ] **Step 3: Write the client**

`src/personal_agent_gateway/design_studio_client.py`:

```python
"""design-studio 로 배치 승인을 중계하는 얇은 클라이언트.

저쪽 API 를 다시 구현하지 않는다. 사장님이 PAG 화면에서 누르는 승인 하나만
넘긴다. 그 밖의 조작(지적·고치기·시스템 만들기)은 design-studio 를 연다.
"""

import logging
from dataclasses import dataclass

import httpx

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AcceptResult:
    ok: bool
    status: str
    message: str | None = None


class DesignStudioClient:
    def __init__(self, base_url: str, *, timeout_seconds: float = 15.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    def accept_layout(self, *, project_root: str, screen: str) -> AcceptResult:
        """프로젝트를 전환한 뒤 배치를 승인한다.

        순서가 중요하다 -- design-studio 는 "지금 프로젝트" 를 하나만 기억하므로,
        전환하지 않고 승인하면 다른 프로젝트의 배치를 승인한다. 전환이 실패하면
        승인을 보내지 않는다.

        Origin 과 Sec-Fetch-Site 를 붙이지 않는다. 안내서 4 장: 셋 다 없는 요청은
        통과하고, 남의 Origin 은 403 이다.
        """
        try:
            with httpx.Client(timeout=self._timeout) as client:
                switched = self._post(client, "/api/project", {"root": project_root})
                if not switched.ok:
                    return switched
                return self._post(client, "/api/layout/accept", {"screen": screen})
        except httpx.HTTPError as exc:
            _LOGGER.info("design-studio unreachable: %s", exc)
            return AcceptResult(False, "unreachable", str(exc))

    def _post(self, client: httpx.Client, path: str, payload: dict) -> AcceptResult:
        response = client.post(self._base_url + path, json=payload)
        if response.status_code < 300:
            return AcceptResult(True, "accepted")
        message = self._message(response)
        if response.status_code == 409:
            return AcceptResult(False, "busy", message)
        return AcceptResult(False, "refused", message)

    @staticmethod
    def _message(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text[:400]
        if not isinstance(body, dict):
            return str(body)[:400]
        # running 은 409 일 때만 온다. 무엇이 도는지가 기다릴지 정하는 정보다.
        parts = [str(body.get(key)) for key in ("error", "running") if body.get(key)]
        return " / ".join(parts)[:400] or response.text[:400]
```

- [ ] **Step 4: Add the config value**

`src/personal_agent_gateway/config.py` — 78행 `claude_permission_mode: str = "acceptEdits"` 옆:

```python
    #: design-studio 서버 주소. PAG 는 이 서버를 띄우지 않는다 -- LMG_LOCAL_TOKEN 이
    #: 필요하고 그건 사람 몫이다.
    design_studio_base_url: str = "http://127.0.0.1:7777"
```

244행 근처 env 를 읽는 자리에:

```python
                design_studio_base_url=(
                    env.get("DESIGN_STUDIO_BASE_URL") or "http://127.0.0.1:7777"
                ),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH=src python -m pytest -q -p no:randomly tests/test_design_studio_client.py tests/test_config.py`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/design_studio_client.py src/personal_agent_gateway/config.py tests/test_design_studio_client.py
git commit -m "feat: design-studio 로 배치 승인을 중계하는 얇은 클라이언트"
```

---

## Task 5: 요청함 읽기와 답하기 API

**Files:**
- Modify: `src/personal_agent_gateway/api/team_runs.py` (1070행 `get_decision_request` 근처)
- Modify: `src/personal_agent_gateway/app.py` (서비스를 `app.state` 에 붙이는 자리)
- Test: `tests/test_api_team_runs.py`

**Interfaces:**
- Consumes: `TeamDesignReviewService` (Task 2) · `DesignStudioClient`, `AcceptResult` (Task 4)
- Produces:
  - `GET /api/team-runs/{team_run_id}/inbox` → `{"items": [...]}`
    - 질문 항목: `{"kind":"question","id":…,"blocking":true,"title":…,"question":…,"revision":…}`
    - 디자인 항목: `{"kind":"design","id":…,"blocking":false,"title":…,"screen":…,"stage":…,"artifact_id":…}`
  - `POST /api/team-runs/{team_run_id}/design-reviews/{review_id}/answer`
    - 본문: `{"status":"accepted"|"rejected","reason":"…"}`

- [ ] **Step 1: Write the failing test**

`tests/test_api_team_runs.py` 끝에 붙인다:

```python
def test_the_inbox_merges_questions_and_designs(tmp_path: Path) -> None:
    """사장님이 보는 목록은 하나다. 합치는 자리는 이 API 하나뿐이다.

    blocking 표시가 있어야 한다 -- 질문은 런을 멈추고 디자인은 멈추지 않으므로,
    표시가 없으면 급한 것을 뒤로 미루게 된다.
    """
    client, run_id, review_id = _run_with_a_pending_design_review(tmp_path)

    response = client.get(f"/api/team-runs/{run_id}/inbox")

    assert response.status_code == 200
    items = response.json()["items"]
    design = [item for item in items if item["kind"] == "design"]
    assert [item["id"] for item in design] == [review_id]
    assert design[0]["blocking"] is False
    assert design[0]["screen"] == "home"
    assert design[0]["artifact_id"]


def test_accepting_calls_design_studio(tmp_path: Path) -> None:
    """전환 뒤 승인. 그 순서가 아니면 남의 프로젝트를 승인한다."""
    client, run_id, review_id = _run_with_a_pending_design_review(tmp_path)
    calls: list[tuple[str, str]] = []

    class _Stub:
        def accept_layout(self, *, project_root, screen):
            calls.append((project_root, screen))
            return AcceptResult(True, "accepted")

    client.app.state.design_studio_client = _Stub()

    response = client.post(
        f"/api/team-runs/{run_id}/design-reviews/{review_id}/answer",
        json={"status": "accepted"},
    )

    assert response.status_code == 200
    assert [screen for _root, screen in calls] == ["home"]
    assert response.json()["design_review"]["status"] == "accepted"


def test_a_busy_studio_leaves_the_item_pending(tmp_path: Path) -> None:
    """409 는 "지금은 안 된다" 다. 항목을 처리한 것으로 치우면 승인이 사라진다."""
    client, run_id, review_id = _run_with_a_pending_design_review(tmp_path)

    class _Busy:
        def accept_layout(self, *, project_root, screen):
            return AcceptResult(False, "busy", "home 화면 만들기")

    client.app.state.design_studio_client = _Busy()

    response = client.post(
        f"/api/team-runs/{run_id}/design-reviews/{review_id}/answer",
        json={"status": "accepted"},
    )

    assert response.status_code == 409
    assert "home 화면 만들기" in response.json()["detail"]
    items = client.get(f"/api/team-runs/{run_id}/inbox").json()["items"]
    assert any(item["id"] == review_id for item in items)


def test_rejecting_does_not_call_design_studio(tmp_path: Path) -> None:
    """반려는 저쪽에 보낼 것이 없다. accept 만 사람의 행위다."""
    client, run_id, review_id = _run_with_a_pending_design_review(tmp_path)
    calls: list[str] = []

    class _Stub:
        def accept_layout(self, *, project_root, screen):
            calls.append(screen)
            return AcceptResult(True, "accepted")

    client.app.state.design_studio_client = _Stub()

    response = client.post(
        f"/api/team-runs/{run_id}/design-reviews/{review_id}/answer",
        json={"status": "rejected", "reason": "여백이 너무 넓다"},
    )

    assert response.status_code == 200
    assert calls == []


def test_rejecting_without_a_reason_is_refused(tmp_path: Path) -> None:
    client, run_id, review_id = _run_with_a_pending_design_review(tmp_path)

    response = client.post(
        f"/api/team-runs/{run_id}/design-reviews/{review_id}/answer",
        json={"status": "rejected", "reason": "  "},
    )

    assert response.status_code == 422
```

> **실행자 참고:** `_run_with_a_pending_design_review(tmp_path)` 는 이 파일에 새로 쓰는 도우미다.
> `authenticated_client` 로 클라이언트를 만들고, `TeamDesignReviewService(...).create(...)` 로
> 항목 하나를 직접 넣은 뒤 `(client, run_id, review_id)` 를 돌려준다.
> 아티팩트 id 는 이 파일이 이미 쓰는 아티팩트 생성 방법을 따른다.
> `AcceptResult` 는 `personal_agent_gateway.design_studio_client` 에서 임포트한다.

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest -q -p no:randomly tests/test_api_team_runs.py -k "inbox or design_review or busy_studio"`
Expected: FAIL with 404 (경로가 없다)

- [ ] **Step 3: Add the endpoints**

`src/personal_agent_gateway/api/team_runs.py` — 1070행 `get_decision_request` 근처에 더한다:

```python
class AnswerDesignReviewRequest(BaseModel):
    status: Literal["accepted", "rejected"]
    reason: str | None = None


@router.get("/{team_run_id}/inbox")
def get_inbox(team_run_id: str, request: Request) -> dict[str, object]:
    """사장님에게 온 것 전부. 질문과 디자인을 한 목록으로 준다.

    표는 둘이지만 사장님이 보는 것은 하나다. 합치는 자리는 여기 하나뿐이다.
    """
    service = request.app.state.team_run_service
    reviews = request.app.state.team_design_review_service
    items: list[dict[str, object]] = []
    decision = service.get_active_decision_request(team_run_id)
    if decision is not None:
        for item in decision.items:
            items.append(
                {
                    "kind": "question",
                    "id": item.id,
                    # 질문은 런을 멈춘다. 디자인은 안 멈춘다. 그 차이가 보여야
                    # 급한 것을 뒤로 미루지 않는다.
                    "blocking": True,
                    "title": item.topic or "Decision",
                    "question": item.question,
                    "revision": decision.revision,
                    "decision_request_id": decision.id,
                }
            )
    for review in reviews.list_pending(team_run_id):
        items.append(
            {
                "kind": "design",
                "id": review.id,
                "blocking": False,
                "title": f"{review.screen} {review.stage}",
                "screen": review.screen,
                "stage": review.stage,
                "artifact_id": review.artifact_id,
            }
        )
    return {"items": items}


@router.post("/{team_run_id}/design-reviews/{review_id}/answer")
def answer_design_review(
    team_run_id: str,
    review_id: str,
    payload: AnswerDesignReviewRequest,
    request: Request,
) -> dict[str, object]:
    reviews = request.app.state.team_design_review_service
    try:
        review = reviews.get(review_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if review.team_run_id != team_run_id:
        raise HTTPException(status_code=404, detail="Design review not found")
    if payload.status == "rejected" and not (payload.reason or "").strip():
        raise HTTPException(
            status_code=422, detail="A rejected design review needs a reason"
        )
    if payload.status == "accepted":
        # 저쪽이 승인을 받아들이기 전에는 우리 쪽 상태를 바꾸지 않는다. 바꿔 두면
        # 409 를 받은 승인이 처리된 것으로 보이고, 계약은 안 생긴 채 항목만 사라진다.
        result = request.app.state.design_studio_client.accept_layout(
            project_root=review.project_root, screen=review.screen
        )
        if not result.ok:
            status_code = 409 if result.status == "busy" else 502
            raise HTTPException(
                status_code=status_code, detail=result.message or result.status
            )
    try:
        answered = reviews.answer(
            review_id, status=payload.status, reason=payload.reason
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "design_review": {
            "id": answered.id,
            "status": answered.status,
            "reason": answered.reason,
            "screen": answered.screen,
        }
    }
```

> **실행자 참고:** `decision.items` 의 실제 필드 이름은 `_decision_request_payload`(590행 근처)가
> 무엇을 꺼내는지 보고 맞춘다. 없는 필드를 지어내지 말고 그 함수가 쓰는 이름을 그대로 쓴다.
> `Literal` 임포트가 파일에 없으면 `from typing import Literal` 을 더한다.

- [ ] **Step 4: Wire the services into app state**

`src/personal_agent_gateway/app.py` — 다른 팀 서비스들을 `app.state` 에 붙이는 자리 옆:

```python
    app.state.team_design_review_service = TeamDesignReviewService(db)
    app.state.design_studio_client = DesignStudioClient(config.design_studio_base_url)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH=src python -m pytest -q -p no:randomly tests/test_api_team_runs.py`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/api/team_runs.py src/personal_agent_gateway/app.py tests/test_api_team_runs.py
git commit -m "feat: 요청함 읽기와 디자인 승인·반려 API"
```

---

## Task 6: 반려가 다음 워커에게 돌아간다

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py` (`_team_note_block` 근처 2282행, 워커 프롬프트 합성부 4734행 근처)
- Test: `tests/test_team_runtime.py`

**Interfaces:**
- Consumes: `TeamDesignReviewService.list_rejected_unresolved` (Task 2)
- Produces: `def _design_review_block(self, run: TeamRun) -> str`

- [ ] **Step 1: Write the failing test**

```python
def test_a_rejected_design_reaches_the_next_worker(tmp_path):
    """반려는 그 워커에게 못 간다 -- 워커는 이미 끝났다. 다음 사이클의 일감이다.

    렌더된 프롬프트로 확인한다. 모듈 상수를 보면 .format() 이 안 된 것도
    통과한다 -- 앞서 그것으로 한 번 당했다.
    """
    from personal_agent_gateway.team_design_reviews import TeamDesignReviewService

    setup = make_operation_runtime_with_completed_worker(tmp_path)
    reviews = TeamDesignReviewService(setup.db)
    task = setup.teams.list_tasks(setup.run.id, setup.cycle.id)[0]
    review = reviews.create(
        team_run_id=setup.run.id,
        cycle_id=setup.cycle.id,
        task_id=task.id,
        artifact_id="artifact-1",
        screen="home",
        project_root=str(setup.run.workspace_root),
        stage="layout",
    )
    reviews.answer(review.id, status="rejected", reason="여백이 너무 넓다")

    run = setup.teams.get_team_run(setup.run.id)
    prompt = setup.runtime._worker_prompt(run, setup.worker, task)

    assert "여백이 너무 넓다" in prompt
    assert "home" in prompt


def test_a_pending_design_is_not_carried(tmp_path):
    """아직 안 본 것을 "고쳐라" 로 실으면 사장님이 보기 전에 다시 만든다."""
    from personal_agent_gateway.team_design_reviews import TeamDesignReviewService

    setup = make_operation_runtime_with_completed_worker(tmp_path)
    task = setup.teams.list_tasks(setup.run.id, setup.cycle.id)[0]
    TeamDesignReviewService(setup.db).create(
        team_run_id=setup.run.id,
        cycle_id=setup.cycle.id,
        task_id=task.id,
        artifact_id="artifact-1",
        screen="home",
        project_root=str(setup.run.workspace_root),
        stage="layout",
    )

    run = setup.teams.get_team_run(setup.run.id)
    prompt = setup.runtime._worker_prompt(run, setup.worker, task)

    assert "REJECTED DESIGN" not in prompt


def test_no_rejection_adds_nothing(tmp_path):
    setup = make_operation_runtime_with_completed_worker(tmp_path)
    task = setup.teams.list_tasks(setup.run.id, setup.cycle.id)[0]
    run = setup.teams.get_team_run(setup.run.id)

    prompt = setup.runtime._worker_prompt(run, setup.worker, task)

    assert "REJECTED DESIGN" not in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest -q -p no:randomly tests/test_team_runtime.py -k "rejected_design_reaches or pending_design_is_not_carried or no_rejection_adds"`
Expected: FAIL — `assert '여백이 너무 넓다' in prompt`

- [ ] **Step 3: Add the block**

`src/personal_agent_gateway/team_runtime.py`, `_team_note_block` 아래:

```python
    def _design_review_block(self, run: TeamRun) -> str:
        """사장님이 반려했고 아직 다시 만들지 않은 디자인.

        반려는 그것을 만든 워커에게 갈 수 없다 -- 워커는 초안만 받고 이미
        끝났다. 그래서 다음 사이클의 일감으로 돌아온다. 아직 안 본 것은 싣지
        않는다. 실으면 사장님이 보기 전에 워커가 다시 만든다.
        """
        rejected = self._design_reviews.list_rejected_unresolved(run.id)
        if not rejected:
            return ""
        lines = [
            f"- {review.screen} ({review.stage}): {review.reason}"
            for review in rejected
        ]
        return (
            "REJECTED DESIGN\n"
            "사장님이 아래를 반려했다. 다시 맡길 때 이유를 브리프에 그대로 담아라.\n"
            + "\n".join(lines)
            + "\n\n"
        )
```

4734행의 프롬프트 합성에 끼운다:

```python
        ) + self._team_note_block(run) + self._design_review_block(run) + WORKER_PROMPT.format(
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python -m pytest -q -p no:randomly tests/test_team_runtime.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "feat: 반려된 디자인의 이유를 다음 사이클 워커에게 돌려준다"
```

---

## Task 7: 워커 프롬프트에 design-studio 사용법

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py` (`WORKER_PROMPT`, 166행 시작)
- Test: `tests/test_team_runtime.py`

**Interfaces:**
- Consumes: 없음
- Produces: 렌더된 워커 프롬프트에 design-studio 사용법이 들어간다

- [ ] **Step 1: Write the failing test**

```python
def test_the_worker_is_told_how_to_hand_design_to_design_studio(tmp_path):
    """모르면 안 쓴다. 그리고 잘못 쓰면 저쪽의 함정에 그대로 빠진다.

    특히 셋: 맥락을 비우면 에이전트가 이름과 값을 지어내고, 409 는 실패가
    아니라 "지금은 안 된다" 이며, 시스템 만들기는 사람의 승인이 필요해 워커의
    일이 아니다.
    """
    setup = make_operation_runtime_with_completed_worker(tmp_path)
    task = setup.teams.list_tasks(setup.run.id, setup.cycle.id)[0]
    run = setup.teams.get_team_run(setup.run.id)

    prompt = setup.runtime._worker_prompt(run, setup.worker, task)

    assert "/api/run" in prompt
    assert "/api/project/context" in prompt
    assert "design-review" in prompt
    # 409 를 실패로 읽으면 워커가 포기한다.
    assert "409" in prompt
    # 시스템 만들기는 사람의 승인이 필요하다 -- 워커의 일이 아니다.
    assert "만들지 마라" in prompt
    # 산출물로 안 내면 preview 로 띄울 아티팩트가 없고 사장님은 못 본다.
    assert "deliverables" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python -m pytest -q -p no:randomly tests/test_team_runtime.py -k "hand_design_to_design_studio"`
Expected: FAIL — `assert '/api/run' in prompt`

- [ ] **Step 3: Extend WORKER_PROMPT**

`WORKER_PROMPT` 끝(오늘 더한 대기 규칙 다음)에 붙인다. `.format()` 을 거치므로 **중괄호는 두 번 쓴다**:

```
When the assignment needs a screen designed, hand it to design-studio at
http://127.0.0.1:7777 instead of inventing one. It holds the design system, and
what it produces is a self-contained HTML file.

    GET  /api/systems                       pick one. Do not create one -- a new
                                            system needs source files and a human's
                                            approval, so 만들지 마라. If none fits,
                                            say so in your result and stop there.
    GET  /api/projects                      read the registered root verbatim
    POST /api/projects  {{"root": <your Working root>, "system": <picked>}}
    POST /api/project   {{"root": <same value>}}
    POST /api/project/context  {{"context": "- …"}}
    POST /api/run       {{"brief": "…", "screen": "home", "stage": "layout"}}

Never skip the context call. Left empty, the design run invents names, columns and
states -- it is told 32KB about the system and nothing about what you are building.
Write only settled facts: names, values, states. Not taste.

/api/run is a long stream. Do not wait on it in one command. Throw it detached,
then watch GET /api/state -- its `running` field goes null when the run ends --
and read GET /api/runs?limit=1 for `ok`. A 409 means another run is in flight, not
a failure: it tells you what is running, so wait and send again.

When the result is worth the owner's eyes, end your result with this block. It is
the only way the owner sees it:

    ```design-review
    {{"screen":"home","stage":"layout","file":"out/home.layout.html"}}
    ```

The file path is relative to your Working root, and **the same path must be in your
deliverables** -- that is what turns it into something the owner can open. A block
whose file was never delivered is dropped, and the owner never sees the design.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python -m pytest -q -p no:randomly tests/test_team_runtime.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "feat: 워커에게 design-studio 에 디자인을 맡기는 법을 알려준다"
```

---

## Task 8: 요청함 모달

**Files:**
- Create: `frontend/src/components/organisms/TeamRunDetail/InboxModal.jsx`
- Modify: `frontend/src/components/organisms/TeamRunDetail/index.jsx` (인라인 패널 872행 근처)
- Modify: `frontend/src/api/client.js` (487행 `reopenTeamRun` 근처)
- Modify: `src/personal_agent_gateway/static/styles.css`
- Test: `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`, `frontend/src/api/client.test.js`

**Interfaces:**
- Consumes: `GET /inbox`, `POST /design-reviews/{id}/answer` (Task 5)
- Produces:
  - `api.teamRunInbox(id) -> {items: []}`
  - `api.answerDesignReview(runId, reviewId, {status, reason}) -> object|null`
  - `<InboxModal items open onClose onAnswerDesign onAnswerQuestion />`

- [ ] **Step 1: Write the failing tests**

`frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx` 끝:

```jsx
describe("받은 요청함", () => {
  const items = [
    { kind: "question", id: "q1", blocking: true, title: "DB 선택",
      question: "SQLite 인가 Postgres 인가?", revision: 2 },
    { kind: "design", id: "d1", blocking: false, title: "home layout",
      screen: "home", stage: "layout", artifact_id: "a1" }
  ];

  it("질문이면 답 칸을, 디자인이면 preview 를 보여준다", async () => {
    render(<InboxModal items={items} open onClose={vi.fn()}
      onAnswerDesign={vi.fn()} onAnswerQuestion={vi.fn()} />);

    // 목록의 첫 항목이 열려 있다.
    expect(screen.getByLabelText("답")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /home layout/ }));

    // 자기완결 HTML 이라 iframe 하나면 된다.
    expect(screen.getByTitle("home layout")).toBeInTheDocument();
    expect(screen.queryByLabelText("답")).not.toBeInTheDocument();
  });

  it("런을 멈춘 항목에 표시가 붙는다", () => {
    // 질문은 런을 멈추고 디자인은 안 멈춘다. 표시가 없으면 급한 것을 미룬다.
    render(<InboxModal items={items} open onClose={vi.fn()}
      onAnswerDesign={vi.fn()} onAnswerQuestion={vi.fn()} />);

    expect(screen.getByLabelText("런이 멈춰 있음")).toBeInTheDocument();
  });

  it("이유 없이 반려할 수 없다", async () => {
    const onAnswerDesign = vi.fn();
    render(<InboxModal items={items} open onClose={vi.fn()}
      onAnswerDesign={onAnswerDesign} onAnswerQuestion={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: /home layout/ }));
    await userEvent.click(screen.getByRole("button", { name: "반려" }));

    expect(screen.getByRole("button", { name: "반려 보내기" })).toBeDisabled();
    expect(onAnswerDesign).not.toHaveBeenCalled();
  });

  it("이유를 적으면 반려가 나간다", async () => {
    const onAnswerDesign = vi.fn().mockResolvedValue(true);
    render(<InboxModal items={items} open onClose={vi.fn()}
      onAnswerDesign={onAnswerDesign} onAnswerQuestion={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: /home layout/ }));
    await userEvent.click(screen.getByRole("button", { name: "반려" }));
    await userEvent.type(screen.getByLabelText("반려 이유"), "여백이 너무 넓다");
    await userEvent.click(screen.getByRole("button", { name: "반려 보내기" }));

    expect(onAnswerDesign).toHaveBeenCalledWith("d1", {
      status: "rejected", reason: "여백이 너무 넓다"
    });
  });

  it("승인은 바로 나간다", async () => {
    const onAnswerDesign = vi.fn().mockResolvedValue(true);
    render(<InboxModal items={items} open onClose={vi.fn()}
      onAnswerDesign={onAnswerDesign} onAnswerQuestion={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: /home layout/ }));
    await userEvent.click(screen.getByRole("button", { name: "승인" }));

    expect(onAnswerDesign).toHaveBeenCalledWith("d1", { status: "accepted" });
  });
});
```

`frontend/src/api/client.test.js` 끝:

```js
it("teamRunInbox 는 항목을 그대로 돌려준다", async () => {
  // 매핑을 허용 목록으로 쓰면 서버가 보낸 칸이 조용히 사라진다 -- 앞서
  // usage_totals 와 plan_shape 이 그렇게 죽었다.
  fetch.mockResolvedValueOnce(jsonResponse({
    items: [{ kind: "design", id: "d1", blocking: false, artifact_id: "a1" }]
  }));

  const result = await api.teamRunInbox("run-1");

  expect(result.items).toEqual([
    { kind: "design", id: "d1", blocking: false, artifact_id: "a1" }
  ]);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx src/api/client.test.js`
Expected: FAIL — `InboxModal is not defined`, `api.teamRunInbox is not a function`

- [ ] **Step 3: Add the client functions**

`frontend/src/api/client.js`, `reopenTeamRun` 근처:

```js
  async teamRunInbox(id) {
    const body = await jsonOrNull(await fetch(
      `/api/team-runs/${encodeURIComponent(id)}/inbox`
    ));
    return { items: body?.items || [] };
  },
  async answerDesignReview(runId, reviewId, payload) {
    const body = await jsonOrNull(await fetch(
      `/api/team-runs/${encodeURIComponent(runId)}/design-reviews/${encodeURIComponent(reviewId)}/answer`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      }
    ));
    return body?.design_review || null;
  },
```

- [ ] **Step 4: Write the modal**

`frontend/src/components/organisms/TeamRunDetail/InboxModal.jsx`:

```jsx
import { useState } from "react";
import Button from "../../atoms/Button";

/* 질문과 디자인은 표가 다르지만 사장님이 보는 것은 한 목록이다. 갈리는 것은
   본문뿐이다 -- 질문이면 답 칸, 디자인이면 preview 와 승인·반려. */
export default function InboxModal({
  items = [], open, onClose, onAnswerDesign, onAnswerQuestion
}) {
  const [selectedId, setSelectedId] = useState(items[0]?.id || null);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [answer, setAnswer] = useState("");

  if (!open) return null;
  const selected = items.find((item) => item.id === selectedId) || items[0] || null;

  function select(item) {
    setSelectedId(item.id);
    setRejecting(false);
    setReason("");
    setAnswer("");
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-label="받은 요청">
      <div className="modal inbox-modal">
        <div className="inbox-head">
          <h2 className="headline">받은 요청 {items.length}</h2>
          <Button size="btn-sm" onClick={onClose}>닫기</Button>
        </div>
        <div className="inbox-body">
          <ul className="inbox-list">
            {items.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className={`inbox-item${item.id === selected?.id ? " is-selected" : ""}`}
                  onClick={() => select(item)}
                >
                  <span className="mono inbox-kind">
                    {item.kind === "design" ? "디자인" : "질문"}
                  </span>
                  {/* 질문은 런을 멈추고 디자인은 안 멈춘다. 그 차이가 안 보이면
                      급한 것을 뒤로 미루게 된다. */}
                  {item.blocking ? (
                    <span className="inbox-blocking" aria-label="런이 멈춰 있음">⏸</span>
                  ) : null}
                  <span className="inbox-title">{item.title}</span>
                </button>
              </li>
            ))}
          </ul>
          <div className="inbox-detail">
            {selected?.kind === "design" ? (
              <>
                {/* 산출물은 외부 자원이 없는 자기완결 HTML 이다 -- P0 규칙이 막는다. */}
                <iframe
                  className="inbox-preview"
                  title={selected.title}
                  src={api.artifactContentUrl(selected.artifact_id)}
                />
                {rejecting ? (
                  <>
                    <label className="inbox-field">
                      <span>반려 이유</span>
                      <textarea
                        aria-label="반려 이유"
                        value={reason}
                        onChange={(event) => setReason(event.target.value)}
                      />
                    </label>
                    <Button
                      size="btn-sm"
                      variant="danger"
                      disabled={!reason.trim()}
                      onClick={() => onAnswerDesign(selected.id, {
                        status: "rejected", reason: reason.trim()
                      })}
                    >
                      반려 보내기
                    </Button>
                  </>
                ) : (
                  <div className="inbox-actions">
                    <Button
                      size="btn-sm"
                      variant="primary"
                      onClick={() => onAnswerDesign(selected.id, { status: "accepted" })}
                    >
                      승인
                    </Button>
                    <Button size="btn-sm" onClick={() => setRejecting(true)}>반려</Button>
                  </div>
                )}
              </>
            ) : selected ? (
              <>
                <p className="inbox-question">{selected.question}</p>
                <label className="inbox-field">
                  <span>답</span>
                  <textarea
                    aria-label="답"
                    value={answer}
                    onChange={(event) => setAnswer(event.target.value)}
                  />
                </label>
                <Button
                  size="btn-sm"
                  variant="primary"
                  disabled={!answer.trim()}
                  onClick={() => onAnswerQuestion(selected, answer.trim())}
                >
                  답변
                </Button>
              </>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
```

> **실행자 참고:** `api.artifactContentUrl(id)` 는 `client.js:213` 에 이미 있다
> (`/api/artifacts/{id}/content`). 문자열을 손으로 만들지 말고 그 함수를 쓴다 — `ArtifactModal` 도
> 그렇게 한다(`index.jsx:73`). `api` 임포트와 `Button` 의 경로·`variant` 값은 같은 폴더의
> 기존 컴포넌트에서 확인한다.

- [ ] **Step 5: Wire the button into TeamRunDetail**

`index.jsx` 872행의 인라인 패널(`team-decision-panel`)을 버튼으로 바꾼다. 기존 답변 경로
(`onAnswerDecision`)는 그대로 두고, 모달의 `onAnswerQuestion` 이 그것을 부른다.

```jsx
  const [inboxOpen, setInboxOpen] = useState(false);
  const [inbox, setInbox] = useState({ items: [] });
  // 상세를 다시 읽을 때 같이 읽는다. 새 폴링을 만들지 않는다.
  useEffect(() => {
    let alive = true;
    api.teamRunInbox(run.id).then((next) => { if (alive) setInbox(next); });
    return () => { alive = false; };
  }, [run.id, detailVersion]);
```

```jsx
  {inbox.items.length ? (
    <Button size="btn-sm" onClick={() => setInboxOpen(true)}>
      받은 요청 {inbox.items.length}
    </Button>
  ) : null}
  <InboxModal
    items={inbox.items}
    open={inboxOpen}
    onClose={() => setInboxOpen(false)}
    onAnswerDesign={async (id, payload) => {
      await api.answerDesignReview(run.id, id, payload);
      setInbox(await api.teamRunInbox(run.id));
    }}
    onAnswerQuestion={onAnswerDecision}
  />
```

> **실행자 참고:** `detailVersion` 은 이 컴포넌트가 상세를 다시 읽을 때 바뀌는 값의 이름이다.
> 그런 값이 없으면 상세를 새로 받는 자리에서 `teamRunInbox` 도 같이 부른다 — **새 폴링을 만들지 않는다.**
> 폴링을 하나 더 만들면 팀런 상세가 이미 자주 도는 위에 요청이 두 배가 된다.

- [ ] **Step 6: Add the styles**

`src/personal_agent_gateway/static/styles.css` 끝:

```css
/* 왼쪽은 목록, 오른쪽은 상세. 여러 건을 연달아 처리하는 모양이다. */
.inbox-modal { width: min(1040px, 92vw); }
.inbox-head { display: flex; justify-content: space-between; align-items: center; }
.inbox-body { display: flex; gap: 14px; margin-top: 12px; }
.inbox-list { flex: 0 0 30%; list-style: none; margin: 0; padding: 0;
  border-right: 1px solid var(--c-border); }
.inbox-item { display: flex; gap: 6px; align-items: center; width: 100%;
  text-align: left; background: none; border: 0; padding: 8px; border-radius: 5px;
  color: inherit; cursor: pointer; }
.inbox-item.is-selected { background: var(--c-surface-2); }
.inbox-kind { font-size: 10px; color: var(--c-grey); }
.inbox-blocking { font-size: 11px; }
.inbox-detail { flex: 1; display: flex; flex-direction: column; gap: 10px; }
/* 배치 승인은 눈으로 하는 판단이라 화면을 화면 크기로 봐야 한다. */
.inbox-preview { width: 100%; height: 460px; border: 1px solid var(--c-border);
  border-radius: 6px; background: #fff; }
.inbox-field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; }
.inbox-field textarea { min-height: 72px; }
.inbox-actions { display: flex; gap: 8px; }
```

> **실행자 참고:** `--c-border`, `--c-surface-2` 같은 변수 이름은 `styles.css` 에 실제로 있는 것으로 맞춘다.
> 없으면 그 파일이 이미 쓰는 이름을 쓴다.

- [ ] **Step 7: Run tests and build**

Run:
```bash
cd frontend && npx vitest run && npx vite build
```
Expected: PASS, 빌드 성공

- [ ] **Step 8: Commit**

```bash
git add frontend/src src/personal_agent_gateway/static/styles.css
git commit -m "feat: 받은 요청함 모달 -- 질문과 디자인 승인을 한 자리에서"
```

---

## 스펙 대조에서 나온 판단 하나

스펙 §6.6 이 이렇게 적고 있다: *"`stale`(승인 뒤 배치를 다시 잡은 상태)이면 계약이 아니다.
그 항목은 요청함에 다시 올린다."* **이 계획에는 `stale` 을 묻는 코드가 없다.** 필요 없기 때문이다.

워커가 같은 화면의 배치를 다시 잡으면 블록을 다시 내고, Task 3 이 **새 항목**을 만든다. 그 항목이
`pending` 으로 목록에 오르므로 "다시 올린다" 는 동작은 저절로 일어난다. `GET /api/layout` 을 불러
`stale` 을 확인하는 것은 같은 결과를 더 비싸게 얻는 길이다.

옛 `accepted` 행은 그대로 둔다. 그때 승인했다는 사실이고, 그것은 사실이다.

---

## 마무리

- [ ] **전체 시험**

```bash
ruff check src/ tests/
PYTHONPATH=src python -m pytest -q -p no:randomly
cd frontend && npx vitest run && npx vite build
```

파이썬 전체는 약 19분 걸린다. **`| tail` 로 자르지 말고 요약 줄을 읽어라** — 잘라 보면 종료 코드가 가려져
실패를 성공으로 읽는다.

- [ ] **사람이 한 바퀴 돌려본다**

이건 자동 시험이 못 잡는다. design-studio 를 띄우고(LMG + `LMG_LOCAL_TOKEN` 필요),
디자인을 맡기는 사이클을 한 번 돌린 뒤 요청함에서 승인해 본다.
`.design/layouts/<화면>.md` 가 생기면 계약까지 닿은 것이다.

**첫 사이클은 "맞는 시스템이 없습니다" 라는 질문으로 끝날 가능성이 높다** — 지금 시스템은 `airbnb` 하나뿐이다.
그것은 실패가 아니라 설계된 동작이다(스펙 §11).
