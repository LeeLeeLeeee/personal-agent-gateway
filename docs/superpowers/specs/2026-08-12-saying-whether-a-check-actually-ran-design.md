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
