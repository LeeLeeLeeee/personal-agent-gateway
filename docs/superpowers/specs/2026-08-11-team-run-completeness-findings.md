# Team run completeness — three findings from run 699c1915

Run `699c1915fa764be598586d2f8bb3a170` failed with `Required task failed` after
83 minutes. The immediate cause was a leader acceptance review that could not be
parsed, and that is addressed separately in
`2026-08-11-team-run-structured-output-resilience-design.md`.

This document records three findings that the failure exposed but that fix does
not touch. They are written down because each one is invisible in the run's own
output: the run reported a task-level failure while the product it produced was
about two thirds complete, and nothing in the pipeline said so.

None of these are scheduled. The point is that the next person reading the
resilience fix can see what it deliberately leaves standing.

## Finding 1 — The acceptance contract is a closed set, and recovery cannot widen it

`TeamAcceptanceService.evaluate` (`team_acceptance.py:88-93`) requires the
worker's declared deliverables to equal `required_outputs` exactly:

```python
if expected - declared:
    return _rejected("failed", "required_output_missing")
if declared - expected:
    return _rejected("failed", "undeclared_deliverable")
```

The intent is scope control — stopping a worker from quietly editing files it
does not own. The implementation cannot tell that intent apart from ordinary good
work:

- Task 8's contract listed four files. The worker also wrote
  `tests/test_study.py`, edited the shared `schemas.py`, and updated
  `docs/english-learning/srs-algorithm.md`, then declared all seven honestly.
- Task 9's contract listed three files. The worker also wrote `API_GAPS.md` and a
  verification script, and declared five.

Both were rejected identically, yet only one of the extras is a real risk:
`schemas.py` is task 7's contract output, so editing it is cross-task
interference. `API_GAPS.md` and an extra test are the task's own area.

**Recovery cannot resolve it either.** The leader's review told the worker to
declare all seven files, while `acceptance_after` was `null` in both reviews —
the contract was not widened. Following the instruction produces
`undeclared_deliverable` again. The worker's retry prompt also carries
`_acceptance_worker_messages`' "Authoritative current acceptance criteria" with
the original four, so it receives two contradictory instructions in one message.

A shape that would separate the two cases: classify a declared path by area
rather than by set membership — required (must exist), own-area extra (allowed,
recorded), foreign or out-of-area (needs explicit leader approval, and the review
must set `acceptance_after` to record it). That is a design sketch, not a
decision.

**This is the trigger.** The resilience fix stops a single unparseable response
from killing a task; it does not stop this rejection from happening in the first
place.

## Finding 2 — The gate can pass software that was never compiled or run

Task 9's contract required three files and verified them with
`file_contains: "export"` and `file_nonempty`. Both passed. The frontend was
never type-checked: `english_learning/frontend/node_modules` does not exist, and
the worker recorded why in its own `API_GAPS.md`:

> TypeScript 타입 체크는 실행되지 않았다. (…) 네트워크가 막힌 환경에서 (…)
> TypeScript 타입 오류와 React import 경로 오류는 미검출일 수 있다.

So three `.tsx` files entered the run's output with no evidence they compile.

The same weakness let a *passing* task ship an incomplete backend. Task 7
("관리자 콘텐츠 등록·LLM 재가공 백엔드") was accepted on five file paths plus
`admin-router-registered`, `llm-schema-validation-present`, and
`ingest-tests-written` — all satisfied by files existing and containing a string.
It implemented 10 of the admin endpoints the service plan calls for; the frontend
later enumerated 9 missing API surfaces it therefore could not call.

The machinery to express this already exists and is not used for judgement:
`acceptance_result.evidence.attested_only` is computed, and the UI renders an
`ATTESTED ONLY` badge. What is missing is (a) carrying that signal into the run's
release verdict rather than a per-task badge, and (b) letting the environment
declare what it can verify, so the planner does not write a contract whose only
checks are existence checks for code that cannot be built here.

## Finding 3 — The plan did not cover the upstream specification

`docs/english-learning/service-plan.md` — produced and accepted earlier in the
same run — enumerates **23 state transitions**, `T-01` through `T-23`. The plan
that followed created 13 tasks:

| ordinal | task |
| --- | --- |
| 0-5 | requirements, service flow, learning modes, SRS spec, UX spec, architecture |
| 6 | SQLite schema and migration |
| 7 | admin content ingest backend |
| 8 | study session and scheduling backend |
| 9 | admin content management screen |
| 10 | learner study screen |
| 11 | QA strategy |
| 12 | functional verification |

No task owns the remaining content-lifecycle transitions. The implemented surface
is 14 routes (10 admin + 4 study), and the frontend's gap report lists the
consequences by transition: force-register (T-03), discard (T-04, T-12, T-17,
T-22), restore (T-23), reject (T-15), republish (T-20), return-to-review (T-21).

