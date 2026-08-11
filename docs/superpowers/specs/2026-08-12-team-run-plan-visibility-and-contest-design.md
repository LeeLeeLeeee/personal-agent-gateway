# Showing what a team run built, and letting the user contest it

Run `699c1915` finished with about two thirds of a product and reported a
task-level failure. Nothing in the run said the product was incomplete. This
design addresses why the operator could not see that, and why — once they did
see it — they had no way to say so.

It grew out of `2026-08-11-team-run-completeness-findings.md` (Finding 4, and the
user-facing half of Finding 3). The findings document treated them as separate
work. They are not: a path for contesting the plan is unusable by someone who
cannot tell what the plan covered, and the operator's only route to that today
is reading the run's own specification documents and cross-referencing them by
hand.

Three parts, one loop: **show what was built → the operator spots a gap →
they contest it → the leader adjudicates and the outcome is recorded.**

## What this deliberately leaves standing

- **Finding 1** — acceptance rejects a worker whose declared deliverables are not
  exactly `required_outputs`, which misfires on honest work. That is the rule
  that actually ended run `699c1915` and it needs its own design.
- **Finding 2's second half** — letting the environment declare what it can
  verify, so a planner does not write a contract whose only checks are file
  reads for code that cannot be built here. Part 1 below makes the weakness
  *visible*; it does not fix it.
- **Machine extraction of obligations from upstream documents.** Feasible when a
  document enumerates them with identifiers, as `service-plan.md` did with
  `T-01`–`T-23`, and a judgement problem otherwise. The same promised-versus-built
  comparison is what the `project-feature-map` skill already produces outside a
  run.

## Part 1 — What the run built

Nearly all of this is already stored per task and simply not shown together.

**Promised versus declared.** `task.acceptance.required_outputs` against
`task.outcome.deliverables`, with the difference in both directions. Task 8 of
run `699c1915` promised four files and declared seven; that fact appears nowhere
in the UI today, even though it is why the task was rejected.

**Whether the declared files exist now.** Resolve each declared path inside the
run's workspace and report missing ones. Use the same containment rule the gate
uses (`safe_workspace_file`) so a path that escapes the workspace is reported as
missing rather than followed.

**How each verification was settled — named honestly.** The gate records a `mode`
per required verification, and there are exactly two:

| stored `mode` | what actually happened | label to use |
| --- | --- | --- |
| `verified` | the contract gave the verification a `check`, and the gate ran it | `파일 내용 확인` / "file inspected" |
| `attested` | the contract gave no check, so the worker's own `passed` was accepted | `워커 신고` / "worker asserted" |

The label matters more than it looks. Every check kind
(`team_verification_checks.py`) is a file read — `file_nonempty`,
`file_contains`, `file_matches`. **No check kind compiles anything, runs a test,
or executes a command.** So `verified` means "we read the file and looked at its
text", and a UI that renders it as "검증됨" would tell the operator something
untrue. Run `699c1915`'s task 7 passed on `admin-router-registered`,
`llm-schema-validation-present`, and `ingest-tests-written` — all satisfied by a
file existing and containing a string — while implementing 10 of the admin
endpoints the service plan called for.

**Run-level rollup.** How many tasks were accepted with `attested_only` true
(no required verification had a runnable check at all), and how many declared
files are missing. These two numbers are the honest headline: they say how much
of the run's verdict rests on the workers' own word.

**Where it goes.** Inside the existing TASKS tab, not a new screen. Screen
composition follows the Claude Design mockup, and inventing a screen the mockup
does not have is not this design's call to make.

## Part 2 — Coverage gaps, as reported by the leader

At the end of a cycle the leader already writes a synthesis. Ask it there for one
more thing: obligations in the accepted specifications that no task owns, each
with the document and section it comes from.

**The synthesis is prose, not a JSON object.** `_validated_synthesis_result`
accepts either a plain-text summary or an `ask_user` resolution; there is no
field list to extend. So the request is for an optional fenced block the parser
looks for and ignores when absent:

````
```coverage-gaps
[{"obligation": "T-04 discard a draft", "document": "docs/english-learning/service-plan.md §4", "note": "no task owns this"}]
```
````

**Optional by construction, and that is the point.** Synthesis is a leader stage;
a leader stage that cannot be parsed costs the cycle. Trading a run for a
nice-to-have field is the wrong exchange, so a missing or malformed block is
recorded as absent and the summary is used as it stands. The UI then says
plainly that **the leader reported no coverage gaps** — distinguishing "reported
none" from "did not report", because those mean different things.

**The leader's report is weak evidence, and it is still worth having.** This
leader already approved a plan narrower than the specification the same run had
just written, so it will often claim full coverage. That is not a failure of the
design — it is the target the operator needs. Seeing "no gaps" next to a product
that visibly lacks discard and reject behaviour gives the operator a recorded
claim to contest, where today there is nothing to point at.

If it turns out the leader omits the block on every cycle, promote it to its own
non-blocking stage then. Not before.

## Part 3 — Contesting the plan

### Why a new stage rather than a bigger add-work prompt

The leader has exactly one prompt for producing a plan, and its whole instruction
is to break the request into tasks. Run `699c1915`'s ledger shows no
`cycle_planning` operation at all — one `cycle_add_work`, from which the entire
13-task plan came. Adding verdicts to that prompt makes every planning path
heavier and widens the blast radius of `_valid_task_plan`, which every planning
path shares. Adjudication is a different verb, so it gets a different stage.

### Flow

```
POST /api/team-runs/{id}/contests {objection}
  → a queued cycle request with source_type "contest", the objection as its instruction
  → dispatcher: claim_next returns None while another request is dispatching,
    so the contest waits for the current cycle to settle
  → the contest claims its own cycle (run_one creates one per request)
  → cycle_contest stage, on the operation ledger → a verdict
       amend | partial  → apply_plan creates the tasks in that cycle,
                          which then executes them like any other cycle
       reject           → that cycle settles immediately, holding no tasks
       ask_back         → raise_system_decision, the run waits for the user
```

A contest therefore occupies a cycle of its own rather than reopening the one it
objects to — `run_one` creates a cycle per claimed request, and reusing a settled
cycle would mean reviving its status. A rejected contest leaves a cycle that ran
one model call and created nothing, which is the provenance worth having: the
objection, the refusal, and its reason are all attached to something durable. It
consumes a cycle slot like any other request.

Nothing new serializes this. `claim_next` already refuses to claim while a
request is `dispatching`, and because the contest never interrupts in-flight
work it cannot reproduce the class of bug that `/add-work`'s mid-flight
application caused when it collided with cancel (fixed in `8f900b0`).

`source_type` is an open string with a validated allowlist;
`knowledge_request` is already a precedent for a different purpose riding the
same queue, so `contest` is one entry alongside it, sharing `manual`'s
`source_id` idempotency.

### The verdict

Modelled on `acceptance_lead`, whose response already carries a `kind` such as
`retry_worker`, so this copies a shape rather than inventing one.

```json
{
  "kind": "amend | partial | reject | ask_back",
  "reason": "required for every kind",
  "tasks": [
    {"title": "...", "description": "...", "owner_agent_id": "... or null",
     "required": true,
     "acceptance": {"required_outputs": ["..."], "required_verifications": ["..."]}}
  ],
  "question": "ask_back only",
  "supersedes": [{"document_path": "...", "decision": "..."}]
}
```

Each entry of `tasks` is exactly what `_valid_task_spec` already accepts — the
same five fields a plan entry carries — so `_valid_contest_verdict` delegates to
it rather than restating the shape, and `apply_plan` consumes the result
unchanged.

`_valid_contest_verdict` enforces:

- **`reason` is required for every verdict.** An empty or missing reason is
  treated as a parse failure, so the repair seam asks once more. A verdict with
  no reason is worthless as a record, which is half of why this feature exists.
- `amend` and `partial` carry at least one task; `reject` carries none;
  `ask_back` carries a question.
