# Acceptance Review That Can Succeed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop a team task from dying because the leader chose a recovery action that could never have worked, and ask the operator instead of failing silently when the attempts run out.

**Architecture:** Three independent changes to the acceptance recovery path. A prompt clause telling the leader which action resolves an `undeclared_deliverable` rejection; a parser-level refusal of a `retry_worker` resolution that names none of the extra paths, which the existing repair seam retries once at no cost to the recovery cap; and a replacement for the task's death at the recovery cap with a decision request naming the paths.

**Tech Stack:** Python 3.13, FastAPI, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-08-12-acceptance-review-that-can-succeed-design.md`

## Global Constraints

- The set-equality rule in `team_acceptance.py` is **not** changed. It is scope control — in run 699c1915 it caught task 8 editing `schemas.py`, which is task 7's contract output. What failed was the handling, not the rule.
- No verification is required when a contract widens. Accepting with `attested_only: true` is a visible outcome the build-evidence view already reports; forcing a check here would conflate scope control with verification depth.
- The live path is `_run_cycle_acceptance`, which invokes the leader through `_invoke_with_repair`. `_review_acceptance` is an older path reached from `_recover_task_outcome` that calls `model.complete` directly with its own single retry. Both get the Part 2 check; only the first has the repair seam.
- Part 3 fires **only** for `undeclared_deliverable` with repeated extras. Reaching the cap on any other rejection must still fail the task exactly as it does today.
- Run tests with `PYTHONPATH=src python -m pytest ... -q -p no:randomly` from the repo root. Run the full suite in the **foreground**; it takes about 10 minutes.
- Lint with `python -m ruff check <files>`; it must print `All checks passed!`.
- Backend baseline is **21 pre-existing failures** in `tests/test_api_agents.py` (4), `tests/test_api_dashboard.py` (1), `tests/test_runtime_factory_headless.py` (16). Judge by delta; any other failure blocks the task.

---

## Task 1: The coherence rule, as a pure function

A pure function first, so the rule is testable without a runtime, a model, or a ledger.

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py`
- Test: `tests/test_team_runtime.py`

**Interfaces:**
- Produces: `undeclared_retry_is_futile(resolution: AcceptanceReviewResolution, extra_paths: frozenset[str]) -> bool` — `True` when the resolution cannot possibly clear the rejection.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_team_runtime.py`.

```python
def test_a_retry_that_names_no_extra_path_is_futile() -> None:
    """retry_worker leaves the contract alone, so the outcome can only be
    accepted if the worker declares fewer files. An instruction that never says
    which files to drop cannot produce that, and run 699c1915 lost two tasks to
    exactly this: the leader said "declare all seven" while the contract listed
    four, and the identical rejection came back until the cap ran out.
    """
    extras = frozenset({"outputs/extra.md", "tests/test_extra.py"})
    resolution = AcceptanceReviewResolution(
        kind="retry_worker",
        reason="The outcome declared files outside the contract.",
        instruction="Declare every file you produced and resubmit.",
    )

    assert undeclared_retry_is_futile(resolution, extras)


def test_a_retry_naming_every_extra_path_is_allowed() -> None:
    """The legitimate cleanup case must keep working -- telling the worker to
    delete what it wrote outside its contract is the reason the set-equality rule
    exists."""
    extras = frozenset({"outputs/extra.md", "tests/test_extra.py"})
    resolution = AcceptanceReviewResolution(
        kind="retry_worker",
        reason="Those files belong to another task.",
        instruction=(
            "Delete outputs/extra.md and tests/test_extra.py, then resubmit "
            "declaring only the contract outputs."
        ),
    )

    assert not undeclared_retry_is_futile(resolution, extras)


