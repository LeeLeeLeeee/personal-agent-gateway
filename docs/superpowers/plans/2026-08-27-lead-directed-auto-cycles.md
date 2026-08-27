# 리드가 다음 사이클을 정하는 자동 반복 — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 자동 반복 사이클의 다음 지시를, 런의 목표를 되풀이하는 대신 직전 사이클에서 리드가 낸 제안으로 바꾼다. 제안이 없으면 시리즈를 거기서 끝낸다.

**Architecture:** 리드는 합성 응답에 선택 펜스 블록(` ```next-cycle `)으로 다음 할 일을 낸다. `team_note_report.py` 와 같은 방식이다 — 순수 함수가 블록을 떼어내고, 아무것도 던지지 않는다. 제안은 합성 결과 payload 의 선택 칸(`next_cycle`)으로 저장되고, 자동 요청을 만들 때 `_auto_instruction` 이 직전 사이클의 그 값을 읽는다. 새 표도 마이그레이션도 없다.

**Tech Stack:** Python 3.13, FastAPI, SQLite, pytest / React, vitest

**Spec:** `docs/superpowers/specs/2026-08-27-lead-directed-auto-cycles-design.md`

## Global Constraints

- 합성 경로에서 예외를 던지지 않는다. 합성은 리드 단계이고, 여기서 터지면 사이클 하나가 통째로 죽는다. 선택인 것이 필수인 것을 죽여서는 안 된다.
- 새 표, 새 컬럼, 마이그레이션을 만들지 않는다. `LATEST_SCHEMA_VERSION` 은 35 그대로다.
- `AutoSeriesStatus` 에 새 값을 더하지 않는다. 끝난 것은 `auto_completed` 이고, 왜 끝났는지는 `pause_reason` 이 담는다.
- 백엔드 테스트는 `PYTHONPATH=src python -m pytest -q -p no:randomly` 가 기준이다. 요약 줄을 읽는다 — 파이프를 타면 종료 코드가 가려진다.
- 프론트는 `cd frontend && npx vitest run`, 빌드는 `npx vite build`.
- 커밋 메시지는 한국어, 왜 그렇게 했는지를 적는다.

---

### Task 1: 합성 응답에서 다음 사이클 제안을 떼어내는 순수 함수

**Files:**
- Create: `src/personal_agent_gateway/team_next_cycle_report.py`
- Test: `tests/test_team_next_cycle_report.py`

**Interfaces:**
- Consumes: 없음
- Produces: `extract_next_cycle(text: str) -> tuple[str, str | None]` — (블록을 뗀 요약, 지시 문자열 또는 None)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
from personal_agent_gateway.team_next_cycle_report import extract_next_cycle


def test_no_block_means_the_lead_had_nothing_to_propose():
    """더 할 일이 없다는 신호다. 시리즈는 여기서 끝난다."""
    summary, instruction = extract_next_cycle("세 일감을 마쳤습니다.")

    assert summary == "세 일감을 마쳤습니다."
    assert instruction is None


def test_the_proposal_is_lifted_out_of_the_summary():
    text = (
        "두 일감을 마쳤습니다.\n"
        "```next-cycle\n"
        '{"instruction":"6문장을 다시 돌려 실제 게시 수를 재라"}\n'
        "```"
    )

    summary, instruction = extract_next_cycle(text)

    assert summary == "두 일감을 마쳤습니다."
    assert instruction == "6문장을 다시 돌려 실제 게시 수를 재라"


def test_broken_json_costs_the_proposal_and_nothing_else():
    """합성은 리드 단계라 여기서 던지면 사이클이 죽는다."""
    text = "요약입니다.\n```next-cycle\n{이건 JSON 이 아니다\n```"

    summary, instruction = extract_next_cycle(text)

    assert summary == "요약입니다."
    assert instruction is None


def test_an_empty_instruction_is_not_a_proposal():
    """빈 지시로 사이클을 열면 팀이 무엇을 하라는지 모른 채 시작한다."""
    text = '요약.\n```next-cycle\n{"instruction":"   "}\n```'

    _summary, instruction = extract_next_cycle(text)

    assert instruction is None


def test_a_proposal_does_not_collide_with_the_other_blocks():
    """세 블록 모두 선택이고 모두 펜스다. 한쪽이 다른 쪽을 먹으면 안 된다."""
    from personal_agent_gateway.team_coverage_report import extract_coverage_gaps
    from personal_agent_gateway.team_note_report import extract_team_note

    text = (
        "요약입니다.\n"
        "```next-cycle\n"
        '{"instruction":"다음 일"}\n'
        "```\n"
        "```team-note\n"
        '{"title":"노트","content_markdown":"본문"}\n'
        "```\n"
        "```coverage-gaps\n"
        '[{"obligation":"로그인","document":"spec.md §2","note":"주인 없음"}]\n'
        "```"
    )

    without_next, instruction = extract_next_cycle(text)
    without_note, note = extract_team_note(without_next)
    summary, gaps = extract_coverage_gaps(without_note)

    assert instruction == "다음 일"
    assert note.title == "노트"
    assert gaps[0]["obligation"] == "로그인"
    assert summary == "요약입니다."


