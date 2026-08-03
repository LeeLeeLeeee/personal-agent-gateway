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
1. The final response in exactly the form the OUTPUT CONTRACT below requires. The
   contract governs this response, not a file you wrote during the run.
2. ONLY {{"resolution":{{"kind":"ask_user","topic":"short topic","question":"one concrete question","why_needed":"why the final response cannot be completed accurately","options":[{{"id":"stable-id","label":"label","impact":"tradeoff"}}],"recommended_option_id":"stable-id or null","blocking_scope":"run"}}}}
At this stage, ask only about final interpretation or presentation that does not
require additional worker execution.

OUTPUT CONTRACT
{contract}"""
```

The sentence about the contract governing the response rather than a file is deliberate: on the observed failure the leader satisfied the contract by writing files and summarized in prose.

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
- Produces: operation stage `"cycle_synthesis_repair"`; `_repair_synthesis_for_contract(run, leader_agent, cycle_id, messages, first_result, contract) -> str | UserDecisionResolution`.

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
    assert "<library_draft>" in summary


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

3d. In `_leader_synthesis`, validate before returning. Both the operation path (`return self._apply_cycle_synthesis_operation(operation)`) and the direct-model path (`return content`) funnel through one check:

```python
        result = ...  # the existing return value at each of the two exit points
        if contract is None or isinstance(result, UserDecisionResolution):
            return result
        try:
            contract.validate(result)
        except ValueError as exc:
            return await self._repair_synthesis_for_contract(
                run,
                leader_agent,
                cycle_id,
                messages,
                result,
                contract,
                redact_text(exc) or "output contract violation",
            )
        return result
```

3e. Add the repair method next to `_leader_synthesis`:

```python
    async def _repair_synthesis_for_contract(
        self,
        run: TeamRun,
        leader_agent: TeamAgent,
        cycle_id: str | None,
        messages: list[dict[str, object]],
        first_result: str,
        contract: OutputContract,
        violation: str,
    ) -> str | UserDecisionResolution:
        repair_messages = [
            *messages,
            {"role": "assistant", "content": first_result},
            {
                "role": "user",
                "content": (
                    "Your response did not satisfy the output contract: "
                    f"{violation}\n"
                    "Send the same result again in exactly the contract's form. "
                    "Do not explain, apologize, or add anything outside it.\n\n"
                    "OUTPUT CONTRACT\n"
                    f"{contract.instructions}"
                ),
            },
        ]
        if cycle_id is None:
            model = self._model(leader_agent, cycle_id)
            response = await model.complete(repair_messages)
            if response.upstream_session_id:
                self._teams.set_agent_session(
                    leader_agent.id, response.upstream_session_id
                )
            return self._finalize_persona_content(
                response.content,
                persona_id=leader_agent.persona_id,
                team_run_id=run.id,
            )

        def synthesis_parser(response):
            return self._validated_synthesis_result(response, leader_agent, run)

        spec = _operation_spec(
            run,
            cycle_id,
            leader_agent,
            "cycle_synthesis_repair",
            0,
            repair_messages,
        )
        operation = await self._invoke_operation(
            spec,
            leader_agent,
            repair_messages,
            synthesis_parser,
        )
        return self._apply_cycle_synthesis_operation(operation)
```

Ordinal `0` is correct: a repair happens at most once per synthesis attempt, and the stage itself distinguishes it from the synthesis operation. The returned result is **not** re-validated — one repair, then whatever comes back is the cycle's summary.

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

### Task 5: Full verification

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