def test_naming_only_some_extras_is_still_futile() -> None:
    """A partial instruction leaves at least one undeclared path behind, so the
    same rejection returns."""
    extras = frozenset({"outputs/extra.md", "tests/test_extra.py"})
    resolution = AcceptanceReviewResolution(
        kind="retry_worker",
        reason="r",
        instruction="Delete outputs/extra.md and resubmit.",
    )

    assert undeclared_retry_is_futile(resolution, extras)


@pytest.mark.parametrize("kind", ["revise_acceptance", "ask_user", "fail"])
def test_only_retry_worker_can_be_futile(kind) -> None:
    """revise_acceptance changes the contract, and the other two do not resubmit
    at all, so none of them can be judged by this rule."""
    resolution = AcceptanceReviewResolution(
        kind=kind,
        reason="r",
        instruction="Declare every file you produced and resubmit.",
    )

    assert not undeclared_retry_is_futile(
        resolution, frozenset({"outputs/extra.md"})
    )


def test_no_extras_means_nothing_to_judge() -> None:
    """Called for a rejection that is not about extra paths, or before any are
    known, the rule must stay out of the way."""
    resolution = AcceptanceReviewResolution(
        kind="retry_worker", reason="r", instruction="Fix the failing check."
    )

    assert not undeclared_retry_is_futile(resolution, frozenset())
```

Add `AcceptanceReviewResolution` and `undeclared_retry_is_futile` to the
`personal_agent_gateway.team_runtime` import block at the top of the test file.

- [ ] **Step 2: Run the tests and watch them fail**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly -k "futile or naming_every or only_retry_worker or nothing_to_judge"`
Expected: FAIL — `ImportError: cannot import name 'undeclared_retry_is_futile'`

- [ ] **Step 3: Write the function**

Put it next to `AcceptanceReviewResolution` (around line 331).

```python
def undeclared_retry_is_futile(
    resolution: AcceptanceReviewResolution,
    extra_paths: frozenset[str],
) -> bool:
    """Can this resolution clear an undeclared_deliverable rejection?

    retry_worker leaves the contract untouched, so acceptance requires the next
    outcome to declare fewer files -- which the worker can only do if it is told
    which ones to drop. Naming them is therefore a floor, not a preference.

    This is a proxy, and a deliberately loose one: whether prose actually makes
    the worker drop a file is not decidable here. A leader that writes "declare
    outputs/extra.md along with the others" names the path and still keeps it, and
    this rule lets it through. The cap-time escalation is what covers that.
    """
    if resolution.kind != "retry_worker" or not extra_paths:
        return False
    instruction = resolution.instruction or ""
    return any(path not in instruction for path in extra_paths)
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly -k "futile or naming_every or only_retry_worker or nothing_to_judge"`
Expected: PASS, 7 tests (the parametrized one counts as three).

- [ ] **Step 5: Lint**

Run: `python -m ruff check src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "feat(team-runs): recognise a retry that cannot clear an undeclared rejection"
```

---