def test_a_non_string_response_does_not_raise():
    assert extract_next_cycle(None) == ("", None)
```

- [ ] **Step 2: 실패하는지 돌려서 확인한다**

Run: `PYTHONPATH=src python -m pytest tests/test_team_next_cycle_report.py -q -p no:randomly`
Expected: FAIL — `ModuleNotFoundError: No module named 'personal_agent_gateway.team_next_cycle_report'`

- [ ] **Step 3: 최소 구현을 쓴다**

```python
"""리드가 사이클 끝에 내는 다음 사이클 제안을 합성 응답에서 꺼내는 순수 함수.

`extract_team_note`, `extract_coverage_gaps` 와 같은 자리, 같은 규칙이다.
합성은 리드 단계라 여기서 예외가 나면 사이클 하나가 통째로 죽는다. 제안은
선택이므로, 선택인 것이 필수인 것을 죽일 수 있으면 안 된다 -- 이 함수는
아무것도 던지지 않고, 읽을 수 없는 것은 없는 것으로 본다.
"""

import json
import re

_BLOCK = re.compile(r"```next-cycle\s*\n(.*?)\n?```", re.DOTALL)
#: 다음 사이클의 지시 하나다. 이보다 길면 사이클 지시가 아니라 보고서다.
MAX_INSTRUCTION_CHARS = 2_000


def extract_next_cycle(text: str) -> tuple[str, str | None]:
    """요약에서 다음 사이클 제안을 떼어내고, 요약과 지시를 돌려준다.

    제안이 없으면 (요약, None). 리드가 더 할 일이 없다고 판단한 경우이고,
    그것이 시리즈를 끝내는 신호다.

    첫 블록만 읽는다. 뒤에 더 있으면 요약에 그대로 남는데, 그 편이 조용히
    지우는 것보다 낫다 -- 사람이 보면 리드가 형식을 잘못 지켰다는 것을 안다.
    """
    if not isinstance(text, str):
        return "", None
    match = _BLOCK.search(text or "")
    if match is None:
        return (text or "").strip(), None
    summary = (text[: match.start()] + text[match.end() :]).strip()
    try:
        payload = json.loads(match.group(1))
    except (TypeError, ValueError):
        return summary, None
    if not isinstance(payload, dict):
        return summary, None
    instruction = payload.get("instruction")
    if not isinstance(instruction, str) or not instruction.strip():
        return summary, None
    return summary, instruction.strip()[:MAX_INSTRUCTION_CHARS]
```

- [ ] **Step 4: 통과하는지 돌려서 확인한다**

Run: `PYTHONPATH=src python -m pytest tests/test_team_next_cycle_report.py -q -p no:randomly`
Expected: PASS — 6 passed

- [ ] **Step 5: 커밋한다**

```bash
git add src/personal_agent_gateway/team_next_cycle_report.py tests/test_team_next_cycle_report.py
git commit -m "feat: 합성 응답에서 다음 사이클 제안을 떼어낸다

팀 노트·커버리지 갭과 같은 자리, 같은 규칙이다. 합성은 리드 단계라 여기서
예외가 나면 사이클 하나가 통째로 죽는다 -- 선택인 것이 필수인 것을 죽여서는
안 되므로 아무것도 던지지 않고, 읽을 수 없는 것은 없는 것으로 본다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 합성 결과에 제안을 싣고 payload 검증을 넓힌다

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py` (`_validated_synthesis_result`, import 줄)
- Modify: `src/personal_agent_gateway/team_model_effects.py` (`_valid_synthesis`)
- Test: `tests/test_team_model_operations.py`

**Interfaces:**
- Consumes: `extract_next_cycle(text) -> tuple[str, str | None]` (Task 1)
- Produces: 합성 payload 의 선택 칸 `next_cycle: str` — `_operation_next_cycle(operation) -> str | None` 로 읽는다 (Task 3 에서 씀)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_team_model_operations.py` 끝에 더한다:

```python
def test_a_synthesis_payload_may_carry_a_next_cycle_instruction():
    """제안은 선택이다. 없어도 유효하고, 있어도 유효해야 한다."""
    assert _valid_synthesis({"summary": "끝냈습니다", "next_cycle": "다음 일"})
    assert _valid_synthesis({"summary": "끝냈습니다"})


def test_an_empty_next_cycle_instruction_is_rejected():
    """빈 지시로 사이클을 열면 팀이 무엇을 하라는지 모른 채 시작한다."""
    assert not _valid_synthesis({"summary": "끝냈습니다", "next_cycle": "  "})
    assert not _valid_synthesis({"summary": "끝냈습니다", "next_cycle": 3})
```

