# Cycle Output Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a Team Run cycle's final synthesized response satisfy the output contract its caller set, instead of being asked for a plain-text summary and then parsed for a contract it was never shown.

**Architecture:** The contract becomes a first-class property of the cycle. The preparer returns `CyclePreparation(instruction, output_contract_id)`; the id is stored beside the effective instruction in the cycle's `semantic_source` metadata. At synthesis the leader gets a contract-shaped prompt instead of the "concise plain-text summary" one, and the server validates the result, re-requesting once through a new `cycle_synthesis_repair` operation stage that mirrors the existing `cycle_planning_repair`.

**Tech Stack:** Python 3.12, FastAPI, SQLite (raw `sqlite3`), pytest / pytest-asyncio, ruff.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-03-team-output-contract-enforcement-design.md`. This plan covers **Part A only**; Part B (typed acceptance checks) is a separate plan.
- Backend interpreter and commands run from the repo root (or the worktree root, if one is in use):
  - Test: `.venv/Scripts/python.exe -m pytest tests/<file> -v`
  - Lint: `.venv/Scripts/python.exe -m ruff check .`
- The repository is NOT clean at baseline: `pytest -q` on main is roughly **32 failed / 1195 passed / 2 skipped**, all failures in `tests/test_runtime_factory_headless.py` and `tests/test_team_cycle_recovery.py`, and some of them flake between runs. `ruff check .` reports **227 pre-existing findings**. Judge your work by the delta against that baseline, never by absolute green. Do not run the whole suite while iterating.
- No database migration. The contract id goes into the existing `team_run_cycles.execution_metadata_json` `semantic_source` object.
- A cycle with no contract must behave exactly as it does today — same prompt, same code path, no validation.
- Korean Conventional Commit subjects, matching the existing history.

---

### Task 1: Output contract registry

**Files:**
- Create: `src/personal_agent_gateway/team_output_contracts.py`
- Test: `tests/test_team_output_contracts.py`

**Interfaces:**
- Consumes: `library_draft_output_contract()` and `parse_library_draft_response()` from `personal_agent_gateway.archive`.
- Produces:
  - `LIBRARY_DRAFT_CONTRACT_ID = "library_draft"`
  - `OutputContract(id: str, instructions: str, validate: Callable[[str], None])` — `validate` raises `ValueError` on violation
  - `get_output_contract(contract_id: str | None) -> OutputContract | None` — returns `None` for `None`, `""`, and unknown ids

- [ ] **Step 1: Write the failing test**

Create `tests/test_team_output_contracts.py`:

```python
import pytest

from personal_agent_gateway.team_output_contracts import (
    LIBRARY_DRAFT_CONTRACT_ID,
    get_output_contract,
)

_VALID = (
    "Draft ready.\n\n"
    '<library_draft>{"kind":"search_method","title":"Search verification method",'
    '"summary":"A repeatable evidence check.","content_markdown":"# Method\\nCheck sources.",'
    '"tags":["research"],"source_urls":[],"persona_ids":[]}</library_draft>'
)


def test_unknown_and_empty_contract_ids_resolve_to_nothing() -> None:
    assert get_output_contract(None) is None
    assert get_output_contract("") is None
    assert get_output_contract("no-such-contract") is None


def test_library_draft_contract_carries_the_marker_instructions() -> None:
    contract = get_output_contract(LIBRARY_DRAFT_CONTRACT_ID)

    assert contract is not None
    assert contract.id == LIBRARY_DRAFT_CONTRACT_ID
    assert "<library_draft>" in contract.instructions


def test_library_draft_contract_validates_the_final_response() -> None:
    contract = get_output_contract(LIBRARY_DRAFT_CONTRACT_ID)
    assert contract is not None

    contract.validate(_VALID)

    with pytest.raises(ValueError):
        contract.validate("## 완료 요약\n\n초안을 파일로 정리했습니다.")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_team_output_contracts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'personal_agent_gateway.team_output_contracts'`

- [ ] **Step 3: Write the implementation**

Create `src/personal_agent_gateway/team_output_contracts.py`:

```python
from collections.abc import Callable
from dataclasses import dataclass

from personal_agent_gateway.archive import (
    library_draft_output_contract,
    parse_library_draft_response,
)

LIBRARY_DRAFT_CONTRACT_ID = "library_draft"


@dataclass(frozen=True)
class OutputContract:
    id: str
    instructions: str
    validate: Callable[[str], None]


def _validate_library_draft(content: str) -> None:
    parse_library_draft_response(content)


