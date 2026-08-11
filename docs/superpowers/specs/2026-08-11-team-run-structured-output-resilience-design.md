# Team run structured-output resilience

A single unparseable model response must not be able to kill a Team run.

Revision note: an earlier draft of this design was wrong in three places — it
derived stage names dynamically where the type forbids it, it used a transition
that is unreachable at the point it is needed, and it proposed storing data the
ledger design explicitly excludes. Those are corrected below, and the coupling
checklist exists because the first draft counted two of the nine places a stage
name is read.

## Problem

Run `699c1915fa764be598586d2f8bb3a170` failed with `Required task failed`. The
work itself was fine: every required output exists on disk and the backend's 11
tests pass. The `team_model_operations` rows show where it actually died:

| stage | ord 8 (backend) | ord 9 (frontend) |
| --- | --- | --- |
| `worker_execution` → `mediation_*` | produced a `task_outcome` | produced a `task_outcome` |
| `acceptance_lead/1` | review issued | review issued |
| `acceptance_worker/1` | failed `invalid_structured_output` | resubmitted successfully |
| `acceptance_worker_repair/1` | **recovered** | — |
| `acceptance_lead/2` | **failed `invalid_structured_output`** | **failed `invalid_structured_output`** |

Both tasks died at the leader's second acceptance review. The worker side had a
repair stage and used it; the leader side has none, so one unparseable response
was terminal, both tasks were `required`, and the run failed.

`invalid_structured_output` occurred four times in this one run. Two were
repaired, two were fatal. This is routine, not an anomaly.

### Why the repair is missing

`InvalidOperationResult` is caught at four places in `team_runtime.py` — `:1213`
(planning), `:2060` (acceptance worker), `:2486` (worker execution), and
`:3035`/`:3080` (synthesis). Nothing catches it for `acceptance_lead` or
`mediation_lead`, so it propagates and fails the task.

Those four sites also use three different conventions for expressing a repair:
`worker_execution` reuses its own stage at `stage_ordinal == 1`; the other three
create a separate `{stage}_repair` operation; and `cycle_synthesis` repairs only
when a contract is present and re-raises otherwise. Opt-in-per-stage is the
defect — a stage added later inherits nothing, and two already do.

### What this design does not fix

The trigger. The second acceptance round happened because the contract rejected
honestly-declared extra files, and the leader's recovery instruction was
unsatisfiable under that same rule. That is Finding 1 in
`2026-08-11-team-run-completeness-findings.md`, along with two other gaps this
change leaves standing. This design only stops an unparseable response from being
terminal.

## Verified mechanics

Six facts about the existing machinery, each read in code, because an earlier
draft was wrong about three of them.

1. `_OPEN_STATUSES` is `{prepared, invoking, completed, waiting_for_provider,
   ambiguous}` (`team_model_operations.py:45-51`). **`prepared` is open**, so
   `get_open_for_cycle` returns a prepared operation and recovery picks it up on
   resume.
2. `reserve(spec)` (`:140-159`) creates an operation in `prepared` **without
   invoking it**, and returns the existing row when the key already exists.
3. **`prepare_retry` cannot be used here.** It transitions `invoking → prepared`
   only (`:287-299`), and the invoker has already called `mark_failed`, so the
   operation is `failed` by the time the runtime sees the exception. Repair must
   create a new operation, which is what the three existing `_repair` sites do.
4. **`acceptance_lead`'s `stage_ordinal` is the acceptance attempt number**
   (`team_runtime.py:1983-1987` passes `attempt`). So the `worker_execution`
   trick of using ordinal 1 for a repair is unavailable here: it would collide
   with the next acceptance round. A distinct stage name is required.
5. `_recover_open_operation` ends with `raise OperationConflict("Open operation
   stage ... is not recoverable here")` (`:852-854`), and **`_execute` then
   checks the recovered stage against a second allowlist** (`:1260-1269`) and
   raises `"Cycle has an open operation for another stage"`. Both must accept a
   new stage or a paused run can never resume.
6. `_validate_decision_blockers` requires a decision item's blocking task to be
   `waiting_for_user` already (`teams.py:2775-2782`), or `publish_decision_request`
   raises. Escalation must move the task to that status first.

## Design

### 1. One repair seam, with an explicit stage table

Add one helper in `TeamRuntime` that every model stage calls instead of
`_invoke_operation`:

```python
async def _invoke_with_repair(
    self, spec, agent, messages, parser, *, repair_messages=None, on_exhausted,
) -> TeamModelOperation
```

On `InvalidOperationResult` it invokes a repair operation at the same
`stage_ordinal`, carrying the failed operation's `upstream_session_id`. On a
second failure it calls `on_exhausted(failed_operation)`.

**Stage names come from a table, not from string concatenation.**
`OperationStage` is a closed `Literal` (`team_model_operations.py:15-27`), so
`f"{stage}_repair"` cannot type-check. Declare the mapping instead:

```python
REPAIR_STAGE: dict[OperationStage, OperationStage] = {
    "cycle_planning": "cycle_planning_repair",
    "cycle_add_work": "cycle_planning_repair",
    "worker_execution": "worker_execution",        # legacy: ordinal 1
    "mediation_lead": "mediation_lead_repair",
    "mediation_worker": "mediation_worker_repair",
    "acceptance_lead": "acceptance_lead_repair",
    "acceptance_worker": "acceptance_worker_repair",
    "cycle_synthesis": "cycle_synthesis_repair",
}
```

A test asserts every non-repair member of `OperationStage` has an entry, so
adding a stage later fails loudly rather than inheriting nothing. `worker_execution`
keeps its ordinal-1 convention — see "Not changed" below.

The generic repair prompt is **shape-agnostic**: it names the reason code and
asks for the same result re-emitted as one raw JSON object with no prose,
Markdown, or code fences. It deliberately does not try to list the expected keys,
because only the parser knows them and there is no schema to read them from. A
stage that wants to restate its keys passes its own `repair_messages`, as
`_acceptance_worker_repair_messages` does. The four existing sites pass theirs, so
no existing prompt text changes.

`cycle_synthesis`'s contract-conditional re-raise disappears: with no contract it
uses the generic prompt instead of dying.

### 2. Leader exhaustion asks the operator instead of failing the run

`on_exhausted` differs by who owns the stage.

**Worker stages** (`worker_execution`, `mediation_worker`, `acceptance_worker`)
re-raise, failing that one task exactly as today. A worker task failing is a
normal, contained outcome.

**Leader stages** (`acceptance_lead`, `mediation_lead`, `cycle_synthesis`) pause
and ask, because failing them costs the whole run:

1. Consume one acceptance round on the task
   (`acceptance_recovery_attempts + 1`). This replaces an earlier step that
   reserved a `prepared` repair operation for resume to pick up; two attempts at
   that failed. Reusing the failed key returns the failed row, because `reserve`
   is keyed and returns whatever exists. Using the next ordinal collides with the
   repair key of the following acceptance attempt. What actually works is
   advancing the attempt: `_run_cycle_acceptance` computes
   `attempt = task.acceptance_recovery_attempts + 1`, and
   `team_model_effects.py:550` requires the operation ordinal to equal that, so
   resume re-enters cleanly at `acceptance_lead/2`. The parse failure genuinely
   used a round, and `ACCEPTANCE_RECOVERY_CAP` then bounds a model that keeps
   returning garbage instead of pausing forever.
2. Append a decision item **with no blocking task**, naming the stage, the task,
   and the recorded failure classification. `_append_decision_item`
   (`teams.py:2648`) creates the collecting request when none exists but is
   private; this design adds a narrow public method on `TeamRunService` for a
   system-authored item rather than reaching into it.
3. Publish the request, which moves the run to `waiting_for_user`.

**The item must not name a blocking task.** An earlier draft had it block on the
task under acceptance, which required moving that task to `waiting_for_user`
first (fact 6). That would have destroyed the work it was trying to save:
`answer_decision_request` resets every blocking task to `status = 'pending'` and
clears its `result` and `error_message` (`teams.py:3062-3073`). Answering would
have re-run the task from the beginning and discarded the worker outcome that was
waiting to be accepted — the opposite of the intent.

With no blocking task the machinery cooperates: `_decision_blocking_task_ids`
yields an empty set, `_validate_decision_blockers` returns early, the task-reset
branch is skipped, and the task keeps its status. The run still pauses, because
the pause comes from publishing, not from the blocking relationship. Publishing
requires the run to be in `{planning, running, summarizing}`
(`teams.py:45`, `:2912`), which holds at a leader parse failure.