`_valid_synthesis` 는 이미 이 파일 상단에서 import 되어 있다.

- [ ] **Step 2: 실패하는지 돌려서 확인한다**

Run: `PYTHONPATH=src python -m pytest tests/test_team_model_operations.py -q -p no:randomly -k next_cycle`
Expected: FAIL — `assert not True` (지금은 `next_cycle` 이 알 수 없는 칸이라 첫 테스트가 False 를 돌려주고, 둘째는 통과하지 못한다)

- [ ] **Step 3: 최소 구현을 쓴다**

`team_model_effects.py` 의 `_valid_synthesis` 에서 선택 칸 집합에 더한다:

```python
    optional = {"contract_payload", "coverage_gaps", "team_note", "next_cycle"}
```

같은 함수의 `return True` 바로 앞에 검사를 더한다:

```python
    if "next_cycle" in payload:
        instruction = payload["next_cycle"]
        if not isinstance(instruction, str) or not instruction.strip():
            return False
```

`team_runtime.py` 의 import 줄 옆에 더한다:

```python
from personal_agent_gateway.team_next_cycle_report import extract_next_cycle
```

`_validated_synthesis_result` 에서 노트를 떼어내는 줄 **앞**에 제안을 떼어낸다:

```python
        without_next, next_cycle = extract_next_cycle(content)
        without_note, note = extract_team_note(without_next)
        summary, gaps = extract_coverage_gaps(without_note)
```

(기존 두 줄 `without_note, note = extract_team_note(content)` / `summary, gaps = extract_coverage_gaps(without_note)` 를 위 세 줄로 바꾼다.)

그리고 payload 를 만드는 곳, `if note is not None:` 블록 다음에 더한다:

```python
        if next_cycle is not None:
            payload["next_cycle"] = next_cycle
```

- [ ] **Step 4: 통과하는지 돌려서 확인한다**

Run: `PYTHONPATH=src python -m pytest tests/test_team_model_operations.py -q -p no:randomly`
Expected: PASS — 전부 통과

- [ ] **Step 5: 커밋한다**

```bash
git add src/personal_agent_gateway/team_runtime.py src/personal_agent_gateway/team_model_effects.py tests/test_team_model_operations.py
git commit -m "feat: 합성 결과가 다음 사이클 제안을 싣는다

선택 칸이므로 없어도 유효하다. 빈 지시는 거절한다 -- 그것으로 사이클을 열면
팀이 무엇을 하라는지 모른 채 시작한다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: 리드에게 제안을 요청하는 프롬프트

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py` (`NEXT_CYCLE_PROMPT` 신설, `_next_cycle_block`, 합성 프롬프트 조립)
- Test: `tests/test_team_runtime.py`

**Interfaces:**
- Consumes: 없음
- Produces: `TeamRuntime._next_cycle_block(run: TeamRun, contract: OutputContract | None, cycle_id: str | None) -> str`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_team_runtime.py` 끝에 더한다. `_note_setup` 헬퍼가 이 파일에 이미 있다:

```python
@pytest.mark.asyncio
async def test_the_lead_is_asked_for_the_next_cycle_instruction(tmp_path):
    """리드는 이미 요약에 다음 할 일을 쓰고 있다. 기계가 읽을 수 있는 자리로
    옮기는 것뿐이다."""
    setup, _archive, _team_id = _note_setup(tmp_path, "끝냈습니다.")

    await setup.runtime.resume(setup.run.id, setup.cycle.id)

    synthesis_prompt = setup.lead_client.messages[-1][0]["content"]
    assert "NEXT CYCLE" in synthesis_prompt
    assert "```next-cycle" in synthesis_prompt


def test_the_worker_is_not_asked_for_the_next_cycle_instruction(tmp_path):
    """작업자 응답은 엄격한 JSON 형식이다. 쓰라고 하지도 않은 블록 예시를
    보여주면 흉내 낼 이유만 준다."""
    setup, _archive, _team_id = _note_setup(tmp_path, "끝냈습니다.")
    run = setup.teams.get_team_run(setup.run.id)
    task = setup.teams.list_tasks(run.id, setup.cycle.id)[0]

    assert "```next-cycle" not in setup.runtime._worker_prompt(run, setup.worker, task)
```

- [ ] **Step 2: 실패하는지 돌려서 확인한다**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly -k next_cycle`
Expected: FAIL — `assert 'NEXT CYCLE' in ...`

- [ ] **Step 3: 최소 구현을 쓴다**

`team_runtime.py` 에서 `TEAM_NOTE_REWRITE_PROMPT = """` 정의 **앞**에 더한다:

```python
NEXT_CYCLE_PROMPT = """

NEXT CYCLE (optional):
This run continues on its own. Whatever you write here becomes the next cycle's
instruction; nobody retypes it. Write what this cycle's findings say to do next
-- concrete enough that the team can start on it without asking you anything.
Do not restate the goal: the goal is carried separately and the team sees it
either way.

Omit the block when there is nothing left worth a cycle. That is how this run
ends -- an omitted block stops it, and the remaining cycles are not spent.

```next-cycle
{{"instruction":"what the next cycle should do"}}
```
"""

```

`_team_note_rewrite_block` 메서드 바로 뒤에 더한다:

```python
    def _next_cycle_block(
        self,
        run: TeamRun,
        contract: OutputContract | None,
        cycle_id: str | None,
    ) -> str:
        """리드에게 다음 사이클 지시를 요청한다.

        스스로 도는 런에서만 묻는다. 사람이 매번 지시를 주는 런에서는 리드가
        쓴 것이 쓰이지 않으므로, 물어놓고 버리면 기계용 표식만 사람이 읽는
        요약에 남는다.
        """
        if run.execution_policy != "auto":
            return ""
        # 계약은 응답의 마지막 형태를 못박는다. 뒤에 블록을 더 붙이면 깨진다.
        if contract is not None:
            return ""
        # 사이클이 없는 런은 합성 응답에서 블록을 떼어내지 않는다.
        if cycle_id is None:
            return ""
        return "\n\n" + NEXT_CYCLE_PROMPT
```

합성 프롬프트 조립부에서 `+ self._commit_block(cycle_id)` 뒤에 이어 붙인다:

```python
 + self._next_cycle_block(run, contract, cycle_id)
```

- [ ] **Step 4: 통과하는지 돌려서 확인한다**

첫 테스트는 `_note_setup` 이 만드는 런이 `triggered` 정책이라 아직 실패한다. 테스트를 그 사실에 맞춘다 — `_note_setup` 뒤에 정책을 바꾸는 줄을 더한다:

```python
    setup.db.execute(
        "update team_runs set execution_policy = 'auto' where id = ?", (setup.run.id,)
    )
```

이 줄을 `test_the_lead_is_asked_for_the_next_cycle_instruction` 의 `await` 앞에 넣는다.

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly -k next_cycle`
Expected: PASS — 2 passed

- [ ] **Step 5: 커밋한다**

```bash
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "feat: 스스로 도는 런에서 리드에게 다음 사이클 지시를 묻는다

auto 정책에서만 묻는다. 사람이 매번 지시를 주는 런에서는 리드가 쓴 것이
쓰이지 않으므로, 물어놓고 버리면 기계용 표식만 요약에 남는다.

블록을 빼면 런이 끝난다는 것을 프롬프트에 적는다 -- 그것이 유일한 종료
신호이고, 리드가 그 사실을 모르면 습관적으로 계속 쓴다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: 자동 요청이 직전 사이클의 제안을 읽는다

**Files:**
- Modify: `src/personal_agent_gateway/team_cycles.py` (`_auto_instruction`, `enqueue_due_auto_requests`)
- Test: `tests/test_team_cycles.py`

**Interfaces:**
- Consumes: 합성 payload 의 `next_cycle` 칸 (Task 2)
- Produces: `_auto_instruction(connection, team_run_id, previous_cycle_id) -> str | None` — 첫 슬롯은 `previous_cycle_id=None` 으로 부르고 목표를 돌려받는다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_team_cycles.py` 끝에 더한다:

```python
def _applied_synthesis(db, teams, run, cycle, agent, payload):
    """합성 결과 하나를 원장에 적용된 상태로 남긴다."""
    import hashlib

    from personal_agent_gateway.team_model_operations import (
        OperationSpec,
        TeamModelOperationService,
        ValidatedOperationResult,
    )

    from personal_agent_gateway.team_model_effects import TeamModelEffectService

    operations = TeamModelOperationService(db)
    reserved = operations.reserve(
        OperationSpec(
            operation_key=f"{cycle.id}:cycle_synthesis:0",
            team_run_id=run.id,
            cycle_id=cycle.id,
            task_id=None,
            agent_id=agent.id,
            provider=agent.backend,
            stage="cycle_synthesis",
            stage_ordinal=0,
            request_digest=hashlib.sha256(cycle.id.encode()).hexdigest(),
        )
    )
    invoking = operations.begin_attempt(reserved.id, "consumer-1")
    completed = operations.complete(
        invoking.id,
        invoking.version,
        ValidatedOperationResult("synthesis", payload),
    )
    # 적용은 효과 서비스가 한다 -- tests/test_api_team_runs.py 가 같은 방식을
    # 쓴다. operations 에는 적용 메서드가 없다.
    TeamModelEffectService(db, teams, operations).apply_synthesis(
        completed.id, payload["summary"]
    )