## Task 2: Refuse the futile resolution where the seam can retry it

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py` — the `_invoke_with_repair` call in `_run_cycle_acceptance` (around line 2280) and `_review_acceptance` (around line 2557)
- Test: `tests/test_team_runtime.py`

**Interfaces:**
- Consumes: `undeclared_retry_is_futile` from Task 1.
- Produces: nothing new; the leader stage now rejects a futile resolution the way it rejects an unparseable one.

- [ ] **Step 1: Write the failing test**

**`make_recoverable_acceptance_runtime` does NOT produce this rejection.** An
earlier draft of this plan said it did. It sets `required_outputs=()` and its
worker responses declare no deliverables at all, so `declared - expected` is
always empty and the gate can never return `undeclared_deliverable` from it — the
rejection it actually produces is `required_verification_failed`.

Do not mutate it: about nineteen tests depend on that rejection reason. Task 2
added `make_undeclared_deliverable_acceptance_runtime`, which declares a
deliverable against empty `required_outputs` and so produces a real
`undeclared_deliverable` with a known extra path. Use that one here and in Task 4,
and read it before writing a test against it.

```python
@pytest.mark.asyncio
async def test_a_futile_retry_is_sent_back_to_the_leader_once(tmp_path):
    """The refusal has to reach the repair seam, not the recovery cap. A leader
    that picks an action which cannot work made a formatting mistake, and paying
    for it with one of two recovery attempts is how run 699c1915 lost its tasks.
    """
    setup = make_recoverable_acceptance_runtime(tmp_path)
    futile = _retry_review("Declare every file you produced and resubmit.")
    corrected = _retry_review(
        "Delete the files outside the contract and resubmit: wrong-check."
    )
    setup.lead_client.responses = [
        ModelResponse(futile, []),
        ModelResponse(corrected, []),
    ]

    await setup.runtime.resume(setup.run.id, setup.cycle.id)

    assert setup.operations.get_by_key(
        f"{setup.cycle.id}:{setup.task.id}:acceptance_lead:1"
    ).status == "failed"
    assert setup.operations.get_by_key(
        f"{setup.cycle.id}:{setup.task.id}:acceptance_lead_repair:1"
    ).status == "applied"
    # The refusal must not have consumed a recovery attempt.
    assert setup.teams.get_task(setup.task.id).acceptance_recovery_attempts == 1


@pytest.mark.asyncio
async def test_a_retry_naming_the_extras_is_accepted_first_time(tmp_path):
    """Guards the other direction: the cleanup instruction must not be refused,
    or the rule would block the legitimate use of retry_worker."""
    setup = make_recoverable_acceptance_runtime(tmp_path)
    setup.lead_client.responses = [
        ModelResponse(
            _retry_review("Delete wrong-check and resubmit the contract outputs."),
            [],
        ),
        ModelResponse("summary", []),
    ]

    await setup.runtime.resume(setup.run.id, setup.cycle.id)

    assert setup.operations.get_by_key(
        f"{setup.cycle.id}:{setup.task.id}:acceptance_lead:1"
    ).status == "applied"
    assert setup.operations.get_by_key(
        f"{setup.cycle.id}:{setup.task.id}:acceptance_lead_repair:1"
    ) is None
```

`_retry_review` is the existing helper in this file that builds a `retry_worker`
resolution JSON from an instruction — read it and confirm the parameter it takes.
The extras for this fixture come from the worker's declared deliverables minus
`required_outputs`; assert against the real value rather than the placeholder
`wrong-check` if they differ.

- [ ] **Step 2: Run and watch the first test fail**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly -k "futile_retry_is_sent_back"`
Expected: FAIL — no repair operation exists, because the futile resolution is currently accepted.

- [ ] **Step 3: Refuse it in the live path**

In `_run_cycle_acceptance`, the `_invoke_with_repair` call currently passes the
module-level parser `_validated_acceptance_review`. That function sees only the
response, so it cannot know the extras. Replace it with a closure built at the
call site:

```python
        extra_paths = frozenset(
            item.path
            for item in outcome.deliverables
            if item.path not in task.acceptance.required_outputs
        )

        def review_parser(response: ModelResponse) -> ValidatedOperationResult:
            validated = _validated_acceptance_review(response)
            if acceptance.reason_code == "undeclared_deliverable":
                resolution = _parse_acceptance_review_resolution(response.content)
                if undeclared_retry_is_futile(resolution, extra_paths):
                    raise ValueError(
                        "retry_worker cannot clear undeclared_deliverable unless "
                        "the instruction names every path to remove: "
                        + ", ".join(sorted(extra_paths))
                    )
            return validated
```

and pass `review_parser` where `_validated_acceptance_review` was.

Raising `ValueError` is what the other parsers raise for a malformed result;
`_invoke_operation` converts it into the `InvalidOperationResult` the repair seam
catches. Confirm that by reading `_invoke_operation` before assuming it — if it
catches a narrower type, raise that instead.

