# Letting a worker say a check did not run

Run `699c1915` shipped three `.tsx` files nobody knows compile. The worker tried
to check them, could not, said so, and was accepted anyway.

Its own report, still in that workspace at
`english_learning/frontend/API_GAPS.md`:

> TypeScript 타입 체크는 실행되지 않았다. `english_learning/frontend`에 TypeScript
> 의존성이 설치되어 있지 않다. 시도한 명령은 `npx --no-install tsc --version`이다.

So the worker ran a command, read the result, and knew the answer. It wrote that
into a Markdown file because the outcome schema gave it nowhere else to put it:

```python
status not in {"passed", "failed"}   # team_outcomes.py — these two, and nothing else
```

Nothing reads that Markdown file. The gate checked that the files existed and
contained the string `export`, and passed the task.

## What this changes

Verification reporting splits into two fields:

```json
{"name": "frontend-typechecks", "checked": false, "status": null,
 "evidence": "npx --no-install tsc: typescript-unavailable"}
```

- `checked` — whether the worker actually confirmed it
- `status` — the result when it did (`passed` / `failed`), `null` when it did not

**The split is the point, not the extra value.** With one enum a worker can report
`passed` for something it never ran, and that is the path of least resistance —
nothing in the shape distinguishes "I checked and it passed" from "I am asserting
it passes". Two fields make the dishonest case require an explicit `checked: true`,
which is a separate, auditable claim rather than a default.

## Who executes

**The worker, while it does the work.** Not the gate.

This is not execution being left out of verification — it is the recognition that
execution already happens on the worker side and only there. The worker runs
under a CLI with tool access, governed by the permission mode the operator already
configured; it chose and ran `npx tsc` in the motivating run without anyone adding
machinery for it. The contract's `required_verifications` already reach it, as an
`Acceptance criteria:` block in the worker prompt.

The gate stays a reader. Giving it execution would mean building a new boundary
inside the API process: `TeamAcceptanceService.evaluate` is called synchronously
from `async` code (`team_runtime.py:2387`), and the existing `ShellRunner` has no
timeout at all, so a check that hangs would hang the server. Two bugs of exactly
that shape were fixed in this repo on 2026-08-11. None of that is needed if the
worker is the one running commands.

## What an unchecked verification does

The task is **accepted**. Refusing it would kill every run in an environment
without the dependencies installed — which is the environment the motivating run
was in — and the goal is to stop unverified work from passing *silently*, not to
stop it from passing.

Three places record it instead:

- The acceptance evidence gains an `unverified` list naming those verifications.
- The task detail view, where a verification currently renders as `파일 내용 확인`
  or `워커 신고`, gains **`미확인`**.
- The run-level rollup counts tasks carrying an unverified verification.

That last one also repairs a known weakness. The rollup today reads
`워커 신고만으로 통과 0 / 13`, which sounds like rigour; `attested_only` is only
true when a task had *zero* runnable checks, so it stays 0 for a run where every
check was a file read. Counting unverified verifications gives the summary a number
that moves when something actually went unchecked.

## Old outcomes

Stored outcomes carry only `passed` / `failed`. The parser reads a verification
with no `checked` field as `checked: true` — at the time that value *was* the
worker's claim to have checked, so the meaning is preserved. No migration.

## Deliberately not here

**Making the leader ask for verification worth having.** The motivating run's
contract required `admin-router-registered`, `llm-schema-validation-present` and
`ingest-tests-written` — all satisfied by a file containing a string. The type
check was never a contract item at all; the worker attempted it on its own
initiative. This design lets a worker be honest and makes that honesty visible. It
does not make the leader demand more than a file read, and it adds no check kind
that compiles or runs anything. That is the other half of Finding 2 and needs its
own design.

**Verifying that `checked: true` is true.** A worker can still claim it checked
something it did not. Detecting that needs either the command and exit code as
evidence the gate can re-run, or the gate running the check itself — both larger,
and both pointless while the contract only ever asks for file reads.

## Coupling checklist

| file | what changes |
| --- | --- |
| `team_outcomes.py:19` | `VerificationEvidence` gains `checked`; `status` becomes optional |
| `team_outcomes.py:111-129` | the parser accepts the new shape, tolerates the old one as `checked: true`, and rejects `checked: true` with a null status. Note it currently pins the key set exactly — `set(raw) != {"name", "status", "evidence"}` — so it has to accept two combinations rather than being loosened to a subset check |
| `team_runtime.py:121` (`WORKER_PROMPT`) | the output schema shown to the worker, plus a line saying to report `checked: false` with the reason rather than guessing |
| `team_acceptance.py:100-147` | a verification with `checked: false` no longer satisfies a required verification by its status, and lands in the evidence's `unverified` list |
| `team_build_evidence.py` | carries `unverified` per task and counts it in the rollup |
| `frontend/.../BuildEvidence.jsx` | renders `미확인`, and the rollup line gains the count |

## Verification

- A worker reporting `checked: false` on a required verification is accepted, and
  the acceptance evidence names that verification as unverified.
- The same outcome does **not** report the verification as passed anywhere.
- A worker reporting `checked: true, status: "passed"` behaves exactly as today.
- `checked: true` with a null status is rejected as a malformed outcome — that
  combination is the shape of a worker trying to have it both ways.
- An outcome in the old shape (`status` only, no `checked`) parses as
  `checked: true` and behaves as it did before, asserted against a stored payload
  from run `699c1915` rather than a hand-written one.
- The run rollup counts a task with an unverified verification, and the task view
  renders `미확인` for it while still rendering `파일 내용 확인` for a gate-run check.