def test_the_next_auto_cycle_uses_the_proposal_not_the_goal(tmp_path):
    """지난 사이클이 무엇을 알아냈든 다음 사이클이 처음과 같은 말을 듣는 것이
    지금 동작이고, 그것이 팀을 제자리에 돌린다."""
    db, teams, cycles, run = make_cycle_services(tmp_path, "auto")
    agent = teams.get_agent(run.leader_agent_id)
    cycle = teams.create_cycle(run.id, "auto", "slot-1")
    _applied_synthesis(
        db, teams, run, cycle, agent, {"summary": "끝", "next_cycle": "6문장을 다시 돌려라"}
    )
    teams.set_cycle_status(cycle.id, "completed")

    instruction = cycles._auto_instruction(db.connect(), run.id, cycle.id)

    assert instruction == "6문장을 다시 돌려라"


def test_the_first_slot_has_no_previous_cycle_and_uses_the_goal(tmp_path):
    """시리즈를 만들 때 나가는 요청은 읽을 제안이 아직 없다. 대체 경로가
    아니라 유일한 경로다."""
    db, teams, cycles, run = make_cycle_services(tmp_path, "auto")

    assert cycles._auto_instruction(db.connect(), run.id, None) == "goal"


def test_a_cycle_with_no_proposal_yields_no_instruction(tmp_path):
    """리드가 더 할 일이 없다고 판단한 경우다. 시리즈는 여기서 끝난다."""
    db, teams, cycles, run = make_cycle_services(tmp_path, "auto")
    agent = teams.get_agent(run.leader_agent_id)
    cycle = teams.create_cycle(run.id, "auto", "slot-1")
    _applied_synthesis(db, teams, run, cycle, agent, {"summary": "끝"})
    teams.set_cycle_status(cycle.id, "completed")

    assert cycles._auto_instruction(db.connect(), run.id, cycle.id) is None
