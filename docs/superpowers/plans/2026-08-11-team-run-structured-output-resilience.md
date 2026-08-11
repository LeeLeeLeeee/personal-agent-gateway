# Team run structured-output resilience implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Use superpowers:test-driven-development for every behaviour change and superpowers:verification-before-completion before claiming any task done.

**Goal:** Stop a single unparseable model response from killing a Team run, and record how the response was malformed without storing it.

**Architecture:** Repair becomes a property of every model invocation rather than something hand-wired per stage: one `TeamRuntime` helper wraps `_invoke_operation`, and stage names come from an explicit table because `OperationStage` is a closed `Literal`. Worker-stage exhaustion fails that task as today; leader-stage exhaustion reserves the repair operation, leaves it `prepared`, and publishes a decision request so the run pauses and resumes into it. Failure records carry a digest and a non-content shape summary.

**Tech Stack:** Python 3.13, FastAPI, SQLite, pytest / pytest-asyncio.

**Design:** `docs/superpowers/specs/2026-08-11-team-run-structured-output-resilience-design.md`
**Findings this deliberately leaves standing:** `docs/superpowers/specs/2026-08-11-team-run-completeness-findings.md`

## Global Constraints

- Branch first. `main` currently carries the process-lifecycle fixes; do not commit these changes to it directly.
- Backend test baseline is **21 failed / 1437 passed / 4 skipped**. The 21 are pre-existing: 16 in `tests/test_runtime_factory_healdess.py`-style `ProviderExecutionCapabilities` drift (`tests/test_runtime_factory_headless.py`), 4 in `tests/test_api_agents.py`, 1 in `tests/test_api_dashboard.py`. Judge completion by delta, never by absolute green.
- Frontend baseline: 41 files / 389 tests, with up to 2 load-dependent `ArchiveView` timeout flakes that are not caused by this work.
- **Do not store model output.** `raw model response` is excluded by `docs/superpowers/specs/2026-07-31-team-model-operation-ledger-design.md:176-182`, backed by ADR `docs/adr/2026-07-15-audit-retention-and-redaction.md`. Task 5 records a digest and structural facts only, and one of its tests asserts the text is absent.
- `src/personal_agent_gateway/frontend_dist/` is gitignored while a few old bundle files remain tracked; never commit it.
- Run the backend suite blocking — it takes about 7.5 minutes.
- Every new stage name must be added to all sites in Task 2's checklist. Missing the validator registry reproduces `invalid_structured_output` on a valid response, which is the bug being fixed.

---

## Task 1: Declare the repair stage table and enforce its completeness

Start here because every later task reads this table, and because the completeness test is what stops the next person from adding a stage that silently inherits no repair.

**Files:**
- Modify: `src/personal_agent_gateway/team_model_operations.py:15-27` (add three members to `OperationStage`)
- Create: `src/personal_agent_gateway/team_repair_stages.py`
- Create: `tests/test_team_repair_stages.py`

**Interfaces:**
- Produces: `REPAIR_STAGE: dict[OperationStage, OperationStage]` and `repair_stage_for(stage: OperationStage) -> OperationStage`, imported by Tasks 2-4.

- [ ] **Step 1: Write the failing completeness test**

```python
from personal_agent_gateway.team_model_operations import OperationStage
from personal_agent_gateway.team_repair_stages import REPAIR_STAGE, repair_stage_for
from typing import get_args


def test_every_stage_has_a_repair_target() -> None:
    """A stage with no entry inherits no repair, which is exactly how
    acceptance_lead came to have none: repair was opt-in per stage."""
    stages = set(get_args(OperationStage))
    repairs = {stage for stage in stages if stage.endswith("_repair")}
    for stage in stages - repairs:
        assert stage in REPAIR_STAGE, f"{stage} has no repair target"


def test_repair_targets_are_real_stages() -> None:
    stages = set(get_args(OperationStage))
    for base, repair in REPAIR_STAGE.items():
        assert repair in stages, f"{base} maps to unknown stage {repair}"


def test_worker_execution_keeps_its_own_stage() -> None:
    """worker_execution repairs at ordinal 1 of its own stage. Renaming it
    would move it out of the workspace-baseline set in team_runtime.py:414-419
    and change how file changes are attributed."""
    assert repair_stage_for("worker_execution") == "worker_execution"


def test_add_work_repairs_through_the_planning_repair_stage() -> None:
    assert repair_stage_for("cycle_add_work") == "cycle_planning_repair"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=src python -m pytest tests/test_team_repair_stages.py -q -p no:randomly`
Expected: collection error — `No module named 'personal_agent_gateway.team_repair_stages'`.

- [ ] **Step 3: Add the three stage members**

In `team_model_operations.py`, extend `OperationStage`:

```python
OperationStage = Literal[
    "cycle_planning",
    "cycle_planning_repair",
    "cycle_add_work",
    "worker_execution",
    "mediation_lead",
    "mediation_lead_repair",
    "mediation_worker",
    "mediation_worker_repair",
    "acceptance_lead",
    "acceptance_lead_repair",
    "acceptance_worker",
    "acceptance_worker_repair",
    "cycle_synthesis",
    "cycle_synthesis_repair",
]
```