The repair prompt should name the extras rather than inheriting the generic one.
Pass `repair_messages=_undeclared_retry_repair_messages(messages, extra_paths)` to
the same call, with:

```python
def _undeclared_retry_repair_messages(
    messages: list[dict[str, object]],
    extra_paths: frozenset[str],
) -> list[dict[str, object]]:
    correction = (
        "Your resolution cannot clear this rejection. The outcome declared these "
        "paths, which the contract does not list:\n"
        + "\n".join(f"- {path}" for path in sorted(extra_paths))
        + "\n\nTo keep them, return revise_acceptance with required_outputs "
        "extended to include every one. To have the worker remove them, return "
        "retry_worker with an instruction that names each path to delete. "
        "retry_worker without those paths leaves the contract unchanged and the "
        "same rejection returns."
    )
    return [{"role": "user", "content": f"{messages[0]['content']}\n\n{correction}"}]
```

- [ ] **Step 4: Apply the same rule to the older path**

`_review_acceptance` calls `model.complete` directly and retries once inside its
own `except ValueError`. Apply the same check after
`_parse_acceptance_review_resolution` in both the first attempt and the retry, so
that path behaves equivalently. It needs the extras, so it must compute them from
the `outcome` and `task` arguments it already receives.

Note in a comment that this path has no ledger operation and no repair seam — its
`except ValueError` is the retry.

- [ ] **Step 5: Run both tests, then the whole runtime suite**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly`
Expected: all pass. If an existing acceptance test breaks because its fixture
sends an instruction that no longer satisfies the rule, fix the fixture's
instruction to name the paths — do not weaken the rule.

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "feat(team-runs): send a futile acceptance retry back to the leader"
```

---

## Task 3: Tell the leader which action resolves this rejection

Done after Task 2 so the refusal is already in place; without it the prompt change
alone leaves the failure possible.

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py` — `ACCEPTANCE_REVIEW_PROMPT` (around line 145)
- Test: `tests/test_team_runtime.py`

- [ ] **Step 1: Write the failing test**

```python
def test_the_review_prompt_says_how_to_resolve_an_undeclared_rejection() -> None:
    """The prompt's general default is "prefer Worker correction when the contract
    is valid", which for honest extra work points the leader at the one action
    that cannot succeed. The exception has to be stated where the leader reads it.
    """
    assert "undeclared_deliverable" in ACCEPTANCE_REVIEW_PROMPT
    assert "revise_acceptance" in ACCEPTANCE_REVIEW_PROMPT
    # It must say that a retry has to name the paths, since that is what the
    # parser now enforces.
    lowered = ACCEPTANCE_REVIEW_PROMPT.lower()
    assert "name" in lowered and "remove" in lowered
```

- [ ] **Step 2: Run and watch it fail**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly -k "review_prompt_says"`
Expected: FAIL — `undeclared_deliverable` does not appear in the prompt.

- [ ] **Step 3: Add the clause**

Insert after the existing "Prefer Worker correction…" paragraph, keeping that line
as the general rule:

```
When the failure reason is undeclared_deliverable the outcome declared files the
contract does not list. Keeping them means the contract was too narrow: return
revise_acceptance with required_outputs extended to include every declared path.
Use retry_worker only to have the worker remove them, and name every path to
remove in the instruction -- retry_worker leaves the contract unchanged, so
without those paths the same rejection returns.
```

- [ ] **Step 4: Run the test, then the runtime suite**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly`
Expected: all pass. Prompt-pinning tests elsewhere may assert the prompt's exact
content; if one fails, extend its expected text rather than removing the clause.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "feat(team-runs): tell the leader which action clears an undeclared rejection"
```

---

## Task 4: WITHDRAWN — Ask the operator instead of failing at the cap