- **A non-empty `supersedes` requires a non-empty `tasks`.** If the leader
  admits an agreed decision is being overturned, the work of correcting the
  document that still states the old decision has to come out of the same
  verdict.

`partial` is structurally identical to `amend` and kept as a distinct kind
anyway: the record needs to be able to say "half of this was declined", and one
enum value is a cheap price for a record that does not overstate agreement.

### Why `supersedes` exists

`docs/english-learning/srs-algorithm.md` §1 required a vetted FSRS library and
forbade reimplementing the weights. The worker reimplemented them, and the
document was edited during the run — with leader approval — so that a later
section sanctions what the code does. The decision was reversed and nothing
records that it was; read the repository today and it looks like that was always
the plan. With this rule the same episode leaves a verdict that names the
reversal, its reason, and a task to correct §1.

### A short contract is answered with a task, not a rewrite

`record_acceptance_decision`'s `revise_acceptance` requires the task to be
`in_progress`. A contest is adjudicated after the cycle settles, when its tasks
are already terminal, so that path is unavailable — and rather than add an
effect that rewrites a settled task's contract, an amend creates a follow-up task
carrying the contract that should have been there. No new effect, the original
task's history stays intact, and what changed is legible as the difference
between two tasks.

### After a rejection

The leader's prompt carries that run's earlier objections and verdicts (kind and
reason) in order, so it does not repeat a refusal it has already reasoned
through, and the operator can answer the stated reason. There is no path for the
operator to overrule a rejection: routing around the leader's judgement removes
the only thing this feature adds.

### Where the outcome is recorded

- The `cycle_contest` operation on the ledger already holds the request digest
  and the verdict.
- A new effect, `apply_contest_verdict`, applies the verdict and writes one
  `team_messages` row with `kind` `plan_adjudication`, sent by the leader and
  tied to the cycle — a path the activity stream already renders, so the timeline
  view follows for free.
- `GET /api/team-runs/{id}/detail` gains `contests`: objection, kind, reason,
  `supersedes`, timestamps.

## Coupling checklist

A new stage is read in more places than it is written. These were enumerated by
following every site that names `cycle_add_work`; missing two of them is what
produced NameErrors during the previous stage addition.

| file | what changes |
| --- | --- |
| `team_model_operations.py:18` | `OperationStage` gains `cycle_contest` and `cycle_contest_repair` |
| `team_model_operations.py:635` | validator registry gains `{"contest_verdict": _valid_contest_verdict}` |
| `team_repair_stages.py:14` | `"cycle_contest": "cycle_contest_repair"` — the completeness test fails until this exists |
| `team_model_effects.py:56` | the planning-stage set, plus `apply_contest_verdict` |
| `team_runtime.py:642, 1340` | the `planning_stage` literal, or a separate argument |
| `team_runtime.py:1405` | `_execute`'s allowlist — the `continue` group |
| `team_cycle_dispatcher.py:343` | `_resume_operation` must resume a recovered contest |
| `team_provider_recovery.py:678, 737` | classify a contest as preplanning, like add-work: `cycle.status == "queued"`, `task_id is None` |
| `team_cycles.py:1242` | `source_type` allowlist gains `contest` |

## Verification

- Each of the four verdict kinds produces its effect: `amend` creates tasks,
  `reject` creates none and settles the cycle, `ask_back` leaves the run
  `waiting_for_user`.
- A verdict with no reason is rejected and re-requested through the repair seam;
  a second failure escalates the way any leader stage does.
- A verdict with `supersedes` but no tasks is rejected.
- A contest queued while a cycle is running does not interrupt it, and is
  adjudicated after it settles.
- The second contest's leader prompt contains the first rejection's reason.
- The `OperationStage` completeness test demands both new stages.
- Part 1 against run `699c1915`'s stored data: task 8 shows four promised and
  seven declared; task 7 shows its three verifications as file-inspected, not as
  tested; the rollup counts the attested-only tasks.
- Part 2 with a synthesis carrying no block, a malformed block, and a valid one —
  the first two leave the summary intact and record the report as absent.
