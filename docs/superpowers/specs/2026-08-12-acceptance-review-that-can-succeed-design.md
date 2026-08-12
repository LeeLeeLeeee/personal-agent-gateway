# Stopping a task from dying on a review that could never work

Run `699c1915` lost tasks 8 and 9 to the same sequence. Each worker wrote files
beyond its contract, declared all of them honestly, and was rejected with
`undeclared_deliverable`. The leader reviewed, chose `retry_worker`, and told the
worker to declare everything. The contract still listed the original four, so the
retry produced the identical rejection, the recovery cap ran out, and the run
reported `Required task failed`.

This corrects a diagnosis as much as it fixes a defect. The findings document
(`2026-08-11-team-run-completeness-findings.md`, Finding 1) recorded that
"recovery cannot resolve it either" and that the worker's retry prompt carries
contradictory instructions. Reading the code shows both claims are wrong, and the
real cause is narrower and easier to fix.

## What the findings document got wrong

**Recovery is fully wired.** `_run_cycle_acceptance` collects the rejected paths
when the reason is `undeclared_deliverable`, `_persisted_undeclared_paths` carries
them across attempts, and the leader can widen the contract with
`revise_acceptance`. `record_acceptance_review` stores the revised acceptance and
returns the updated task, which is the task the worker's retry prompt is then
built from — so there is no contradiction once the contract is actually widened.

**Widening works.** Verified by running the gate directly: a contract listing one
output rejects a three-deliverable outcome with `undeclared_deliverable`, and the
same outcome against a contract listing all three is accepted. The mechanism the
fix depends on does what it needs to.

## The real cause

`ACCEPTANCE_REVIEW_PROMPT` tells the leader:

> Prefer Worker correction when the contract is valid. Revise acceptance only when
> the contract itself is wrong.

For honest extra work the contract *is* too narrow, so `revise_acceptance` is
correct — but nothing says so, and the stated default pulls the leader toward
`retry_worker`. Choosing `retry_worker` while leaving the contract alone can only
succeed if the worker declares *fewer* files, so an instruction to declare all of
them guarantees the same rejection.

The system holds every fact needed to notice this — the reason code, the exact
extra paths, the action, and whether the contract changed — and checks none of
them.

## Three parts

### Part 1 — tell the leader what works for this rejection

Add to `ACCEPTANCE_REVIEW_PROMPT` a clause specific to `undeclared_deliverable`:
keeping the extra files requires `revise_acceptance` with the widened
`required_outputs`; `retry_worker` is only for having the worker remove them, and
must name every path to remove. The existing "prefer Worker correction" line stays
as the general rule with this rejection called out as its exception.

The leader already receives the rejected paths, so it can see what the extras are.
What is missing is guidance on which action resolves them.

### Part 2 — reject a review that cannot succeed, and be honest about the limit

Refuse a resolution where the reason is `undeclared_deliverable`, the action is
`retry_worker`, and the instruction does not name every extra path.

**Where that check goes, corrected.** The live path is `_run_cycle_acceptance`,
which invokes the leader through `_invoke_with_repair` with the **module-level**
parser `_validated_acceptance_review` — a function that sees only the response and
therefore cannot know the extras. The check needs a parser closure built at that
call site, holding `task` and the rejected paths and delegating to
`_validated_acceptance_review` before applying the extra rule. Several other stages
already build their parser inline this way.

`_review_acceptance` is a separate, older path that calls `model.complete`
directly with its own single retry and no ledger operation. It is reached from
`_recover_task_outcome`, not from the cycle path real runs take. The same check
belongs there for consistency, and a raise is caught by that method's existing
`except ValueError`, which retries once — equivalent behaviour by a different
mechanism.

**This narrows the failure; it does not prove coherence.** The check is a proxy
for "will this instruction make the worker declare fewer files", which no
validator can decide from prose. Two residual cases:

- A leader that writes "declare `src/extra.py` along with the others" names the
  path and still keeps it. The check passes and the retry fails again. Part 3
  covers this.
- A leader that writes "remove everything outside the contract" is coherent but
  names nothing. Part 1's prompt closes this by requiring the paths, so the check
  is enforcing a stated format rather than guessing at intent.