**Do not implement this task.** It was attempted and reverted. Reopening an
acceptance round after the operator answers is impossible on the path real runs
take without a schema change: `acceptance_recovery_attempts` is both the recovery
budget and the operation address, so no counter value yields an operation key that
is free *and* an attempt that is in budget. The only working implementation landed
on the cycle-less legacy path, which no run in this deployment can enter, and the
partial version left the live path worse than untouched — a permanent pause
accumulating duplicate questions on a cycle that could never advance.

See the spec's Part 3 section for the full reasoning. The steps below are kept as
the record of what was designed, not as work to do.

## Task 4 (withdrawn): Ask the operator instead of failing at the cap

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py` — `_run_cycle_acceptance`'s cap return (around line 2613) and `_recover_task_outcome`'s equivalent
- Test: `tests/test_team_runtime.py`

**Interfaces:**
- Consumes: `raise_system_decision(team_run_id, cycle_id, *, topic, question)` on `TeamRunService`, which publishes a decision request carrying no blocking task and leaves the run `waiting_for_user`.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_the_cap_asks_the_operator_when_the_same_extras_keep_returning(
    tmp_path,
):
    """This is where run 699c1915's tasks 8 and 9 died, leaving the run with
    nothing but "Required task failed". The extras are known, so the operator can
    be asked whether to widen the contract or drop them."""
    setup = make_undeclared_deliverable_acceptance_runtime(tmp_path)
    # Two reviews that pass the coherence rule and still fail to clear the
    # rejection: each names the extras but the worker re-declares them anyway.
    naming = _retry_review("Delete wrong-check and resubmit.")
    setup.lead_client.responses = [ModelResponse(naming, []), ModelResponse(naming, [])]
    setup.worker_client.responses = [
        ModelResponse(_outcome_json("draft", verification="wrong-check"), []),
        ModelResponse(_outcome_json("draft", verification="wrong-check"), []),
        ModelResponse(_outcome_json("draft", verification="wrong-check"), []),
    ]

    run = await setup.runtime.resume(setup.run.id, setup.cycle.id)

    assert run.status == "waiting_for_user"
    assert setup.teams.get_task(setup.task.id).status == "in_progress"
    request = setup.teams.get_active_decision_request(setup.run.id)
    assert request is not None
    assert "wrong-check" in request.items[0]["question"]
    assert all(not item["blocking_task_ids"] for item in request.items)


@pytest.mark.asyncio
async def test_the_cap_still_fails_the_task_for_other_rejections(tmp_path):
    """The change must not swallow every acceptance failure into a question."""
    setup = make_recoverable_acceptance_runtime(tmp_path)
    fail_review = json.dumps(
        {
            "resolution": {
                "kind": "fail",
                "reason": "The verification cannot be satisfied.",
                "reason_code": "verification_unfixable",
                "summary": "recovery cannot continue",
            }
        }
    )
    setup.lead_client.responses = [
        ModelResponse(fail_review, []),
        ModelResponse("summary", []),
    ]

    run = await setup.runtime.resume(setup.run.id, setup.cycle.id)

    assert run.status != "waiting_for_user"
    assert setup.teams.get_task(setup.task.id).status == "failed"
```

There is no `_fail_review` helper in this file — only `_retry_review(instruction)`
and `_ask_user_resolution(question)` — so the `fail` resolution is built inline
above. Check the exact field set `_valid_acceptance_resolution` accepts for `fail`
before trusting the JSON as written; it pins the key set exactly.

The worker-response count in the first test has to match how many attempts the
fixture actually makes — run it and adjust from the failure rather than guessing.

- [ ] **Step 2: Run and watch the first test fail**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly -k "cap_asks_the_operator"`
Expected: FAIL — the run ends `failed`, not `waiting_for_user`.

- [ ] **Step 3: Replace the cap's return for this rejection**

The cap check currently reads:

```python
            if task.acceptance_recovery_attempts >= ACCEPTANCE_RECOVERY_CAP:
                return task, outcome, acceptance