Nothing new is built for the operator-facing half: the decision-request flow
already owns the table, the run status transition, the cycle pause, the "Input
needed" panel with ANSWER & RESUME, and the resume path. Answering runs
`dispatcher.resume` → `runtime.resume` → `_execute_and_synthesize` → `_execute`
→ `_recover_open_operation`, which finds the prepared repair operation and
invokes it. The answer does not need to encode an action; the only thing waiting
is that operation. Cancelling stays available through Stop.

**Rejected: `interventions.py`.** It looks like the natural home, but it is an
in-memory store with no references in `app.py`, `team_runtime.py`, or any API
module. Using it would mean building persistence, an API, and UI for a pause flow
that already exists and works.

### 3. Record what the failure looked like, not what the model said

The first draft proposed storing the response text. That contradicts a standing
decision: the ledger design lists **"raw model response"** under 저장하지 않는 정보
(`2026-07-31-team-model-operation-ledger-design.md:176-182`), backed by ADR
`2026-07-15-audit-retention-and-redaction` ("prompt/output/file 본문을 audit에
저장하지 않는다"). Storing it would need that decision overturned first, which is
a separate conversation and not one this fix should smuggle in.

The diagnostic question does not actually need the content. What an investigator
asks first is "is the model returning the same broken thing every time, or
something different each time, and in what way was it broken" — which a
classification answers:

Add to `team_model_operations`:

- `failure_digest` — sha256 of the response text. Distinguishes a stuck model
  from a flaky one without retaining the text.
- `failure_shape` — a small JSON object of non-content facts: character length,
  whether the payload parsed as JSON at all, whether it was wrapped in a code
  fence, **which of the expected keys were missing**, and **how many unexpected
  keys there were** as a count.

The asymmetry is deliberate. Expected key names come from the contract, so naming
the missing ones records nothing the model produced. Unexpected key names *are*
model output — a model emitting free-form keys would leak content through them —
so only their count is kept. An earlier draft proposed storing the present key
names and would have undermined the very rule this section is written to respect.

`mark_failed` gains an optional parameter and
`TeamModelInvoker` supplies it at both parse-failure sites
(`team_model_invoker.py:119-139`, `:154-172`). The shape surfaces in the task
detail panel's existing diagnostic area.

This answers the question that stopped the 699c1915 investigation while leaving
the audit rule intact. If the shape turns out to be insufficient in practice,
that is the moment to argue for amending the ADR — with a concrete case.

### Not changed

`worker_execution` keeps its ordinal-1 repair convention. Renaming it to a
`_repair` stage is not a rename: the workspace-baseline set
(`team_runtime.py:414-419`) contains `worker_execution` and deliberately excludes
`acceptance_worker_repair`, which reuses the failed operation's baseline instead.
Renaming would silently move it from one baseline policy to the other, changing
how file changes are attributed. The uniformity that rename bought is provided by
the table and its completeness test instead.

## Coupling checklist — every place a stage name is read

Adding `acceptance_lead_repair`, `mediation_lead_repair`, and
`mediation_worker_repair` means touching each of these. The first draft named two
of them.

| # | site | what breaks if missed |
| --- | --- | --- |
| 1 | `OperationStage` Literal, `team_model_operations.py:15-27` | type check only; runtime accepts the name and it fails later at 2 or 6 |
| 2 | effects validator registry, `team_model_effects.py:3088+` | `_result_serialization` raises `OperationResultValidationError` → converted to **`invalid_structured_output`**. Forgetting this reproduces the exact bug being fixed, on a valid response |
| 3 | built-in validators, `team_model_operations.py:556-564` | planning stages only; check when touching those |
| 4 | `_validate_worker_operation` allowlist, `team_model_effects.py:1171-1185` | worker-stage effects rejected as "not a Worker execution stage" |
| 5 | `_PLAN_STAGES` / `_SYNTHESIS_STAGES`, `team_model_effects.py:53-60` | a plan or synthesis repair is not recognised as belonging to its group |
| 6 | `_recover_open_operation` dispatch, `team_runtime.py:470-854` | `"not recoverable here"` — a paused run never resumes |
| 7 | `_execute` open-stage allowlist, `team_runtime.py:1260-1269` | `"Cycle has an open operation for another stage"` — same outcome, one line later |
| 8 | workspace baseline set, `team_runtime.py:414-419` | worker stages only; a lead repair must not be added here |
| 9 | `_WORKER_STAGES` / `_LEAD_STAGES`, `team_provider_recovery.py:538-544` | provider-wait state validation is silently skipped — no error, weaker invariant |
| 10 | `_validate_lead_operation`, `team_model_effects.py:1223-1230` | `operation.stage != stage` rejects the repair **after it has already succeeded** — the run fails with `Operation is not a acceptance_lead stage` |

Site 10 was found during implementation, not during design: the repair operation
completed, its result validated, and the effect application then refused it. Two
earlier drafts of this checklist missed it. A repair re-emits the same result for
the same stage, so its effect is the base stage's effect, and the validator has
to accept either name.

Unaffected but verified:

- **The `next_stage` Literals** (`team_model_effects.py:68-90`, `:1746`, `:2635`).
  No existing `_repair` stage appears in any of them, because a repair is invoked
  as the retry of a failed operation rather than as a forward transition. An
  earlier draft listed these as a coupling point; they are not one for the new
  repair stages.
- `team_cycle_dispatcher._resume_operation` (`:339-350`) classifies add-work by
  stage — `cycle_add_work`, or `cycle_planning_repair` at ordinal 2 — and falls
  through to a plain resume for anything else, which is correct here.
- `hook_runner.py:477` filters synthesis stages only.

**One test should enforce most of this**: for every member of `OperationStage`,
assert it has a validator entry, a repair-table entry or an explicit exemption,
and membership in exactly one of the worker/lead/cycle groupings. That closes 1,
2, 5 and 9 at once and tells the next person what to update.

## Verification

- `_invoke_with_repair` over a fake invoker: parse fails once then the repair
  succeeds; a worker stage fails twice and raises; a leader stage fails twice and
  the run lands `waiting_for_user` with a published decision request, the task
  still `in_progress`, and no blocking task on the item.
- A resume test: after the decision is answered, resume re-enters acceptance at
  the next attempt rather than raising at `_recover_open_operation` or at
  `_execute`'s allowlist. Nothing is reserved ahead of it — see the escalation
  section for why the two reserving designs could not work.
- The `OperationStage` completeness test described above.
- Regression tests pinning the four existing repair prompts byte-for-byte, so
  collapsing the call sites cannot silently reword them.
- A `worker_execution` recovery test proving ordinal-1 still resumes.
- `mark_failed`: digest computed over the full text, `failure_shape` records
  length, JSON-parse outcome, fence flag, and key names; both null when no
  response text is supplied; **and no test fixture asserts response text is
  stored** — the point is that it is not.
- Migration test in `tests/test_migrations.py` for the two new columns.
- Full backend suite against the recorded 21-failure baseline; frontend suite for
  the diagnostic panel change.

## What the live runtime showed

Recorded because the defect this work fixes is one no existing test caught, so
test output alone is not evidence the real path works.

- Migration 30 applied on `data/app.sqlite`: `schema_version` 29 → 30,
  `failure_digest` and `failure_shape_json` present, and all 54 existing
  operations preserved with both columns null. (The database was copied to the
  scratchpad first.)
- `GET /api/team-runs/{id}/detail` on run `699c1915` returns `failure_shape` on
  all 13 tasks. Every value is null, which is the correct reading rather than a
  gap: those failures predate the column, so no shape was ever recorded for
  them. The populated path is covered by
  `test_latest_failure_shapes_reports_the_failure_still_blocking_each_task`.
- The served bundle contains the new panel — the built asset carries
  `RESPONSE DID NOT PARSE`.
- The escalation loop was driven end to end against a real `TeamRuntime` with a
  leader that returns prose twice: the run stopped at `waiting_for_user` with
  the task still `in_progress` and no blocking task, answering left the task
  `in_progress`, and resume re-entered at `acceptance_lead/2` and finished the
  run `completed`. The ledger afterwards read `acceptance_lead/1 failed`,
  `acceptance_lead_repair/1 failed`, `acceptance_lead/2 applied`,
  `acceptance_worker/2 applied`, `cycle_synthesis/0 applied`.
- Not observed live: a leader parse failure produced by a real model. Forcing
  one needs a persona pointed at a model that returns prose, which would mean
  editing the operator's configured team.