- [ ] **Step 4: Create the table module**

```python
"""Which stage repairs which.

The names are declared rather than derived. OperationStage is a closed Literal,
so f"{stage}_repair" cannot type-check, and a derived name would also hide the
one stage that deliberately repairs in place.
"""

from personal_agent_gateway.team_model_operations import OperationStage

REPAIR_STAGE: dict[OperationStage, OperationStage] = {
    "cycle_planning": "cycle_planning_repair",
    # add-work replans through the same repair stage, at ordinal 2
    # (team_cycle_dispatcher.py:343-347 relies on that pairing).
    "cycle_add_work": "cycle_planning_repair",
    # worker_execution repairs at ordinal 1 of itself. It is the only stage in
    # the workspace-baseline set (team_runtime.py:414-419) that has a repair, and
    # a separate stage name would silently move it to the other baseline policy.
    "worker_execution": "worker_execution",
    "mediation_lead": "mediation_lead_repair",
    "mediation_worker": "mediation_worker_repair",
    "acceptance_lead": "acceptance_lead_repair",
    "acceptance_worker": "acceptance_worker_repair",
    "cycle_synthesis": "cycle_synthesis_repair",
}


def repair_stage_for(stage: OperationStage) -> OperationStage:
    return REPAIR_STAGE[stage]
```

- [ ] **Step 5: Run the test**

Run: `PYTHONPATH=src python -m pytest tests/test_team_repair_stages.py -q -p no:randomly`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/team_model_operations.py \
        src/personal_agent_gateway/team_repair_stages.py \
        tests/test_team_repair_stages.py
git commit -m "feat(operations): declare repair stages and enforce completeness"
```

---

## Task 2: Register the three new stages everywhere a stage name is read

Do this before wiring any behaviour. A stage that exists in the type but is missing from the validator registry fails with `invalid_structured_output` on a perfectly valid response — reproducing the bug this plan fixes, on the repair path, where there is no second chance.

**Files:**
- Modify: `src/personal_agent_gateway/team_model_effects.py` (validator registry near `:3088`, `_validate_worker_operation` at `:1171-1185`)
- Modify: `src/personal_agent_gateway/team_provider_recovery.py:538-544`
- Modify: `tests/test_team_repair_stages.py`

**Interfaces:**
- Consumes: `REPAIR_STAGE` from Task 1.
- Produces: nothing new; later tasks assume every stage in `OperationStage` is registered.

- [ ] **Step 1: Write the failing registration tests**

Append to `tests/test_team_repair_stages.py`:

```python
from personal_agent_gateway.team_model_effects import (
    team_model_effect_result_validators,
)
from personal_agent_gateway.team_provider_recovery import (
    _LEAD_STAGES,
    _WORKER_STAGES,
)


def test_every_repair_stage_validates_the_same_kinds_as_its_base() -> None:
    """A stage missing from this registry makes _result_serialization raise
    OperationResultValidationError, which the invoker converts to
    invalid_structured_output -- on a valid response."""
    validators = team_model_effect_result_validators()
    for base, repair in REPAIR_STAGE.items():
        if base == repair:
            continue
        assert repair in validators, f"{repair} has no result validators"
        assert set(validators[repair]) >= set(validators[base]), (
            f"{repair} accepts fewer result kinds than {base}"
        )


def test_every_stage_is_grouped_for_provider_recovery() -> None:
    """A stage in neither group silently skips the provider-wait source-state
    validation -- no error, weaker invariant."""
    stages = set(get_args(OperationStage))
    cycle_stages = {stage for stage in stages if stage.startswith("cycle_")}
    for stage in stages - cycle_stages:
        assert stage in _WORKER_STAGES or stage in _LEAD_STAGES, (
            f"{stage} belongs to neither worker nor lead group"
        )
```

- [ ] **Step 2: Run and watch it fail**

Run: `PYTHONPATH=src python -m pytest tests/test_team_repair_stages.py -q -p no:randomly`
Expected: FAIL — `mediation_lead_repair has no result validators`.

- [ ] **Step 3: Add validator entries**

In `team_model_effects.py`'s `team_model_effect_result_validators()`, mirror each base stage's entry for its repair. The repair returns the same result kinds because it re-emits the same result:

```python
        "mediation_lead": {
            "mediation_resolution": _valid_mediation_resolution,
        },
        "mediation_lead_repair": {
            "mediation_resolution": _valid_mediation_resolution,
        },
        "mediation_worker": {
            "task_outcome": _valid_task_outcome,
            "worker_query": _valid_worker_query,
        },
        "mediation_worker_repair": {
            "task_outcome": _valid_task_outcome,
            "worker_query": _valid_worker_query,
        },
        "acceptance_lead": {
            "acceptance_review": _valid_acceptance_resolution,
        },
        "acceptance_lead_repair": {
            "acceptance_review": _valid_acceptance_resolution,
        },