```

Before returning, escalate when the rejection is about extras that have already
been seen:

```python
            if task.acceptance_recovery_attempts >= ACCEPTANCE_RECOVERY_CAP:
                if acceptance.reason_code == "undeclared_deliverable" and rejected_paths:
                    # Failing here is how run 699c1915 lost tasks 8 and 9 -- the
                    # attempts were spent, the extras were known, and nobody was
                    # asked. The paths are the whole question.
                    self._teams.raise_system_decision(
                        run.id,
                        task.cycle_id,
                        topic="Undeclared deliverables on "
                        f"'{task.title}' could not be resolved",
                        question=(
                            "The worker declared these files, which the task "
                            "contract does not list:\n"
                            + "\n".join(f"- {path}" for path in sorted(rejected_paths))
                            + "\n\nShould the contract be widened to include them, "
                            "or should they be removed?"
                        ),
                    )
                    raise AcceptanceRecoveryExhausted(task.id)
                return task, outcome, acceptance
```

`rejected_paths` is already in scope in `_recover_task_outcome`; in
`_run_cycle_acceptance` compute it the same way that method does, from
`_persisted_undeclared_paths` plus the current outcome's extras.

Define the exception beside the other runtime exceptions:

```python
class AcceptanceRecoveryExhausted(RuntimeError):
    """Recovery ran out of attempts on a rejection the operator can settle.

    Raised rather than returned because every caller of the acceptance path
    treats a returned rejection as "fail the task", which is the behaviour being
    replaced. It is caught where UnparsableLeadOutput is caught, so the run stays
    waiting_for_user with the task intact.
    """
```

Catch it exactly where `UnparsableLeadOutput` is caught — in `start()`, in
`resume()`, and in the task loop's re-raise tuple. Grep for
`UnparsableLeadOutput` and add this alongside every occurrence; missing one of
those three sites is how the equivalent pause was half-wired before.

- [ ] **Step 4: Run both tests, then the runtime and teams suites**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py tests/test_teams.py -q -p no:randomly`
Expected: all pass.

- [ ] **Step 5: Verify the task survives and the answer leads somewhere**

Add a test asserting that answering the request leaves the task `in_progress` with
its `outcome` intact — the reason `raise_system_decision` is used rather than a
blocking decision item is that answering a blocking one resets the task to
`pending` and clears its result.