The parser is the right home because a rejection there raises
`InvalidOperationResult`, which the repair seam catches on the live path: the
leader is asked once more with a message naming the extras and the fact that
keeping them needs a widened contract. That costs no acceptance attempt, so a
formatting mistake cannot kill the task. A second failure escalates to the
operator through the `on_exhausted` hook already wired at that call site.

It cannot live in `_valid_acceptance_resolution`: that validator receives only the
leader's own JSON and has no access to the rejection or the extra paths.

### Part 3 — ask instead of dying when the attempts run out

Today, when `attempts >= ACCEPTANCE_RECOVERY_CAP`, `_run_cycle_acceptance` returns
and the task fails. That is where tasks 8 and 9 died, and where the run learned
nothing except `Required task failed`.

Replace that death with a question, but only for this rejection: if the cap is
reached while the rejection is `undeclared_deliverable` and the same extra paths
have been rejected before, publish a decision request naming those paths and
asking the operator whether to widen the contract or drop them.

**At the cap, not at the repeat.** An earlier draft of this section fired on the
second failure, replacing the second review. That is wrong: Part 1 has improved
the prompt, so the second review is exactly the chance the leader should get to
choose correctly. Only once the attempts are spent is the loop proven futile — and
that is also the only point where the alternative is the task dying, so nothing is
pre-empted.

This judges nothing about the instruction text, which is why it covers Part 2's
first residual case: a review that names the paths and still keeps them passes
Part 2, burns the attempt, and lands here.

The arithmetic: the cap check runs before the review and each review that consumes
an attempt increments the counter, so failures one and two each get a review and
failure three reaches the cap. Part 3 replaces what failure three does.

## Deliberately not here

**The set-equality rule stays.** It is scope control: it catches a worker quietly
editing files it does not own, and in this very run task 8 modified `schemas.py`,
which is task 7's contract output — real cross-task interference. Allowing
own-area extras automatically would remove that signal and require defining
"own area", which is a larger question. The rule caught something; what failed was
the handling.

**No verification is required when the contract widens.** Running the gate with a
widened contract returns `attested_only: true` — accepted with no runnable check
on any output. That is worth seeing, and the build-evidence view merged on
2026-08-12 shows it per task and per run. Forcing a check here would conflate
scope control with verification depth, and every check kind is a file read anyway,
so the requirement would buy less than it appears to. Finding 2 is where that
belongs.

**The leader's judgement is still its own.** If it widens a contract to admit an
extra that genuinely should not have been written, that is a bad approval and
nothing here detects it. This design removes deaths caused by *form* and raises a
futile loop to the operator; it does not make the leader right.

## Coupling checklist

| file | what changes |
| --- | --- |
| `team_runtime.py` — `ACCEPTANCE_REVIEW_PROMPT` (around line 145) | the `undeclared_deliverable` clause and the requirement to name paths |
| `team_runtime.py` — the `_invoke_with_repair` call in `_run_cycle_acceptance` (around line 2280) | replace the module-level `_validated_acceptance_review` with a closure that also applies the Part 2 refusal |
| `team_runtime.py` — `_review_acceptance` (around line 2557) | the same refusal on the older direct-`model.complete` path, where its existing `except ValueError` provides the retry |
| `team_runtime.py` — `_run_cycle_acceptance` (around line 2613) | Part 3 replaces the `attempts >= ACCEPTANCE_RECOVERY_CAP` return for this rejection, using the paths `_persisted_undeclared_paths` already carries |
| `teams.py` — `raise_system_decision` | reused for Part 3's decision request; no change expected |

`_valid_acceptance_resolution` in `team_model_effects.py` is deliberately
untouched.

## Verification

- The gate accepts a widened contract against the same declared set, and rejects
  the narrow one — the premise, pinned by test rather than assumed.
- A `retry_worker` resolution on `undeclared_deliverable` that names none of the
  extras is refused and repaired once; one that names them all is accepted.
- A `revise_acceptance` resolution widening the contract leads to an accepted
  retry end to end through the runtime, not just through the gate.
- Reaching the cap on `undeclared_deliverable` with the same extras publishes a
  decision request naming them and leaves the run `waiting_for_user` with the task
  still `in_progress`, rather than failing it. Reaching the cap on any other
  rejection still fails the task as it does today — the change must not swallow
  every acceptance failure into a question.
- Answering that request resumes into an acceptance attempt that can succeed.
- A `retry_worker` that names the extras still works — the fix must not block the
  legitimate cleanup case, which is the reason the set-equality rule exists.