- A required verification the worker omits entirely still fails the task as it does
  today — the new field must not turn a missing report into a tolerated one.

## What the implementation actually showed

Built on `feat/unchecked-verification-report` (`0621a13`, `d0ea82a`+`c43c652`,
`c0c4ab0`, `c6cc311`+`7b278cf`). Every bullet above is covered by a test. What the
runs beyond the tests showed:

- **Backward compatibility is stronger than this document claimed.** The design
  promised old outcomes parse as `checked: true`; verified against **every** stored
  outcome in `data/app.sqlite`, not just run `699c1915`'s: 24 outcomes, 0
  unparsable, and all 137 verifications read as `checked=True` with their original
  `passed`/`failed` status. Run `699c1915` accounts for 11 outcomes / 44
  verifications.
- **Full backend suite: 21 failed, 1554 passed, 4 skipped** — exactly the
  pre-existing baseline (`test_runtime_factory_headless.py` 16,
  `test_api_agents.py` 4, `test_api_dashboard.py` 1), with passes up 18 from the
  new tests. Frontend: 41 files / 400 tests / 0 failures against a baseline of
  399 measured on this branch beforehand. `ruff check src/ tests/` clean.
- **Run `699c1915`'s rollup reads `unverified_task_count: 0`**, which is the
  correct answer, not a wiring failure: its stored acceptance results predate the
  key. Rebuilding that run's evidence through the same functions `/detail` uses
  gives `{"task_count": 13, "worker_asserted_only_count": 0,
  "missing_file_count": 0, "unverified_task_count": 0}` and still reports the
  promised-versus-declared mismatches — 2 tasks naming 5 undeclared promises
  between them (the three `.tsx` files this design came from, plus two QA docs).

### What the whole-branch review caught that the per-task reviews could not

Both findings were in the same blind spot: this document's coupling checklist named
`BuildEvidence.jsx` as the only screen, and the numbers already on that screen were
declared out of scope. Fixed in `54f3d64`.

- **The task dialog renders verifications twice.** `BuildEvidence` reads the gate's
  recorded evidence and said `미확인`; the acceptance list beside it
  (`index.jsx:263`) read the worker's raw report and fell back to
  `status || "missing"` — so an unchecked verification, whose status is `null` by
  design, printed **`MISSING`**. That word meant exactly one thing until now: the
  worker never reported this verification at all. Shipping this would have created
  the very conflation the design exists to end, in the one screen it exists to
  improve. The list now reads the gate's recorded status for that mode, as it
  already did for a gate-run check. Confirmed by reverting the fix and watching the
  new test fail on the literal string `MISSING`.
- **Two older labels claimed the wrong thing.** `attested_only` is
  `verified_count == 0` — the gate ran no check — which is not "the worker vouched
  for it". A task whose only required verification came back `checked: false` sets
  that flag while the worker vouched for nothing; it explicitly declined to. The
  badge said `ATTESTED ONLY` and the rollup said `워커 신고만`. Both now name what
  the gate did: `NO GATE CHECK` and `게이트 미검사`. The computation is untouched,
  so every number keeps its meaning and no other reader changes. The three counts
  overlap by design and the code now says so.

### A floor on the new count, worth knowing before reading a 0

The gate consults a worker's report only for a required verification carrying no
`check`. For a contract where every required verification carries one, a worker
could report `checked: false` on all of them and `unverified_task_count` would still
read 0. Run `699c1915` is exactly that shape — all 11 of its tasks are 100%
check-carrying, and 53 of the 70 required verifications stored in `data/app.sqlite`
carry a check. So `미확인 0` means "nothing the worker was asked to self-report went
unconfirmed", not "nothing went unchecked". Recorded in `run_build_evidence`'s
docstring rather than left for a reader to discover.

### Known and out of scope

- A worker's `checked: false` on a verification that carries a `check` is
  discarded: the gate records `mode: "verified"` with its own evidence and the
  worker's reason survives only in the raw outcome JSON, which nothing renders.
  The gate deciding alone is correct and pinned by a test; only the reporting loss
  is a gap — the same "wrote it where nothing reads it" failure, in miniature.
- Adding `unverified` to the accepted evidence changes `_worker_input_digest`, and
  `checked` changes `asdict(outcome)`. A `task_outcome` operation applied by
  pre-branch code and replayed after this lands raises `OperationConflict`. The
  window is a run interrupted between apply and caller-progress across the deploy;
  all runs in `data/app.sqlite` are terminal, so nothing is at risk today. This is
  structural to any evidence-shape change.
- Pre-existing, found while reviewing this: `team_outcomes.py`'s
  `status not in {"passed", "failed"}` raises a bare `TypeError` for a model that
  emits `"status": []`, and `_task_outcome` catches only `TaskOutcomeError`, so it
  escapes unhandled instead of becoming `invalid_task_outcome`. Same expression
  predates this branch.

### What could not be verified, and why

- **The HTTP and browser leg.** `/api/team-runs/{id}/detail` now answers
  `401 {"code":"http_401","detail":"OTP login required"}`, so the payload was
  verified by rebuilding it from `data/app.sqlite` through the same
  `task_build_evidence` / `run_build_evidence` calls the endpoint makes
  (`api/team_runs.py:493` and `:528`) — everything except the auth and serialization
  layers. Confirming the rendered `미확인` in a real browser needs an operator to
  log in; the component test covers the rendering itself.
- **A real `checked: false` from a live worker.** Producing one requires a model
  running in an environment missing a tool it wants to use. Not forced. The path is
  covered by tests at the parser, gate, evidence, and render layers, and the prompt
  was checked line-by-line against what the parser accepts.