```python
@pytest.mark.asyncio
async def test_answering_the_cap_question_does_not_discard_the_outcome(tmp_path):
    setup = make_recoverable_acceptance_runtime(tmp_path)
    naming = _retry_review("Delete wrong-check and resubmit.")
    setup.lead_client.responses = [ModelResponse(naming, []), ModelResponse(naming, [])]
    setup.worker_client.responses = [
        ModelResponse(_outcome_json("draft", verification="wrong-check"), []),
        ModelResponse(_outcome_json("draft", verification="wrong-check"), []),
        ModelResponse(_outcome_json("draft", verification="wrong-check"), []),
    ]
    await setup.runtime.resume(setup.run.id, setup.cycle.id)
    request = setup.teams.get_active_decision_request(setup.run.id)

    setup.teams.answer_decision_request(
        setup.run.id,
        request.id,
        request.revision,
        {request.items[0]["id"]: "widen the contract"},
    )

    task = setup.teams.get_task(setup.task.id)
    assert task.status == "in_progress"
    assert task.outcome is not None
```

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly -k "answering_the_cap_question"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "feat(team-runs): ask the operator when acceptance recovery runs out"
```

---

## Task 5: Verify the whole path and finish

Task 4 is withdrawn, so this task verifies Tasks 1-3 only.

- [ ] **Step 1: Full backend suite, foreground**

Run: `PYTHONPATH=src python -m pytest -q -p no:randomly`
Expected: **21 failed**, in `tests/test_api_agents.py` (4), `tests/test_api_dashboard.py` (1), `tests/test_runtime_factory_headless.py` (16), with passes up by this plan's new tests. Report the failure list, not just the count.

- [ ] **Step 2: Lint**

Run: `python -m ruff check src/personal_agent_gateway/ tests/`
Expected: `All checks passed!`

- [ ] **Step 3: Prove the premise end to end, not just at the gate**

The spec's premise — widening the contract makes the same declared set acceptable
— was verified against `TeamAcceptanceService` alone. Add a runtime test that
drives it through the recovery path: a leader that returns `revise_acceptance`
with the extras included, and a worker that re-declares them, ends with the task
`completed`.

There is no `_revise_review` helper — build the resolution inline, matching the
shape `ACCEPTANCE_REVIEW_PROMPT` documents and `_valid_acceptance_resolution`
accepts:

```python
@pytest.mark.asyncio
async def test_widening_the_contract_lets_the_same_outcome_through(tmp_path):
    """The premise the whole design rests on, exercised through the runtime
    rather than against the gate in isolation."""
    setup = make_recoverable_acceptance_runtime(tmp_path)
    task = setup.teams.get_task(setup.task.id)
    widened = json.dumps(
        {
            "resolution": {
                "kind": "revise_acceptance",
                "reason": "The contract was narrower than the work.",
                "instruction": "Resubmit declaring the same files.",
                "acceptance": {
                    "required_outputs": list(task.acceptance.required_outputs),
                    "required_verifications": [
                        {"name": "wrong-check", "check": None}
                    ],
                },
            }
        }
    )
    setup.lead_client.responses = [
        ModelResponse(widened, []),
        ModelResponse("summary", []),
    ]

    await setup.runtime.resume(setup.run.id, setup.cycle.id)

    settled = setup.teams.get_task(setup.task.id)
    assert settled.status == "completed"
    assert "wrong-check" in [
        item.name for item in settled.acceptance.required_verifications
    ]
```

The fixture's worker declares a verification named `wrong-check` that the original
contract does not require, which is what makes its outcome rejectable; widening the
contract to require that verification is the revision under test. Read
`make_recoverable_acceptance_runtime` and adjust the names if the fixture differs —
the assertion that matters is that the task reaches `completed` through the
revision, not the specific string.

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly -k "widening_the_contract"`
Expected: PASS.

- [ ] **Step 4: Live check**

Restart the runtime so the Python changes load:

```bash
npm run stop && npm start
```

Then confirm what can be confirmed without running a whole team:

- The prompt clause is in the served code: `PYTHONPATH=src python -c "from personal_agent_gateway.team_runtime import ACCEPTANCE_REVIEW_PROMPT as p; print('undeclared_deliverable' in p)"`
- Run 699c1915's tasks 8 and 9 still show their promised-versus-declared mismatch in `/detail`'s `build_evidence` (4 promised / 7 declared and 3 / 7). This change does not alter stored history, so those numbers must be unchanged — if they moved, something wrote to that run.

Say explicitly what you could not arrange. A real leader producing a futile
resolution needs a live team run and is not worth forcing.

- [ ] **Step 5: Record and finish**

Append what you observed to the spec's Verification section, commit that file
alone, then use `superpowers:finishing-a-development-branch`.

```bash
git add docs/superpowers/specs/2026-08-12-acceptance-review-that-can-succeed-design.md
git commit -m "docs(team-runs): record what the acceptance recovery fix showed"
```

---

## Deliberately not in this plan

- **Changing the set-equality rule.** It is scope control and it caught real cross-task interference in the run that motivated this work.
- **Requiring a verification when a contract widens.** Accepting with `attested_only: true` is visible in the build-evidence view; forcing a check belongs with Finding 2, which is about giving the environment a way to say what it can actually verify.
- **Making the leader's judgement correct.** If it widens a contract to admit something that should never have been written, nothing here notices. This plan removes deaths caused by form and raises a spent loop to the operator.