```

- [ ] **Step 4: Add `mediation_worker_repair` to the worker-effect allowlist**

`_validate_worker_operation` (`team_model_effects.py:1171-1185`) rejects anything outside its set as "not a Worker execution stage":

```python
            not in {
                "worker_execution",
                "mediation_worker",
                "mediation_worker_repair",
                "acceptance_worker",
                "acceptance_worker_repair",
            }
```

Leave the `allowed_result_kinds` branches below it alone: `mediation_worker_repair` re-emits a `task_outcome`, which the base set already permits.

- [ ] **Step 5: Group the new stages for provider recovery**

`team_provider_recovery.py:538-544`:

```python
_WORKER_STAGES = {
    "worker_execution",
    "mediation_worker",
    "mediation_worker_repair",
    "acceptance_worker",
    "acceptance_worker_repair",
}
_LEAD_STAGES = {
    "mediation_lead",
    "mediation_lead_repair",
    "acceptance_lead",
    "acceptance_lead_repair",
}
```

- [ ] **Step 6: Run the tests**

Run: `PYTHONPATH=src python -m pytest tests/test_team_repair_stages.py tests/test_team_model_effects.py tests/test_team_provider_recovery.py -q -p no:randomly`
Expected: all pass. `test_team_model_effects.py` has 50 tests and `test_team_provider_recovery.py` 20; none should change.

- [ ] **Step 7: Commit**

```bash
git add src/personal_agent_gateway/team_model_effects.py \
        src/personal_agent_gateway/team_provider_recovery.py \
        tests/test_team_repair_stages.py
git commit -m "feat(operations): register repair stages in validators and recovery groups"
```

---

## Task 3: Teach recovery and execution to accept the new stages

Two allowlists guard the resume path, one line apart. Both must accept a repair stage or a paused run can never wake up — and the escalation in Task 6 deliberately leaves a prepared operation for exactly that path to pick up.

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py:568-580` (`mediation_lead` branch), `:619-651` (`acceptance_lead` branch), `:653-658` (worker branch set), `:1254-1266` (`_execute` allowlist)
- Modify: `tests/test_team_runtime.py`

**Interfaces:**
- Consumes: the stage members from Task 1.
- Produces: `_recover_open_operation` handles `acceptance_lead_repair`, `mediation_lead_repair`, `mediation_worker_repair`; `_execute` accepts them as open stages.

- [ ] **Step 1: Write the failing recovery test**

`tests/test_team_runtime.py` already has `make_recoverable_acceptance_runtime(tmp_path)`
(`:814-845`), which returns a `SimpleNamespace` with `run`, `cycle`, `task`,
`leader`, `worker`, `teams`, `operations`, `runtime`, `lead_client`, and
`worker_client`. Use it; do not build a new fixture.

```python
@pytest.mark.asyncio
async def test_prepared_acceptance_lead_repair_is_recoverable(tmp_path):
    """The escalation leaves this operation prepared on purpose. If either
    allowlist rejects the stage, the paused run never resumes."""
    setup = make_recoverable_acceptance_runtime(tmp_path)
    setup.operations.reserve(
        _operation_spec(
            setup.run,
            setup.cycle.id,
            setup.leader,
            "acceptance_lead_repair",
            1,
            [{"role": "user", "content": "re-emit"}],
            task_id=setup.task.id,
        )
    )
    setup.lead_client.responses = [
        ModelResponse(_retry_review("Fix the missing citation check."), []),
    ]

    recovery = await setup.runtime._recover_open_operation(
        setup.run, setup.leader, setup.cycle.id
    )

    assert recovery is not None
    assert recovery.operation.stage == "acceptance_lead_repair"
```

`_operation_spec` and `ModelResponse` are already imported in this test module;
`_retry_review` is its local fixture builder at `:213`.

- [ ] **Step 2: Run and watch it fail**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly -k prepared_acceptance_lead_repair`
Expected: FAIL with `OperationConflict: Open operation stage acceptance_lead_repair is not recoverable here`.

- [ ] **Step 3: Accept the repair stages in the recovery dispatch**

In `team_runtime.py`, widen the two lead branches and the worker set:

```python
        if operation.stage in {"mediation_lead", "mediation_lead_repair"}:
```

```python
        if operation.stage in {"acceptance_lead", "acceptance_lead_repair"}:
```

```python
        if operation.stage in {
            "worker_execution",
            "mediation_worker",
            "mediation_worker_repair",
            "acceptance_worker",
            "acceptance_worker_repair",
        }:
```

Inside each lead branch, when the stage is the repair variant, build the repair prompt instead of the original messages — Task 4 provides `_repair_messages`. Until Task 4 lands, pass the same messages; the test above only checks recoverability.

- [ ] **Step 4: Accept them in `_execute`'s allowlist**

`team_runtime.py:1254-1266`:

```python
                    if open_operation.stage not in {
                        "worker_execution",
                        "mediation_lead",
                        "mediation_lead_repair",
                        "mediation_worker",
                        "mediation_worker_repair",
                        "acceptance_lead",
                        "acceptance_lead_repair",
                        "acceptance_worker",
                        "acceptance_worker_repair",
                    }:
```

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly`
Expected: the new test passes and the existing 138 do not regress.

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "feat(team-runtime): make repair stages recoverable on resume"
```

---

## Task 4: One repair seam every stage goes through

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py` — add `_invoke_with_repair` next to `_invoke_operation` at `:400`, add `_repair_messages` near `_acceptance_worker_repair_messages` at `:3460`
- Modify: `tests/test_team_runtime.py`

**Interfaces:**
- Consumes: `repair_stage_for` from Task 1.
- Produces:

```python
async def _invoke_with_repair(
    self,
    spec: OperationSpec,
    agent: TeamAgent,
    messages: list[dict[str, object]],
    parser: Callable[[ModelResponse], ValidatedOperationResult],
    *,
    repair_messages: list[dict[str, object]] | None = None,
    on_exhausted: Callable[[TeamModelOperation], Awaitable[None]] | None = None,
) -> TeamModelOperation
```

`on_exhausted` receives the failed repair operation. Returning normally means the caller handled it — used by Task 6 to pause. Passing `None` re-raises, which is the worker-stage behaviour.

- [ ] **Step 1: Write the failing seam tests**

This module drives failures by scripting the stub client's responses rather than
by patching internals — an unparseable response is just a `ModelResponse` whose
body is not the expected JSON. Follow that pattern.

```python
@pytest.mark.asyncio
async def test_lead_acceptance_repairs_invalid_structured_output_once(tmp_path):
    """The worker side already recovers this way; the lead side did not."""
    setup = make_recoverable_acceptance_runtime(tmp_path)
    setup.lead_client.responses = [
        ModelResponse("I reviewed it and it looks fine to me.", []),  # unparseable
        ModelResponse(_retry_review("Fix the missing citation check."), []),
        ModelResponse("summary", []),
    ]

    await setup.runtime.resume(setup.run.id, setup.cycle.id)

    failed = setup.operations.get_by_key(
        f"{setup.cycle.id}:{setup.task.id}:acceptance_lead:1"
    )
    repaired = setup.operations.get_by_key(
        f"{setup.cycle.id}:{setup.task.id}:acceptance_lead_repair:1"
    )
    assert failed.status == "failed"
    assert failed.reason_code == "invalid_structured_output"
    assert repaired.status == "applied"


@pytest.mark.asyncio
async def test_worker_stage_still_fails_the_task_when_repair_also_fails(tmp_path):
    """Worker exhaustion stays contained: that one task fails, as today."""
    setup = make_recoverable_acceptance_runtime(tmp_path)
    setup.worker_client.responses = [
        setup.worker_client.responses[0],
        ModelResponse("I finished the work.", []),      # unparseable
        ModelResponse("Still finished, trust me.", []),  # repair also unparseable
    ]
    setup.lead_client.responses = [
        ModelResponse(_retry_review("Fix the missing citation check."), []),
        ModelResponse("summary", []),
    ]

    run = await setup.runtime.resume(setup.run.id, setup.cycle.id)

    task = setup.teams.get_task(setup.task.id)
    assert task.status == "failed"
    assert task.error_message == "invalid_structured_output"
    assert run.status in {"failed", "completed_with_failures"}
```

- [ ] **Step 2: Run and watch it fail**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly -k "repair_retries or repair_also_fails or on_exhausted"`
Expected: FAIL — `'TeamRuntime' object has no attribute '_invoke_with_repair'`.

- [ ] **Step 3: Add the generic repair prompt**

Next to `_acceptance_worker_repair_messages`:

```python
def _repair_messages(reason_code: str | None) -> list[dict[str, object]]:
    """Shape-agnostic on purpose.

    Only the parser knows the expected keys and there is no schema to read them
    from, so naming keys here would be correct for one stage and subtly wrong
    for the rest. A stage that wants to restate its keys passes its own
    repair_messages.
    """
    error = reason_code or "invalid_structured_output"
    return [
        {
            "role": "user",
            "content": (
                "Your previous response could not be parsed.\n"
                f"Error: {error}.\n\n"
                "Do not repeat the work and do not modify files. Re-emit only "
                "the previous final result as one raw JSON object. No "
                "explanations, no Markdown, no code fences."
            ),
        }
    ]
```

- [ ] **Step 4: Add the seam**

```python
    async def _invoke_with_repair(
        self,
        spec: OperationSpec,
        agent: TeamAgent,
        messages: list[dict[str, object]],
        parser: Callable[[ModelResponse], ValidatedOperationResult],
        *,
        repair_messages: list[dict[str, object]] | None = None,
        on_exhausted: Callable[[TeamModelOperation], Awaitable[None]] | None = None,
    ) -> TeamModelOperation:
        try:
            return await self._invoke_operation(spec, agent, messages, parser)
        except InvalidOperationResult as exc:
            failed = self._operations.get(exc.operation_id)

        repair_stage = repair_stage_for(spec.stage)
        repair_ordinal = (
            failed.stage_ordinal + 1
            if repair_stage == spec.stage
            else failed.stage_ordinal
        )
        prompt = repair_messages or _repair_messages(failed.reason_code)
        repair_spec = _operation_spec(
            self._teams.get_team_run(spec.team_run_id),
            spec.cycle_id,
            agent,
            repair_stage,
            repair_ordinal,
            prompt,
            task_id=spec.task_id,
            upstream_session_id=failed.upstream_session_id,
        )
        try:
            return await self._invoke_operation(repair_spec, agent, prompt, parser)
        except InvalidOperationResult as exc:
            if on_exhausted is None:
                raise
            await on_exhausted(self._operations.get(exc.operation_id))
            return self._operations.get(exc.operation_id)