```

- [ ] **Step 2: 실패하는지 돌려서 확인한다**

Run: `PYTHONPATH=src python -m pytest tests/test_team_cycles.py -q -p no:randomly -k auto_instruction or proposal or first_slot`
Expected: FAIL — `TypeError: _auto_instruction() takes 2 positional arguments but 3 were given`

- [ ] **Step 3: 최소 구현을 쓴다**

`team_cycles.py` 의 `_auto_instruction` 을 바꾼다:

```python
    @staticmethod
    def _auto_instruction(
        connection: sqlite3.Connection,
        team_run_id: str,
        previous_cycle_id: str | None,
    ) -> str | None:
        """다음 자동 사이클이 받을 지시.

        직전 사이클에서 리드가 낸 제안을 쓴다. 목표를 그대로 반복하던 예전
        동작은 지난 사이클이 무엇을 알아냈든 다음 사이클을 처음으로 되돌렸다.

        첫 슬롯만 예외다 -- 읽을 제안이 아직 없으므로 목표를 쓴다. 그 뒤로
        제안이 없으면 None 을 돌려주고, 부르는 쪽이 시리즈를 끝낸다.
        """
        if previous_cycle_id is None:
            row = connection.execute(
                "select goal from team_runs where id = ?", (team_run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Team run not found: {team_run_id}")
            instruction = str(row["goal"] or "").strip()
            if not instruction:
                raise ValueError("AUTO Team Run requires a base objective")
            return instruction
        row = connection.execute(
            """
            select result_json from team_model_operations
            where cycle_id = ? and stage in ('cycle_synthesis', 'cycle_synthesis_repair')
              and status = 'applied' and result_kind = 'synthesis'
            order by created_at desc limit 1
            """,
            (previous_cycle_id,),
        ).fetchone()
        if row is None or not row["result_json"]:
            return None
        try:
            payload = (json.loads(row["result_json"]) or {}).get("payload") or {}
        except (TypeError, ValueError):
            return None
        instruction = payload.get("next_cycle") if isinstance(payload, dict) else None
        if not isinstance(instruction, str) or not instruction.strip():
            return None
        return instruction.strip()
```

파일 상단에 `import json` 이 없으면 더한다.

`enqueue_due_auto_requests` 의 `for row in due:` 안에서, `request = self._enqueue_request(` 앞에 제안을 먼저 읽고 없으면 시리즈를 끝낸다:

```python
                instruction = self._auto_instruction(
                    connection,
                    series.team_run_id,
                    previous["id"] if previous is not None else None,
                )
                if instruction is None:
                    # 제안이 없으면 목표를 다시 던지지 않는다 -- 그것이 팀을
                    # 제자리에 돌리는 예전 동작이다. 남은 횟수를 쓰지 않고
                    # 끝내되, 왜 끝났는지는 남긴다.
                    connection.execute(
                        """
                        update team_run_auto_series
                        set status = 'auto_completed', next_run_at = null,
                            pause_reason = 'lead_proposed_no_next_cycle',
                            paused_cycle_id = null, completed_at = ?, updated_at = ?
                        where id = ? and status = 'waiting_interval'
                        """,
                        (timestamp, timestamp, series.id),
                    )
                    continue
                request = self._enqueue_request(
                    connection,
                    series.team_run_id,
                    "auto",
                    _auto_source_id(series.id, slot, 1),
                    instruction,
                    ...
                )
```

(`self._auto_instruction(connection, series.team_run_id)` 자리에 `instruction` 을 넣는다.)

`initialize_auto_series` 의 호출도 세 번째 인자를 받도록 고친다:

```python
            self._auto_instruction(connection, team_run_id, None),
```

- [ ] **Step 4: 통과하는지 돌려서 확인한다**

Run: `PYTHONPATH=src python -m pytest tests/test_team_cycles.py -q -p no:randomly`
Expected: PASS — 전부 통과

- [ ] **Step 5: 커밋한다**

```bash
git add src/personal_agent_gateway/team_cycles.py tests/test_team_cycles.py
git commit -m "feat: 자동 사이클이 직전 사이클의 제안을 지시로 받는다

목표를 그대로 반복하던 동작은 지난 사이클이 무엇을 알아냈든 다음 사이클을
처음으로 되돌렸다. 첫 슬롯만 목표를 쓴다 -- 읽을 제안이 아직 없다.

제안이 없으면 목표를 다시 던지지 않고 시리즈를 끝낸다. 사유는 pause_reason
에 남긴다. 상태 어휘를 늘리지 않는다 -- 끝난 것은 끝난 것이다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: 다음 사이클 지시와 종료 사유를 화면에 보여준다

**Files:**
- Modify: `src/personal_agent_gateway/api/team_runs.py` (`_cycle_payload`, 상세 조립부)
- Modify: `frontend/src/components/organisms/TeamRunDetail/index.jsx`
- Modify: `src/personal_agent_gateway/static/styles.css`
- Test: `tests/test_api_team_runs.py`, `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`

**Interfaces:**
- Consumes: 합성 payload 의 `next_cycle` (Task 2), 시리즈의 `pause_reason` (Task 4)
- Produces: 사이클 payload 의 `next_cycle_instruction: str | None`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_api_team_runs.py` 의 `test_detail_reads_the_real_synthesis_not_the_question_it_asked_first` 안, `note` 를 만드는 곳에 `next_cycle` 을 함께 싣는다:

```python
    synthesis = complete_synthesis(
        1,
        "synthesis",
        {
            "summary": "Built it.",
            "coverage_gaps": gaps,
            "team_note": note,
            "next_cycle": "6문장을 다시 돌려 실제 게시 수를 재라",
        },
    )
```

그리고 같은 테스트 끝의 단언 옆에 더한다:

```python
    # 스스로 도는 런은 이 지시로 다음 사이클을 연다. 무엇이 갈지 보이지 않으면
    # 사람이 개입할 자리를 놓친다.
    assert reported["next_cycle_instruction"] == "6문장을 다시 돌려 실제 게시 수를 재라"
```

`test_cycle_space_policy_is_included_in_cycle_detail` 의 사이클 payload 비교에 칸을 더한다:

```python
            "next_cycle_instruction": None,
```

화면 테스트는 `TeamRunDetail.test.jsx` 의 `describe("TeamRunDetail 대시보드와 탭 구조", ...)` 안, `it("탭은 넷이고 처음에는 Run 이 열린다", ...)` 앞에 더한다:

```javascript
  it("다음 사이클에 무엇이 갈지 보여준다", async () => {
    // 승인 단계를 두지 않기로 했으므로, 보이지 않으면 개입할 자리가 없다.
    renderApp({
      detail: {
        cycles: [{
          id: "c1", sequence: 8, status: "completed",
          next_cycle_instruction: "6문장을 다시 돌려라"
        }]
      }
    });
    await userEvent.click(screen.getByRole("tab", { name: /HISTORY/ }));
    const panel = screen.getByRole("tabpanel", { name: "History" });

    expect(within(panel).getByText(/다음 사이클 · 6문장을 다시 돌려라/)).toBeInTheDocument();
  });

  it("리드가 다음 할 일을 내지 않으면 그 사실을 말한다", async () => {
    // 조용히 멈추면 5번 돌 줄 알았던 것이 2번에 끝난 것을 나중에야 안다.
    renderApp({
      detail: {
        activeAutoSeries: {
          id: "s1", status: "auto_completed", settled_slots: 2, target_slots: 5,
          pause_reason: "lead_proposed_no_next_cycle"
        }
      }
    });
    const dashboard = screen.getByRole("region", { name: "Dashboard" });

    expect(within(dashboard).getByText(/다음 할 일이 없어 멈췄습니다/)).toBeInTheDocument();
  });
```

- [ ] **Step 2: 실패하는지 돌려서 확인한다**

Run: `PYTHONPATH=src python -m pytest tests/test_api_team_runs.py -q -p no:randomly -k "synthesis or space_policy"`
Expected: FAIL — `KeyError: 'next_cycle_instruction'`

Run: `cd frontend && npx vitest run src/components/organisms/TeamRunDetail -t "다음 사이클"`
Expected: FAIL — Unable to find text

- [ ] **Step 3: 최소 구현을 쓴다**

`api/team_runs.py` 의 `_cycle_payload` 서명과 반환에 칸을 더한다:

```python
def _cycle_payload(
    cycle: TeamRunCycle,
    coverage_gaps: list[dict[str, str]] | None = None,
    team_note_title: str | None = None,
    next_cycle_instruction: str | None = None,
) -> dict[str, object]:
```

```python
        "team_note_title": team_note_title,
        "next_cycle_instruction": next_cycle_instruction,
```

상세 조립부에서 노트 제목을 꺼내는 곳 옆에 지시도 꺼낸다:

```python
    next_cycle_by_cycle: dict[str, str | None] = {}
```

```python
            note = synthesis_payload.get("team_note")
            note_by_cycle[cycle.id] = (
                note.get("title") if isinstance(note, dict) else None
            )
            instruction = synthesis_payload.get("next_cycle")
            next_cycle_by_cycle[cycle.id] = (
                instruction if isinstance(instruction, str) else None
            )
```

```python
            _cycle_payload(
                cycle,
                coverage_by_cycle.get(cycle.id),
                note_by_cycle.get(cycle.id),
                next_cycle_by_cycle.get(cycle.id),
            )
            for cycle in cycles
```

`TeamRunDetail/index.jsx` 의 사이클 기록에서 팀 노트 줄 바로 뒤에 더한다:

```jsx
                    {cycle.next_cycle_instruction ? (
                      <div className="team-cycle-note mono">
                        {`다음 사이클 · ${cycle.next_cycle_instruction}`}
                      </div>
                    ) : null}
```

같은 파일의 `activeAutoSeries` 를 그리는 곳(`{activeAutoSeries.settled_slots || 0} / {activeAutoSeries.target_slots || 0} SETTLED` 가 있는 블록) 바로 뒤에 더한다:

```jsx
                  {activeAutoSeries.pause_reason === "lead_proposed_no_next_cycle" ? (
                    <span className="mono team-cycle-note">
                      리드가 다음 할 일이 없어 멈췄습니다
                    </span>
                  ) : null}
```

CSS 는 `.team-cycle-note` 를 그대로 쓴다 — 새 규칙을 더하지 않는다.

- [ ] **Step 4: 통과하는지 돌려서 확인한다**

Run: `PYTHONPATH=src python -m pytest tests/test_api_team_runs.py -q -p no:randomly`
Expected: PASS

Run: `cd frontend && npx vitest run`
Expected: PASS — 전부 통과

- [ ] **Step 5: 커밋한다**

```bash
git add src/personal_agent_gateway/api/team_runs.py frontend/src/components/organisms/TeamRunDetail/index.jsx frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx tests/test_api_team_runs.py
git commit -m "feat: 다음 사이클 지시와 종료 사유를 화면에 남긴다

승인 단계를 두지 않기로 했으므로, 무엇이 갈지 보이지 않으면 사람이 개입할
자리가 없다. 멈추는 것도 조용하면 5번 돌 줄 알았던 것이 2번에 끝난 것을
나중에야 안다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: 두 사이클을 끝까지 이어 붙여 확인한다

**Files:**
- Test: `tests/test_team_cycles.py`

**Interfaces:**
- Consumes: Task 1~4 전부
- Produces: 없음

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_team_cycles.py` 끝에 더한다:

```python
def test_a_series_carries_the_proposal_into_the_next_slot(tmp_path):
    """조각을 따로 부르지 않고 시리즈를 이어 붙인다.

    이 기능은 리드 응답에서 다음 요청까지 네 군데를 지난다. 앞서 화면과 서버가
    둘 다 멀쩡한데 중간 배선이 빠져 기능이 조용히 죽은 적이 있다.
    """
    from datetime import UTC, datetime, timedelta

    db, teams, cycles, run = make_cycle_services(tmp_path, "auto")
    agent = teams.get_agent(run.leader_agent_id)
    series = cycles.create_auto_series(run.id)
    first = cycles.claim_next(run.id)
    assert first is not None
    assert first.instruction == "goal"

    cycle = teams.create_cycle(run.id, "auto", first.source_id, request_id=first.id)
    _applied_synthesis(
        db, teams, run, cycle, agent, {"summary": "끝", "next_cycle": "6문장을 다시 돌려라"}
    )
    teams.set_cycle_status(cycle.id, "completed")
    cycles.settle_cycle(cycle.id)

    later = datetime.now(UTC) + timedelta(seconds=series.interval_seconds + 1)
    created = cycles.enqueue_due_auto_requests(now=later)

    assert [item.instruction for item in created] == ["6문장을 다시 돌려라"]


def test_a_series_ends_when_the_lead_proposes_nothing(tmp_path):
    from datetime import UTC, datetime, timedelta

    db, teams, cycles, run = make_cycle_services(tmp_path, "auto")
    agent = teams.get_agent(run.leader_agent_id)
    series = cycles.create_auto_series(run.id)
    first = cycles.claim_next(run.id)
    cycle = teams.create_cycle(run.id, "auto", first.source_id, request_id=first.id)
    _applied_synthesis(db, teams, run, cycle, agent, {"summary": "끝"})
    teams.set_cycle_status(cycle.id, "completed")
    cycles.settle_cycle(cycle.id)

    later = datetime.now(UTC) + timedelta(seconds=series.interval_seconds + 1)

    assert cycles.enqueue_due_auto_requests(now=later) == []
    # 끝난 시리즈는 get_active_series 가 찾지 못한다 -- auto_completed 는
    # 활성 상태가 아니다. 표에서 직접 읽는다.
    row = db.fetchone(
        "select status, pause_reason from team_run_auto_series where id = ?",
        (series.id,),
    )
    assert row["status"] == "auto_completed"
    assert row["pause_reason"] == "lead_proposed_no_next_cycle"
```

- [ ] **Step 2: 실패하는지 돌려서 확인한다**

Run: `PYTHONPATH=src python -m pytest tests/test_team_cycles.py -q -p no:randomly -k series_carries or series_ends`
Expected: FAIL 또는 ERROR — 헬퍼 이름(`settle_cycle`, `get_series`, `get_active_series`)이 이 저장소의 실제 이름과 다르면 여기서 드러난다. 그때는 `grep -n "def settle\|def get_series\|def get_active_series" src/personal_agent_gateway/team_cycles.py` 로 실제 이름을 확인해 테스트를 고친다 — 구현을 고치지 않는다.

- [ ] **Step 3: 구현은 없다**

Task 1~4 가 이미 만들었다. 이 과제는 배선이 실제로 이어지는지만 본다. 실패하면 어느 조각이 끊겼는지 그 자리에서 드러난다.

- [ ] **Step 4: 통과하는지 돌려서 확인한다**

Run: `PYTHONPATH=src python -m pytest tests/test_team_cycles.py -q -p no:randomly`
Expected: PASS

- [ ] **Step 5: 전체를 돌리고 커밋한다**

```bash
ruff check src/ tests/
PYTHONPATH=src python -m pytest -q -p no:randomly
cd frontend && npx vitest run && npx vite build && cd ..
git add tests/test_team_cycles.py
git commit -m "test: 시리즈가 제안을 다음 슬롯으로 실제로 나르는지 끝까지 본다

조각을 따로 부르지 않는다. 이 기능은 리드 응답에서 다음 요청까지 네 군데를
지나고, 앞서 화면과 서버가 둘 다 멀쩡한데 중간 배선이 빠져 기능이 조용히
죽은 적이 있다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## 자기 점검 결과

**설계 대비 빠진 것:** 없다. 설계의 여섯 조각(추출기·payload·프롬프트·`_auto_instruction`·종료·화면)이 Task 1~5 에 있고, Task 6 이 끝까지 잇는다.

**이름 일관성:** `extract_next_cycle`(Task 1) → payload 칸 `next_cycle`(Task 2) → `_auto_instruction(connection, team_run_id, previous_cycle_id)`(Task 4) → payload 칸 `next_cycle_instruction`(Task 5). payload 칸 이름이 서버 안(`next_cycle`)과 화면용(`next_cycle_instruction`)에서 다른 것은 의도다 — 앞은 원장 payload 의 칸이고 뒤는 사이클 payload 의 칸이라 서로 다른 표에 산다.

**이름 확인 결과:** 계획을 쓴 뒤 실제 이름을 확인했고 두 곳을 고쳤다.
`get_series` 는 없다 -- 끝난 시리즈는 `get_active_series` 가 찾지 못하므로
(`auto_completed` 는 활성 상태가 아니다) 표에서 직접 읽는다. 원장에 적용하는
메서드도 `operations` 가 아니라 `TeamModelEffectService.apply_synthesis` 다.
`settle_cycle`(482), `get_active_series`(733), `create_auto_series`(341),
`claim_next`(442) 는 있는 그대로다.