_CONTRACTS: dict[str, OutputContract] = {
    LIBRARY_DRAFT_CONTRACT_ID: OutputContract(
        id=LIBRARY_DRAFT_CONTRACT_ID,
        instructions=library_draft_output_contract(),
        validate=_validate_library_draft,
    ),
}


def get_output_contract(contract_id: str | None) -> OutputContract | None:
    if not contract_id:
        return None
    return _CONTRACTS.get(contract_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_team_output_contracts.py -v`
Expected: PASS, 3 tests

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/team_output_contracts.py tests/test_team_output_contracts.py
git commit -m "feat: 사이클 출력 계약 레지스트리 추가"
```

---

### Task 2: Carry the contract id from the preparer to the cycle

**Files:**
- Modify: `src/personal_agent_gateway/teams.py` (`set_cycle_effective_instruction` ~line 581, new `get_cycle_output_contract_id`)
- Modify: `src/personal_agent_gateway/team_cycle_dispatcher.py` (`CyclePreparer` ~line 25, claim path ~line 150-163)
- Modify: `src/personal_agent_gateway/hook_runner.py` (`prepare_team_cycle` ~line 219)
- Test: `tests/test_teams.py`, `tests/test_hook_runner.py`

**Interfaces:**
- Produces:
  - `CyclePreparation(instruction: str, output_contract_id: str | None = None)`, defined in `team_cycle_dispatcher.py` and exported for `hook_runner`
  - `CyclePreparer = Callable[[TeamCycleRequest, TeamRunCycle], Awaitable[CyclePreparation | None]]`
  - `TeamRunService.set_cycle_effective_instruction(cycle_id, instruction, output_contract_id=None)`
  - `TeamRunService.get_cycle_output_contract_id(cycle_id) -> str | None`
- Consumes: nothing from Task 1 yet — the id is an opaque string here.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_teams.py` (use the file's existing service fixture pattern for building a run and a cycle):

```python
def test_cycle_stores_and_returns_the_output_contract_id(tmp_path: Path) -> None:
    teams, cycle = _run_with_cycle(tmp_path)

    teams.set_cycle_effective_instruction(
        cycle.id,
        "Prepare the delegated Knowledge Request as a Library review draft.",
        output_contract_id="library_draft",
    )

    assert teams.get_cycle_output_contract_id(cycle.id) == "library_draft"
    assert teams.get_cycle_effective_instruction(cycle.id) == (
        "Prepare the delegated Knowledge Request as a Library review draft."
    )


def test_cycle_without_a_contract_returns_none(tmp_path: Path) -> None:
    teams, cycle = _run_with_cycle(tmp_path)

    teams.set_cycle_effective_instruction(cycle.id, "Do the work.")

    assert teams.get_cycle_output_contract_id(cycle.id) is None
```

`_run_with_cycle` is a helper you add next to the other helpers in that file: it builds a `TeamRunService` on a fresh `Database`, creates a continuous team run with a leader persona, enqueues a cycle request, and returns `(teams, cycle)`. Copy the run/cycle construction from an existing cycle test in the same file rather than inventing a new shape.

Add to `tests/test_hook_runner.py`:

```python
@pytest.mark.asyncio
async def test_knowledge_request_preparation_carries_the_library_draft_contract(
    tmp_path: Path,
) -> None:
    (
        runner,
        teams,
        team_run,
        archive,
        knowledge_request,
        cycle,
    ) = await _delegated_knowledge_cycle(tmp_path)
    request = teams.get_cycle(cycle.id)
    cycle_request = runner._team_cycles.get_request(request.request_id)

    preparation = await runner.prepare_team_cycle(cycle_request, cycle)

    assert preparation is not None
    assert preparation.output_contract_id == "library_draft"
    assert "<library_draft>" in preparation.instruction
    assert knowledge_request.title in preparation.instruction
```

`_delegated_knowledge_cycle` already exists in that file from earlier work. If reaching the `TeamCycleRequest` through `runner._team_cycles` reads badly, have the helper return it instead — adjust the helper, not the production code.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_teams.py -k output_contract tests/test_hook_runner.py -k preparation -v`
Expected: FAIL — `set_cycle_effective_instruction() got an unexpected keyword argument 'output_contract_id'`, and `prepare_team_cycle` returning a `str`

- [ ] **Step 3: Write the implementation**

3a. In `teams.py`, extend `set_cycle_effective_instruction`:

```python
    def set_cycle_effective_instruction(
        self,
        cycle_id: str,
        instruction: str,
        output_contract_id: str | None = None,
    ) -> TeamRunCycle:
```

and inside, after the existing immutability check, write both keys:

```python
            metadata["semantic_source"] = {
                **semantic_source,
                "effective_instruction": instruction,
                "output_contract_id": output_contract_id,
            }
```

Add the reader next to `get_cycle_effective_instruction`:

```python
    def get_cycle_output_contract_id(self, cycle_id: str) -> str | None:
        row = self._db.fetchone(
            "select execution_metadata_json from team_run_cycles where id = ?",
            (cycle_id,),
        )
        if row is None:
            raise KeyError(f"Team run cycle not found: {cycle_id}")
        metadata = _execution_metadata_object(row["execution_metadata_json"])
        semantic_source = metadata.get("semantic_source", {})
        if not isinstance(semantic_source, dict):
            raise ValueError("Cycle semantic source metadata is invalid")
        contract_id = semantic_source.get("output_contract_id")
        if contract_id is None:
            return None
        if not isinstance(contract_id, str) or not contract_id.strip():
            raise ValueError("Cycle output contract metadata is invalid")
        return contract_id
```

3b. In `team_cycle_dispatcher.py`, replace the `CyclePreparer` alias with the dataclass and the new signature:

```python
@dataclass(frozen=True)
class CyclePreparation:
    instruction: str
    output_contract_id: str | None = None


CyclePreparer = Callable[
    [TeamCycleRequest, TeamRunCycle],
    Awaitable[CyclePreparation | None],
]
```

Add `from dataclasses import dataclass` to the imports.

In the claim path (currently lines 150-163), keep the last preparer's contract id:

```python
            instruction = request.instruction
            output_contract_id: str | None = None
            for preparer in self._preparers:
                replacement = await preparer(request, cycle)
                if replacement is not None:
                    instruction = replacement.instruction
                    output_contract_id = replacement.output_contract_id
            if request.previous_summary_text:
                instruction += (
                    "\n\nPREVIOUS CYCLE SUMMARY\n"
                    + request.previous_summary_text
                )
            self._teams.set_cycle_effective_instruction(
                cycle.id,
                instruction,
                output_contract_id,
            )
```

3c. In `hook_runner.py`, `prepare_team_cycle` returns the new type:

```python
    async def prepare_team_cycle(
        self,
        request: TeamCycleRequest,
        cycle: TeamRunCycle,
    ) -> CyclePreparation | None:
        if request.source_type == "knowledge_request":
            return CyclePreparation(
                instruction=self._prepare_knowledge_request(request, cycle),
                output_contract_id=LIBRARY_DRAFT_CONTRACT_ID,
            )
        if request.source_type != "hook":
            return None
        ...
```

and every remaining `return <str>` in that method becomes `return CyclePreparation(instruction=<str>)`. Import `CyclePreparation` from `team_cycle_dispatcher` and `LIBRARY_DRAFT_CONTRACT_ID` from `team_output_contracts`.

If importing `CyclePreparation` from `team_cycle_dispatcher` into `hook_runner` creates a circular import, move the dataclass into `team_output_contracts.py` and import it from there in both modules. Check the import direction before writing the import, and say in your report which placement you used.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_teams.py tests/test_hook_runner.py tests/test_team_cycle_dispatcher.py -v`
Expected: PASS. `tests/test_team_cycle_dispatcher.py` has existing preparers returning strings — update those test doubles to return `CyclePreparation`; that is part of this task.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/teams.py \
  src/personal_agent_gateway/team_cycle_dispatcher.py \
  src/personal_agent_gateway/hook_runner.py \
  tests/test_teams.py tests/test_hook_runner.py tests/test_team_cycle_dispatcher.py
git commit -m "feat: 사이클 출력 계약 id를 준비 단계에서 보존"
```

---

### Task 3: Synthesis asks for the contract instead of a plain summary

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py` (new `SYNTHESIS_CONTRACT_PROMPT` next to `SYNTHESIS_PROMPT` ~line 127; `_leader_synthesis` ~line 2728)
- Test: `tests/test_team_runtime.py`

**Interfaces:**
- Consumes: `get_output_contract` from Task 1, `get_cycle_output_contract_id` from Task 2.
- Produces: `_cycle_output_contract(cycle_id: str | None) -> OutputContract | None` on `TeamRunRuntime`; the synthesis prompt text for a contract cycle.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_team_runtime.py`. It already has everything needed: `make_operation_runtime_with_completed_worker(tmp_path)` returns a `setup` whose cycle is ready for synthesis, `setup.lead_client` is an `OperationModel` that records every prompt in `.messages` and pops `.responses` per call, and `setup.operations.list_for_cycle` exposes the ledger.

Add these two module-level helpers near the other helpers:

```python
_LIBRARY_DRAFT_SUMMARY = (
    "Draft ready.\n\n"
    '<library_draft>{"kind":"search_method","title":"Search verification method",'
    '"summary":"A repeatable evidence check.","content_markdown":"# Method\\nCheck sources.",'
    '"tags":["research"],"source_urls":[],"persona_ids":[]}</library_draft>'
)


def _set_library_draft_contract(setup) -> None:
    existing = setup.teams.get_cycle_effective_instruction(setup.cycle.id)
    setup.teams.set_cycle_effective_instruction(
        setup.cycle.id,
        existing or "Prepare the delegated Knowledge Request as a Library review draft.",
        output_contract_id="library_draft",
    )
```

Reading the existing instruction first matters: the effective instruction is immutable once written, so the helper must reuse it and only add the contract id.

```python
@pytest.mark.asyncio
async def test_synthesis_prompt_uses_the_output_contract(tmp_path):
    setup = make_operation_runtime_with_completed_worker(tmp_path)
    _set_library_draft_contract(setup)
    setup.lead_client.responses = [ModelResponse(_LIBRARY_DRAFT_SUMMARY)]

    await setup.runtime.resume(setup.run.id, setup.cycle.id)

    prompt = setup.lead_client.messages[-1][0]["content"]
    assert "<library_draft>" in prompt
    assert "concise plain-text summary" not in prompt
    assert "ask_user" in prompt


@pytest.mark.asyncio
async def test_synthesis_prompt_is_unchanged_without_a_contract(tmp_path):
    setup = make_operation_runtime_with_completed_worker(tmp_path)
    setup.lead_client.responses = [ModelResponse("summary")]

    await setup.runtime.resume(setup.run.id, setup.cycle.id)

    prompt = setup.lead_client.messages[-1][0]["content"]
    assert "concise plain-text summary" in prompt
    assert "<library_draft>" not in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_team_runtime.py -k synthesis_prompt -v`
Expected: FAIL — the contract case still contains "concise plain-text summary"

- [ ] **Step 3: Write the implementation**

3a. Add the prompt next to `SYNTHESIS_PROMPT`:

```python
SYNTHESIS_CONTRACT_PROMPT = """You are the leader of a personal-agent-gateway Team Run.
Goal: {goal}
Task results:
{results}

Before finalizing, identify any consequential choice that only the user can make to
produce an accurate final response. First use the goal, frozen rules, prior user
decisions, and task results.
Return either:
1. A short plain-text summary of what was accomplished, including any failures,
   followed by the final response in exactly the form the OUTPUT CONTRACT below
   requires. The contract governs this response, not a file you wrote during the
   run.
2. ONLY {{"resolution":{{"kind":"ask_user","topic":"short topic","question":"one concrete question","why_needed":"why the final response cannot be completed accurately","options":[{{"id":"stable-id","label":"label","impact":"tradeoff"}}],"recommended_option_id":"stable-id or null","blocking_scope":"run"}}}}
At this stage, ask only about final interpretation or presentation that does not
require additional worker execution.

OUTPUT CONTRACT
{contract}"""
```

Two deliberate details in option 1. The sentence about the contract governing the response rather than a file addresses the observed failure, where the leader satisfied the contract by writing files and then summarized in prose. The short summary **before** the contract output is what Task 4 stores as the cycle summary — without it the cycle's summary would be the raw contract payload, which then propagates into the next cycle's prompt as `PREVIOUS CYCLE SUMMARY`, into the Team Run UI, and into `run-result.json`. The Library Draft contract permits text before the marker and forbids it after, so this composes with the contract rather than fighting it.

3b. Add the lookup helper on `TeamRunRuntime`, next to `_goal_context`:

```python
    def _cycle_output_contract(self, cycle_id: str | None) -> OutputContract | None:
        if cycle_id is None:
            return None
        return get_output_contract(
            self._teams.get_cycle_output_contract_id(cycle_id)
        )
```

Import `OutputContract` and `get_output_contract` from `personal_agent_gateway.team_output_contracts`.

3c. In `_leader_synthesis`, replace the `SYNTHESIS_PROMPT.format(...)` term:

```python
        contract = self._cycle_output_contract(cycle_id)
        synthesis_block = (
            SYNTHESIS_CONTRACT_PROMPT.format(
                goal=goal_context,
                results=results,
                contract=contract.instructions,
            )
            if contract is not None
            else SYNTHESIS_PROMPT.format(goal=goal_context, results=results)
        )
```

and use `synthesis_block` where `SYNTHESIS_PROMPT.format(...)` was concatenated.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_team_runtime.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "feat: 계약이 있는 사이클은 합성에서 계약 형식을 요구"
```

---

### Task 4: Validate the synthesized output and re-request once

**Files:**
- Modify: `src/personal_agent_gateway/team_model_operations.py` (`OperationStage` literal ~line 15)
- Modify: `src/personal_agent_gateway/team_runtime.py` (`_leader_synthesis`, the stage sets at ~418, ~568, ~1028, ~1059)
- Modify: `src/personal_agent_gateway/team_provider_recovery.py` (stage handling ~lines 593-660)
- Test: `tests/test_team_runtime.py`

**Interfaces:**
- Consumes: `OutputContract.validate` from Task 1, `_cycle_output_contract` from Task 3.
- Produces:
  - operation stage `"cycle_synthesis_repair"`
  - `OutputContract.human_summary: Callable[[str], str]` (added to the Task 1 dataclass)
  - `_validated_synthesis_result(response, leader, run, contract=None, *, strict=True)`
  - `_synthesis_repair_messages(messages, contract)`
  - the applied synthesis operation's `result_json` carries `contract_payload` (the raw contract-shaped response) whenever a contract was satisfied — Task 5 reads it from there.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_team_runtime.py`, reusing `_LIBRARY_DRAFT_SUMMARY` and `_set_library_draft_contract` from Task 3:

```python
_PROSE_SUMMARY = "## 완료 요약\n\n초안을 파일로 정리했습니다."


@pytest.mark.asyncio
async def test_contract_violation_triggers_exactly_one_repair(tmp_path):
    setup = make_operation_runtime_with_completed_worker(tmp_path)
    _set_library_draft_contract(setup)
    setup.lead_client.responses = [
        ModelResponse(_PROSE_SUMMARY),
        ModelResponse(_LIBRARY_DRAFT_SUMMARY),
    ]

    await setup.runtime.resume(setup.run.id, setup.cycle.id)

    stages = [item.stage for item in setup.operations.list_for_cycle(setup.cycle.id)]
    assert stages.count("cycle_synthesis") == 1
    assert stages.count("cycle_synthesis_repair") == 1
    assert setup.lead_client.calls == 2
    summary = setup.teams.get_cycle(setup.cycle.id).summary or ""
    assert summary.startswith("Draft ready.")
    assert "<library_draft>" not in summary


@pytest.mark.asyncio
async def test_successful_contract_stores_prose_summary_and_ledger_payload(tmp_path):
    setup = make_operation_runtime_with_completed_worker(tmp_path)
    _set_library_draft_contract(setup)
    setup.lead_client.responses = [ModelResponse(_LIBRARY_DRAFT_SUMMARY)]

    await setup.runtime.resume(setup.run.id, setup.cycle.id)

    summary = setup.teams.get_cycle(setup.cycle.id).summary or ""
    assert summary.strip() == "Draft ready."
    assert "<library_draft>" not in summary
    applied = [
        item
        for item in setup.operations.list_for_cycle(setup.cycle.id)
        if item.stage == "cycle_synthesis" and item.status == "applied"
    ]
    assert len(applied) == 1
    assert "<library_draft>" in applied[0].result_json["contract_payload"]


@pytest.mark.asyncio
async def test_second_contract_violation_is_returned_as_is(tmp_path):
    setup = make_operation_runtime_with_completed_worker(tmp_path)
    _set_library_draft_contract(setup)
    setup.lead_client.responses = [
        ModelResponse(_PROSE_SUMMARY),
        ModelResponse(_PROSE_SUMMARY),
    ]

    await setup.runtime.resume(setup.run.id, setup.cycle.id)

    assert setup.lead_client.calls == 2
    cycle = setup.teams.get_cycle(setup.cycle.id)
    assert cycle.status == "completed"
    assert "<library_draft>" not in (cycle.summary or "")


@pytest.mark.asyncio
async def test_ask_user_resolution_is_not_treated_as_a_violation(tmp_path):
    setup = make_operation_runtime_with_completed_worker(tmp_path)
    _set_library_draft_contract(setup)
    setup.lead_client.responses = [
        ModelResponse(
            json.dumps(
                {
                    "resolution": {
                        "kind": "ask_user",
                        "topic": "publication",
                        "question": "Publish as a shared Library entry?",
                        "why_needed": "The audience changes the wording.",
                        "options": [
                            {"id": "shared", "label": "Shared", "impact": "everyone"}
                        ],
                        "recommended_option_id": "shared",
                        "blocking_scope": "run",
                    }
                }
            )
        )
    ]

    await setup.runtime.resume(setup.run.id, setup.cycle.id)

    assert setup.lead_client.calls == 1
    assert setup.teams.get_cycle(setup.cycle.id).status == "waiting_for_user"
```

The `ask_user` payload shape is copied from `test_cycle_synthesis_decision_applies_before_waiting_for_user` in the same file; if that test's shape has drifted, match the current one.

No separate test is added for the new operation stage. `OperationStage` is a typing `Literal` and the `stage` column is plain text with no CHECK constraint, so a test that records the string would assert nothing the repair test above does not already cover end to end.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_team_runtime.py -k "contract or violation" -v`
Expected: FAIL — only one synthesis call happens, so `setup.lead_client.calls == 2` fails and no `cycle_synthesis_repair` operation exists

- [ ] **Step 3: Write the implementation**

3a. Add the stage to the literal in `team_model_operations.py`:

```python
OperationStage = Literal[
    "cycle_planning",
    "cycle_planning_repair",
    "cycle_add_work",
    "worker_execution",
    "mediation_lead",
    "mediation_worker",
    "acceptance_lead",
    "acceptance_worker",
    "cycle_synthesis",
    "cycle_synthesis_repair",
]
```

No result validator entry is needed: `_built_in_result_validators` covers only the plan stages, and synthesis results are validated by the runtime's parser.

3b. In `team_runtime.py`, wherever a stage set or comparison treats `"cycle_synthesis"` as the synthesis stage — the recovery dispatch at ~line 534, the stage sets at ~568, ~1028, and ~1059 — include `"cycle_synthesis_repair"` alongside it, so a repair operation left open by a crash recovers the same way a synthesis operation does. Read each site before editing; treat the repair stage exactly as the synthesis stage, never as a plan stage.

3c. In `team_provider_recovery.py`, do the same: every place that names `"cycle_synthesis"` gains `"cycle_synthesis_repair"` with identical handling. Grep for `cycle_synthesis` in that file and cover each hit.

3d. Teach `_validated_synthesis_result` about the contract. It gains two parameters and, on success, splits the response into the human summary and the contract payload:

```python
    def _validated_synthesis_result(
        self,
        response: ModelResponse,
        leader: TeamAgent,
        run: TeamRun,
        contract: OutputContract | None = None,
        *,
        strict: bool = True,
    ) -> ValidatedOperationResult:
        resolution = _parse_mediation_resolution(response.content)
        if resolution["kind"] == "ask_user":
            return ValidatedOperationResult("user_decision", resolution)
        content = self._finalize_persona_content(
            response.content,
            persona_id=leader.persona_id,
            team_run_id=run.id,
        )
        if contract is None:
            return ValidatedOperationResult("synthesis", {"summary": content})
        try:
            contract.validate(content)
        except ValueError:
            if strict:
                raise
            return ValidatedOperationResult("synthesis", {"summary": content})
        return ValidatedOperationResult(
            "synthesis",
            {
                "summary": contract.human_summary(content),
                "contract_payload": content,
            },
        )
```

`contract.human_summary(content)` is a new field on `OutputContract` added in this task — a `Callable[[str], str]` that returns the part of the response a person should read. For `library_draft` it returns the text before the marker, falling back to the payload's own `summary` field when the leader wrote nothing before it, and to `"(no summary)"` if both are empty. Add it to the dataclass in `team_output_contracts.py` alongside `validate`, with a unit test in `tests/test_team_output_contracts.py` covering all three cases.

Storing the prose rather than the raw response is what keeps the contract payload out of the next cycle's `PREVIOUS CYCLE SUMMARY`, the Team Run UI, and `run-result.json`. Keeping the payload in the same result JSON is what lets Task 5 read it without any new storage.

The `strict=False` variant exists for the repair call only: if the second attempt also violates the contract, the repair must still produce a usable operation. Letting it raise would abort the cycle as failed and discard the leader's summary entirely, after the team has already done all the work.

3e. In `_leader_synthesis`, pass the contract to the parser and wrap the invoke in the repair pattern that `_plan_operation` (lines 1014-1038) already uses. Read that method first and mirror it — this is the same shape, not a new mechanism:

```python
            def synthesis_parser(response):
                return self._validated_synthesis_result(
                    response, leader_agent, run, contract
                )

            ...  # existing recovery, resolved_request_ids, synthesis_ordinal, spec

            try:
                operation = await self._invoke_operation(
                    spec,
                    leader_agent,
                    messages,
                    synthesis_parser,
                )
            except InvalidOperationResult as exc:
                if contract is None:
                    raise
                failed = self._operations.get(exc.operation_id)
                repair_messages = _synthesis_repair_messages(messages, contract)
                repair_spec = _operation_spec(
                    run,
                    cycle_id,
                    leader_agent,
                    "cycle_synthesis_repair",
                    synthesis_ordinal,
                    repair_messages,
                    upstream_session_id=failed.upstream_session_id,
                )
                operation = await self._invoke_operation(
                    repair_spec,
                    leader_agent,
                    repair_messages,
                    lambda response: self._validated_synthesis_result(
                        response, leader_agent, run, contract, strict=False
                    ),
                )
            return self._apply_cycle_synthesis_operation(operation)
```

Three things this relies on, all verified in the existing code:

- when the parser raises, `TeamModelInvoker` marks the operation `failed` with reason `invalid_structured_output` and raises `InvalidOperationResult` (`team_model_invoker.py:135`). The violating operation is closed, not left open, so nothing needs cancelling and crash recovery will not resurrect it.
- reusing `failed.upstream_session_id` keeps the leader's provider session, so it sees its own previous answer.
- apply runs exactly once, on whichever operation succeeded, so `apply_synthesis`'s check that the applied summary equals the recorded one (`team_model_effects.py:738`) holds.

The repair spec reuses the same `synthesis_ordinal`: the operation key is `{cycle}:{task}:{stage}:{ordinal}`, so the differing stage already makes it unique.

3f. Add the repair message builder next to `_planning_repair_messages`:

```python
def _synthesis_repair_messages(
    messages: list[dict[str, object]],
    contract: OutputContract,
) -> list[dict[str, object]]:
    return [
        *messages,
        {
            "role": "user",
            "content": (
                "Your previous response did not satisfy the output contract. "
                "Send the same result again: first a short plain-text summary, "
                "then the contract output in exactly the required form, with "
                "nothing after it.\n\nOUTPUT CONTRACT\n"
                f"{contract.instructions}"
            ),
        },
    ]
```

The instruction repeats "a short plain-text summary first" so the repair does not contradict Task 3's prompt and leave the cycle with an empty summary.

3g. The direct-model path in `_leader_synthesis` — the branch taken when `cycle_id is None` — is unchanged. A run without a cycle has no contract, so `contract` is always `None` there.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_team_runtime.py tests/test_team_model_operations.py tests/test_team_provider_recovery.py tests/test_hook_runner.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py \
  src/personal_agent_gateway/team_model_operations.py \
  src/personal_agent_gateway/team_provider_recovery.py \
  tests/test_team_runtime.py
git commit -m "feat: 계약 위반 시 합성을 1회 재요청"
```

---

### Task 5: Settlement reads the contract payload from the ledger

**Files:**
- Modify: `src/personal_agent_gateway/hook_runner.py` (`_apply_knowledge_request_draft`)
- Test: `tests/test_hook_runner.py`

**Interfaces:**
- Consumes: the applied synthesis operation's `result_json["contract_payload"]` from Task 4.
- Produces: no new public interface. `_apply_knowledge_request_draft` prefers the ledger payload and falls back to parsing `cycle.summary`.

**Why this task exists:** after Task 4, `cycle.summary` no longer contains the marker on the success path — it contains the prose. `_apply_knowledge_request_draft` currently parses `cycle.summary`, so without this change every successful contract would be recorded as `draft_contract_violation`. The fallback is equally load-bearing: cycles that completed **before** this feature still carry the marker in their summary, and startup reconciliation replays them.

- [ ] **Step 1: Write the failing tests**

Two tests, both in `tests/test_hook_runner.py`. The end-to-end "payload reaches the ledger" half is already covered by Task 4's `test_successful_contract_stores_prose_summary_and_ledger_payload`, so these cover the two branches of the lookup rather than re-driving a whole cycle through a second harness.

```python
class _FakeOperations:
    def __init__(self, operations):
        self._operations = operations

    def list_for_cycle(self, cycle_id):
        return list(self._operations)


class _FakeOperation:
    def __init__(self, stage, status, result_json):
        self.stage = stage
        self.status = status
        self.result_json = result_json


@pytest.mark.asyncio
async def test_draft_is_built_from_the_ledger_contract_payload(tmp_path):
    (
        runner,
        teams,
        team_run,
        archive,
        knowledge_request,
        cycle,
    ) = await _delegated_knowledge_cycle(tmp_path)
    runner._operations = _FakeOperations(
        [
            _FakeOperation(
                "cycle_synthesis",
                "applied",
                {"summary": "Draft ready.", "contract_payload": _LIBRARY_DRAFT_RESPONSE},
            )
        ]
    )
    teams.set_cycle_status(cycle.id, "completed", summary="Draft ready.")

    await runner.on_team_run_settled(teams.get_team_run(team_run.id), cycle.id)

    drafts = archive.list_entries(status="draft")
    assert len(drafts) == 1
    assert drafts[0].origin_request_id == knowledge_request.id
    assert archive.get_request(knowledge_request.id).last_draft_error_code is None


@pytest.mark.asyncio
async def test_draft_falls_back_to_parsing_the_cycle_summary(tmp_path):
    (
        runner,
        teams,
        team_run,
        archive,
        knowledge_request,
        cycle,
    ) = await _delegated_knowledge_cycle(tmp_path)
    teams.set_cycle_status(cycle.id, "completed", summary=_LIBRARY_DRAFT_RESPONSE)

    await runner.on_team_run_settled(teams.get_team_run(team_run.id), cycle.id)

    drafts = archive.list_entries(status="draft")
    assert len(drafts) == 1
    assert drafts[0].origin_request_id == knowledge_request.id
```

`_LIBRARY_DRAFT_RESPONSE` is the marker-carrying string already used by `test_successful_draft_clears_an_earlier_failure` in this file; lift it to a module-level constant if it is still inline there. The second test sets the cycle summary to exactly the pre-change shape, so it is the regression guard for cycles that completed before this feature.

Setting `runner._operations` directly in the first test is deliberate: the attribute is optional wiring (see Step 3), and a fake keeps this test about the lookup branch rather than about the ledger's internals.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_hook_runner.py -k "ledger or fallback" -v`
Expected: FAIL — the ledger case records `draft_contract_violation` because the prose summary has no marker

- [ ] **Step 3: Write the implementation**

In `_apply_knowledge_request_draft`, replace the direct parse of `cycle.summary` with a payload lookup that falls back:

```python
            source = self._contract_payload_for_cycle(cycle) or (cycle.summary or "")
            try:
                _result_text, payload = parse_library_draft_response(source)
            except ValueError as exc:
                return self._fail_draft(
                    request_id, cycle, "draft_contract_violation", exc
                )
```

and add the lookup:

```python
    def _contract_payload_for_cycle(self, cycle: TeamRunCycle) -> str | None:
        if self._operations is None:
            return None
        for operation in reversed(self._operations.list_for_cycle(cycle.id)):
            if operation.stage not in {"cycle_synthesis", "cycle_synthesis_repair"}:
                continue
            if operation.status != "applied":
                continue
            payload = (operation.result_json or {}).get("contract_payload")
            return payload if isinstance(payload, str) and payload.strip() else None
        return None
```

`HookRunner` does not hold the operation service today. Add it through the existing `attach_team_cycle_queue` wiring in `app.py` rather than adding a new constructor argument, and keep it optional so the tests that build a `HookRunner` without it still work — that is what the `self._operations is None` guard is for.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_hook_runner.py tests/test_archive.py -v`
Expected: PASS, including the pre-existing knowledge-request settlement tests

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/hook_runner.py src/personal_agent_gateway/app.py \
  tests/test_hook_runner.py
git commit -m "feat: 정산이 원장의 계약 페이로드를 우선 사용"
```

---

### Task 6: Full verification

- [ ] **Step 1: Run the backend suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: no new failures against the baseline in Global Constraints. Report the counts and name any failure outside `tests/test_runtime_factory_headless.py` and `tests/test_team_cycle_recovery.py` — those are yours to fix.

- [ ] **Step 2: Run the feature's files directly**

Run: `.venv/Scripts/python.exe -m pytest tests/test_team_output_contracts.py tests/test_teams.py tests/test_hook_runner.py tests/test_team_runtime.py tests/test_team_cycle_dispatcher.py tests/test_team_model_operations.py tests/test_team_provider_recovery.py -q`
Expected: PASS, no failures. The whole-suite run flakes in the pre-existing areas; this run must be clean.

- [ ] **Step 3: Lint the changed files**

Run: `.venv/Scripts/python.exe -m ruff check src/personal_agent_gateway/team_output_contracts.py src/personal_agent_gateway/team_runtime.py src/personal_agent_gateway/teams.py src/personal_agent_gateway/team_cycle_dispatcher.py src/personal_agent_gateway/hook_runner.py src/personal_agent_gateway/team_model_operations.py src/personal_agent_gateway/team_provider_recovery.py`
Expected: no new findings. Also confirm `ruff check .` still reports 227.

- [ ] **Step 4: Commit any fixes**

Skip if nothing needed fixing.