```

`repair_ordinal` bumps only for the in-place convention, because `worker_execution` repairs at ordinal 1 of itself while the others reuse the failed ordinal — the same rule `_synthesis_repair_operation` already follows.

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly`
Expected: the three new tests pass, the existing 138 unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "feat(team-runtime): add one repair seam for every model stage"
```

---

## Task 5: Record how a response was malformed, without storing it

**Files:**
- Modify: `src/personal_agent_gateway/migrations.py` (new `_migration_30_operation_failure_shape`, register in `MIGRATIONS`)
- Modify: `src/personal_agent_gateway/team_model_operations.py:301-318` (`mark_failed`), and `_operation_from_row`
- Modify: `src/personal_agent_gateway/team_model_invoker.py:119-139`, `:154-172`
- Modify: `tests/test_migrations.py`, `tests/test_team_model_operations.py`

**Interfaces:**
- Produces:

```python
def failure_shape(text: str, expected_keys: frozenset[str]) -> dict[str, object]
```

in `team_model_operations.py`, and `mark_failed(..., response_text: str | None = None, expected_keys: frozenset[str] = frozenset())`.

- [ ] **Step 1: Write the failing shape tests**

```python
import json

from personal_agent_gateway.team_model_operations import failure_shape


def test_failure_shape_records_structure_not_content() -> None:
    text = '```json\n{"status": "completed", "surprise": "secret value"}\n```'
    shape = failure_shape(text, frozenset({"status", "summary", "deliverables"}))

    assert shape["length"] == len(text)
    assert shape["fenced"] is True
    assert shape["parsed_json"] is True
    assert sorted(shape["missing_expected_keys"]) == ["deliverables", "summary"]
    # Unexpected key NAMES are model output; only their count is kept.
    assert shape["unexpected_key_count"] == 1
    assert "surprise" not in json.dumps(shape)
    assert "secret value" not in json.dumps(shape)


def test_failure_shape_handles_unparseable_text() -> None:
    shape = failure_shape("I think the answer is probably fine!", frozenset({"status"}))

    assert shape["parsed_json"] is False
    assert shape["fenced"] is False
    assert shape["missing_expected_keys"] == ["status"]
    assert shape["unexpected_key_count"] == 0
```

- [ ] **Step 2: Run and watch it fail**

Run: `PYTHONPATH=src python -m pytest tests/test_team_model_operations.py -q -p no:randomly -k failure_shape`
Expected: FAIL — `ImportError: cannot import name 'failure_shape'`.

- [ ] **Step 3: Implement the classifier**

In `team_model_operations.py`:

```python
def failure_shape(text: str, expected_keys: frozenset[str]) -> dict[str, object]:
    """Non-content facts about a response that failed to parse.

    Deliberately excludes the text and any key the model invented. Expected key
    names come from the contract, so listing the missing ones records nothing
    the model produced; unexpected key names are model output, so only their
    count is kept. The ledger design excludes raw model responses and this stays
    inside that rule.
    """
    stripped = text.strip()
    fenced = stripped.startswith("```")
    body = stripped
    if fenced:
        body = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", stripped)
    parsed: object | None = None
    try:
        parsed = json.loads(body)
    except (TypeError, ValueError):
        parsed = None
    keys = set(parsed) if isinstance(parsed, dict) else set()
    return {
        "length": len(text),
        "fenced": fenced,
        "parsed_json": isinstance(parsed, dict),
        "missing_expected_keys": sorted(expected_keys - keys),
        "unexpected_key_count": len(keys - expected_keys),
    }