This is not a worker failure. Every developer task passed or failed on its own
contract, and every contract was narrower than the specification the same run had
just written. Nothing compares the two, so the omission is silent — and it is the
largest visible gap in the product, which is why the run reads as "mostly not
done" even though the tasks that ran mostly succeeded.

A coverage check is feasible when the upstream artifact enumerates obligations
with identifiers, as this one does: after planning, map every `T-xx` to a task
and surface the unmapped ones. Where the upstream document has no identifiers it
needs judgement instead. The same comparison — promised versus implemented,
both directions — is what the `project-feature-map` skill produces, so the tool
for it now exists outside the run.

## Finding 4 — The user cannot contest the plan, only add to it

**Designed 2026-08-12 in `2026-08-12-team-run-plan-visibility-and-contest-design.md`,
together with the operator-facing half of Finding 3.** Following the code while
writing that design sharpened this finding: `/add-work` refuses runs whose
`lifecycle_mode` is `continuous`, which both existing runs are, so the endpoint
quoted below is not even the path they take. Instructions arrive as cycle
requests and land on the `cycle_add_work` stage — and run 699c1915's ledger holds
no `cycle_planning` operation at all, one `cycle_add_work`, from which its entire
13-task plan came. The prompt below is therefore not merely *one* leader path; it
is the only prompt that ever produces a plan.

Seventeen endpoints let the user act on an in-flight run. None of them lets the
user say the plan itself is wrong and have the leader adjudicate.

`/add-work` is the only one that routes through the leader, and its prompt gives
the leader exactly one job:

> The user is adding work to an in-flight run. **Break the request into concrete
> tasks.** Return ONLY a JSON array of task objects…

The response schema is an array of tasks, so there is no place to express a
refusal, a conflict with an accepted decision, or "task 7 already covers this".
The leader can only decompose. Whatever the user sends becomes tasks.

Two consequences already visible in this run:

- **A reversed decision leaves no record.** `srs-algorithm.md` §1 required a
  vetted FSRS library and forbade reimplementing the weights. The worker
  implemented them anyway, and the document was edited during the run — with
  leader approval, per the review instruction — so that a later section sanctions
  what the code does. The decision was reversed and nothing records that it was.
  With no adjudication step, a change arrives as a quiet document edit.
- **Finding 3 has no remedy from the user's side.** Noticing that the discard and
  reject transitions have no owner, the only available move is free-text
  `/add-work`, which the leader will decompose into tasks without ever judging
  that the *plan* was short of the specification.

What a harnessed path would have to settle:

- **Target.** A change request can aim at the task set, at one task's contract
  (`required_outputs`), or at an accepted design decision. Those are judged
  against different things, so the target has to be explicit.
- **Verdicts.** Accept-and-amend, reject-with-reason, partial, and ask-back are
  the minimum. `acceptance_lead` already expresses adjudication this way — its
  response carries a `kind` such as `retry_worker` — so there is a precedent to
  copy rather than a format to invent.
- **Where the outcome is recorded.** This is the part the FSRS case shows
  missing. Both a rejection and an acceptance need the reason stored, and an
  acceptance that overturns an accepted design decision should require the
  corresponding document or ADR to be updated as part of the same step, not
  after the fact.
- **When it lands.** Immediately, interrupting the in-flight task, or after the
  current task settles. `/add-work` applies mid-flight today, which is how it
  collided with cancel and produced the resurrection bug fixed in `8f900b0`.

Implementation would be cheap relative to its value: `/add-work` already runs
through the leader as the `cycle_add_work` stage, with the operation ledger,
recovery, and resume wired around it. A change request is its sibling — the same
plumbing with a different verb and a verdict-shaped response.

## How these relate

Findings 2 and 3 are one failure seen from both ends: a plan narrower than the
specification passes because the gate checks existence rather than behaviour.
Fixing either alone leaves the other able to hide an incomplete product.

Finding 1 is separate. It is a correctness rule that misfires on honest work, and
it is what actually ended this run.

## Also noticed, unrelated to completeness

- `interventions.py` was an in-memory store with no references in `app.py`,
  `team_runtime.py`, or any API module. **Resolved 2026-08-12: removed.** The
  capability it reached for already exists, wired and persisted, as the team-run
  decision request flow, so a second in-memory version could only drift from it.
- `docs/english-learning/srs-algorithm.md` contradicts itself: §1 requires a
  vetted FSRS library and forbids reimplementing the weights, while a later
  section sanctions the in-repo implementation that `srs.py` actually contains.
  The review instruction shows the document was edited during the run with
  leader approval, so a decision was reversed with no record that it was.