```

- [ ] **Step 4: Write the failing migration test**

In `tests/test_migrations.py`, following the shape of the existing column tests:

```python
def test_migration_adds_operation_failure_columns(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.sqlite")
    db.initialize()
    with db.connection() as connection:
        columns = {
            row["name"]
            for row in connection.execute("pragma table_info(team_model_operations)")
        }
    assert {"failure_digest", "failure_shape_json"} <= columns
```

- [ ] **Step 5: Add the migration**

In `migrations.py`, after `_migration_29_team_run_workspace_inheritance`:

```python
def _migration_30_operation_failure_shape(
    connection: sqlite3.Connection,
) -> None:
    existing = _columns(connection, "team_model_operations")
    if "failure_digest" not in existing:
        connection.execute(
            "alter table team_model_operations add column failure_digest text"
        )
    if "failure_shape_json" not in existing:
        connection.execute(
            "alter table team_model_operations add column failure_shape_json text"
        )
```

Register it in `MIGRATIONS` as `(30, "operation_failure_shape", _migration_30_operation_failure_shape)`.

- [ ] **Step 6: Write the failing persistence test**

```python
def test_mark_failed_records_digest_and_shape_but_not_text(tmp_path: Path) -> None:
    service, spec = _prepared_operation(tmp_path)  # existing helper in this file
    operation = service.begin_attempt(spec.operation_key_id, "consumer-1")
    text = '{"status": "completed", "leaked": "do not store me"}'

    failed = service.mark_failed(
        operation.id,
        operation.version,
        "invalid_structured_output",
        response_text=text,
        expected_keys=frozenset({"status", "summary"}),
    )

    assert failed.failure_digest == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert failed.failure_shape["missing_expected_keys"] == ["summary"]
    assert failed.failure_shape["unexpected_key_count"] == 1
    with service._db.connection() as connection:
        row = connection.execute(
            "select * from team_model_operations where id = ?", (failed.id,)
        ).fetchone()
    stored = " ".join(str(value) for value in tuple(row))
    assert "do not store me" not in stored
    assert "leaked" not in stored


def test_mark_failed_without_response_text_stores_nothing(tmp_path: Path) -> None:
    service, spec = _prepared_operation(tmp_path)
    operation = service.begin_attempt(spec.operation_key_id, "consumer-1")

    failed = service.mark_failed(
        operation.id, operation.version, "provider_unavailable"
    )

    assert failed.failure_digest is None
    assert failed.failure_shape is None
```

`_prepared_operation` is the shape this file already uses to reserve an operation and hand back the service; reuse whatever it is called there rather than adding another. `hashlib` is already imported in this module.

- [ ] **Step 7: Extend `mark_failed` and the row mapper**

`mark_failed` gains two keyword arguments and writes both columns inside the same transition. Add `failure_digest: str | None` and `failure_shape: dict[str, object] | None` to `TeamModelOperation` and to `_operation_from_row`, decoding `failure_shape_json` with `json.loads` when present.

- [ ] **Step 8: Pass the response through from the invoker**

At both parse-failure sites in `team_model_invoker.py`, supply the text and the expected keys the parser wanted. The invoker does not know the contract, so add an optional `expected_keys: frozenset[str] = frozenset()` parameter to `invoke()` and have callers pass it; `_invoke_with_repair` forwards it.

- [ ] **Step 9: Run the tests**

Run: `PYTHONPATH=src python -m pytest tests/test_team_model_operations.py tests/test_migrations.py -q -p no:randomly`
Expected: all pass, including the two that assert absence.

- [ ] **Step 10: Commit**

```bash
git add src/personal_agent_gateway/migrations.py \
        src/personal_agent_gateway/team_model_operations.py \
        src/personal_agent_gateway/team_model_invoker.py \
        tests/test_migrations.py tests/test_team_model_operations.py
git commit -m "feat(operations): record how a response failed to parse, not its text"
```

---

## Task 6: Leader exhaustion pauses the run and asks

**Files:**
- Modify: `src/personal_agent_gateway/teams.py` (new public method near `_append_decision_item` at `:2648`)
- Modify: `src/personal_agent_gateway/team_runtime.py` (`_escalate_unparsable_lead_output`, and pass `on_exhausted` at the three leader call sites: `:1983` acceptance, the `mediation_lead` invocation, and `cycle_synthesis`)
- Modify: `tests/test_api_team_runs.py`, `tests/test_team_runtime.py`

**Interfaces:**
- Consumes: `_invoke_with_repair`'s `on_exhausted` from Task 4.
- Produces: `TeamRunService.raise_system_decision(team_run_id, cycle_id, *, topic, question) -> TeamDecisionRequest`, which appends a run-scoped item with **no blocking task** and publishes it.

- [ ] **Step 1: Write the failing escalation test**

```python
@pytest.mark.asyncio
async def test_leader_parse_failure_pauses_the_run_instead_of_failing_it(tmp_path):
    """The prepared repair operation is what resume picks up, and the task must
    keep its status: answer_decision_request resets blocking tasks to pending
    and clears their result, which would discard the worker outcome."""
    setup = make_recoverable_acceptance_runtime(tmp_path)
    task_status_before = setup.teams.get_task(setup.task.id).status

    await setup.runtime._escalate_unparsable_lead_output(
        setup.run, setup.cycle.id, setup.task, "acceptance_lead", setup.leader
    )

    run = setup.teams.get_team_run(setup.run.id)
    assert run.status == "waiting_for_user"
    assert setup.teams.get_task(setup.task.id).status == task_status_before
    request = setup.teams.get_active_decision_request(setup.run.id)
    assert request is not None
    assert all(not item.get("blocking_task_ids") for item in request.items)
    open_operation = setup.operations.get_open_for_cycle(setup.cycle.id)
    assert open_operation.stage == "acceptance_lead_repair"
    assert open_operation.status == "prepared"
```

- [ ] **Step 2: Run and watch it fail**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly -k pauses_the_run_instead`
Expected: FAIL — no attribute `_escalate_unparsable_lead_output`.

- [ ] **Step 3: Add the service method**

In `TeamRunService`, next to the private `_append_decision_item`:

```python
    def raise_system_decision(
        self,
        team_run_id: str,
        cycle_id: str | None,
        *,
        topic: str,
        question: str,
    ) -> TeamDecisionRequest:
        """Pause the run on a question the system asked, not an agent.

        The item carries no blocking task on purpose: answer_decision_request
        resets blocking tasks to pending and clears their result, which would
        throw away the outcome the pause exists to preserve. The pause comes
        from publishing, not from the blocking relationship.
        """
        now = _now()
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            self._append_decision_item(
                connection,
                team_run_id,
                cycle_id,
                {"topic": topic, "question": question, "options": []},
                now,
                blocking_task_id=None,
                stage="task",
            )
        return self.publish_decision_request(team_run_id, cycle_id)
```

- [ ] **Step 4: Add the runtime escalation**

```python
    async def _escalate_unparsable_lead_output(
        self,
        run: TeamRun,
        cycle_id: str,
        task: TeamTask,
        stage: OperationStage,
        leader: TeamAgent,
    ) -> None:
        """Pause rather than fail. A leader stage failing costs the whole run,
        and the work waiting for review is still good."""
        repair_spec = _operation_spec(
            run,
            cycle_id,
            leader,
            repair_stage_for(stage),
            self._acceptance_attempt(task),
            _repair_messages("invalid_structured_output"),
            task_id=task.id,
        )
        self._operations.reserve(repair_spec)
        self._teams.raise_system_decision(
            run.id,
            cycle_id,
            topic=f"{stage} output could not be parsed",
            question=(
                f"The leader's {stage} response failed to parse twice on task "
                f"'{task.title}'. The recorded failure shape is on the operation. "
                "Answer to retry it; use Stop to end the run instead."
            ),
        )
        await self._publish(
            {
                "type": "team.run.input_requested",
                "team_run_id": run.id,
                "reason": "unparsable_lead_output",
                "stage": stage,
            }
        )
```

Use whatever accessor the runtime already has for the acceptance attempt number rather than adding `_acceptance_attempt` if one exists; the value must match the ordinal the failed operation used.

- [ ] **Step 5: Wire `on_exhausted` at the three leader call sites**

Replace `await self._invoke_operation(...)` with `await self._invoke_with_repair(...)` at the `acceptance_lead` site (`:1983`), the `mediation_lead` site, and the `cycle_synthesis` site, passing:

```python
            on_exhausted=lambda failed: self._escalate_unparsable_lead_output(
                run, cycle_id, task, "acceptance_lead", leader_agent
            ),
```

For `cycle_synthesis` there is no task; pass the existing `_synthesis_repair_messages` as `repair_messages` and escalate with a run-scoped question that names no task. Keep the existing `contract is None` branch's behaviour by letting the generic prompt handle that case, and delete the re-raise at `:3036-3037` and `:3081-3082`.

- [ ] **Step 6: Write the failing resume test**

```python
@pytest.mark.asyncio
async def test_answering_resumes_into_the_prepared_repair(tmp_path):
    setup = make_recoverable_acceptance_runtime(tmp_path)
    await setup.runtime._escalate_unparsable_lead_output(
        setup.run, setup.cycle.id, setup.task, "acceptance_lead", setup.leader
    )
    request = setup.teams.get_active_decision_request(setup.run.id)

    setup.teams.answer_decision_request(
        setup.run.id, request.id, request.revision, {request.items[0]["id"]: "retry"}
    )
    recovery = await setup.runtime._recover_open_operation(
        setup.run, setup.leader, setup.cycle.id
    )

    assert recovery is not None
    assert setup.teams.get_task(setup.task.id).status != "pending"
```

- [ ] **Step 7: Run the tests**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py tests/test_api_team_runs.py -q -p no:randomly`
Expected: new tests pass; the existing 138 + 63 do not regress.

- [ ] **Step 8: Commit**

```bash
git add src/personal_agent_gateway/teams.py \
        src/personal_agent_gateway/team_runtime.py \
        tests/test_team_runtime.py tests/test_api_team_runs.py
git commit -m "feat(team-runs): pause and ask when a leader response cannot be parsed"
```

---

## Task 7: Collapse the four existing repair sites onto the seam

Do this last so the seam is already proven. The risk here is silently rewording a prompt, so the tests pin the prompts before the refactor.

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py:1213`, `:2060`, `:2486`, `:3035`/`:3080` (the four `InvalidOperationResult` catch sites), and remove `_synthesis_repair_operation` at `:887` if nothing else calls it
- Modify: `tests/test_team_runtime.py`

**Interfaces:**
- Consumes: `_invoke_with_repair` from Task 4.

- [ ] **Step 1: Write the prompt-pinning tests**

These substrings are the current text, read from
`src/personal_agent_gateway/team_runtime.py`. The point is that the refactor
must not change them.

```python
def test_existing_repair_prompts_are_unchanged() -> None:
    """Collapsing four hand-wired sites onto one seam must not reword them.
    Each of these prompts was tuned against a specific failure; a generic
    replacement would quietly lose that."""
    worker = _worker_repair_messages([{"role": "user", "content": "base"}])
    assert "Return ONLY a JSON array. No prose, no code fences." in worker[0]["content"]

    acceptance = _acceptance_worker_repair_messages("invalid_structured_output")
    assert "Your previous response could not be parsed." in acceptance[0]["content"]
    assert "status, summary, reason_code, deliverables, verifications" in (
        acceptance[0]["content"]
    )

    planning = _planning_repair_messages([{"role": "user", "content": "base"}])
    assert "Return ONLY the required TaskOutcome JSON object or" in planning[0]["content"]
```

If any assertion fails before you refactor anything, the constant was already
edited — stop and reconcile with git history rather than loosening the test.

- [ ] **Step 2: Run it — it should pass before any refactor**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly -k existing_repair_prompts`
Expected: PASS. This is the baseline the refactor must preserve.

- [ ] **Step 3: Route each site through the seam**

Replace each hand-wired try/except with a single `_invoke_with_repair` call passing that site's existing repair prompt as `repair_messages` and `on_exhausted=None` for worker stages. Delete the now-unreachable `_synthesis_repair_operation` and the `contract is None` re-raise.

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly`
Expected: all pass, prompt-pinning included.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "refactor(team-runtime): route existing repairs through the shared seam"
```

---

## Task 8: Surface the failure shape and verify end to end

**Files:**
- Modify: `frontend/src/components/organisms/TeamRunDetail/index.jsx` (task detail diagnostics)
- Modify: `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`
- Modify: `src/personal_agent_gateway/api/team_runs.py` (include `failure_shape` in the task detail payload)

- [ ] **Step 1: Write the failing frontend test**

```jsx
it("shows how a leader response failed to parse", async () => {
  render(<TeamRunDetail detail={{
    run: { id: "r1", goal: "G", status: "waiting_for_user", run_mode: "plan_and_execute" },
    agents: [], messages: [],
    tasks: [{
      id: "t1", title: "Verify guide", status: "in_progress",
      failure_shape: { length: 812, fenced: true, parsed_json: false,
                       missing_expected_keys: ["resolution"], unexpected_key_count: 0 },
    }],
  }} />);

  await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));
  await userEvent.click(screen.getByRole("button", { name: "Open task Verify guide" }));
  const dialog = screen.getByRole("dialog", { name: "Task details: Verify guide" });

  expect(within(dialog).getByText(/812/)).toBeInTheDocument();
  expect(within(dialog).getByText(/resolution/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run and watch it fail**

Run: `npm --prefix frontend test -- TeamRunDetail`
Expected: FAIL — the text is not rendered.

- [ ] **Step 3: Render it in the existing diagnostic area**

Add a block inside the task detail dialog using the existing `team-task-diagnostic` class, showing length, whether it parsed, whether it was fenced, the missing keys, and the unexpected-key count. Render nothing when `failure_shape` is absent.

- [ ] **Step 4: Run the frontend suite**

Run: `npm --prefix frontend test`
Expected: 41 files pass; ignore up to 2 `ArchiveView` timeout flakes.

- [ ] **Step 5: Run the full backend suite, blocking**

Run: `PYTHONPATH=src python -m pytest -q`
Expected: **21 failed / 1442 passed** — the same 21 pre-existing failures, with passes up by the new tests. Any new failure blocks completion.

- [ ] **Step 6: Live verification**

Restart the runtime (`npm run stop` then `npm start`) so the Python changes and the rebuilt bundle load. Then:

- Confirm the migration applied: `select failure_digest, failure_shape_json from team_model_operations limit 1` returns without error.
- Force a leader parse failure by pointing a leader persona at a model that returns prose — or, if that is not arrangeable, drive `_escalate_unparsable_lead_output` against a real run from a REPL and confirm the UI shows "Input needed" with the question, the run is `waiting_for_user`, and answering resumes into the repair operation rather than restarting the task.
- Record what was actually observed. Do not claim the live path works from test output alone: the bug this plan fixes is one that no existing test caught.

- [ ] **Step 7: Commit and finish**

```bash
git add frontend/src/components/organisms/TeamRunDetail/ \
        src/personal_agent_gateway/api/team_runs.py
git commit -m "feat(team-runs): show how a response failed to parse in task details"
```

Then use `superpowers:finishing-a-development-branch`.

---

## Deliberately not in this plan

From `2026-08-11-team-run-completeness-findings.md`: the closed acceptance
contract that triggered the second review (Finding 1), the gate that passes code
never compiled (Finding 2), the plan narrower than its own specification
(Finding 3), and the missing path for a user to contest a plan (Finding 4). Each
needs its own design. This plan stops a run from dying; it does not stop a run
from going the wrong way.
