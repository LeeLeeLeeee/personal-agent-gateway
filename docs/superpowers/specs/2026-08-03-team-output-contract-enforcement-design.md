# Team Output Contract Enforcement Design

## Goal

Make a Team Run actually satisfy the output contract its caller set, and make
task acceptance check what a task produced instead of trusting what the worker
said about it.

## Problem

Two independent defects let a Team Run report success while producing nothing
the caller can use. Both were observed on cycle
`bbc37fe14bb144c1a1c68d56d40a4776` (2026-08-03), the delegated Knowledge
Request whose draft never appeared.

### A. The output contract never reaches the step that produces the output

`HookRunner._prepare_knowledge_request` builds a 1392-character instruction
that ends with the Library Draft contract: end the final response with exactly
one `<library_draft>` JSON marker and write nothing after it. The dispatcher
passes that text to `TeamRunOrchestrator.run_cycle`, which forwards it to
`TeamRunRuntime.add_work` — so the contract enters the run through
`ADD_WORK_PROMPT`, the prompt that turns an instruction into tasks. The leader
read it there: it created a task whose acceptance required a
`library-draft-marker-format-check`.

The cycle's final text, however, comes from `_leader_synthesis`. That prompt is
built from `_goal_context(run, cycle_id)` (`team_runtime.py:763`), which for a
cycle returns `TeamRunService.get_cycle_objective` — the short stored
`team_cycle_requests.instruction`, in this case
`"Prepare the delegated Knowledge Request as a Library review draft."`. The
long prepared text is persisted separately as the cycle's
`effective_instruction` and is read back only by the add-work and recovery
paths.

So at the moment the cycle's final response is written, the contract is absent
and `SYNTHESIS_PROMPT` (`team_runtime.py:127`) asks for the opposite:

> Return either: 1. A concise plain-text summary of what was accomplished,
> including any failures.

`HookRunner._settle_knowledge_request` then parses that plain-text summary
looking for the marker. No obedient team can pass. The contract is treated as
work instructions to fan out, never as a contract on the cycle's own output.

### B. Acceptance verifies shape, not substance

`TeamAcceptanceService.evaluate` (`team_acceptance.py:43`) checks three things:
the declared deliverable paths equal the required output names, each declared
file exists and is safe, and for each required verification name the worker
reported an entry with `status == "passed"`.

Verification names are free-form strings the leader invents at planning time
(`PLANNING_PROMPT` asks for `"required_verifications":["verification-name"]`).
The server cannot know what a name means, so it cannot check it. The
`evidence` field is free text and is never read by anything.

On the observed cycle the final task required
`library-draft-marker-format-check` and was accepted with evidence reading
`"파일 본문 기준 단일 JSON Library Draft 마커, 필수 필드, so…"`. Grepping the
four deliverables the run produced:

```
d3-6week-curriculum-library-review-draft-synthesis.md    markers=0
d3-v7-6week-curriculum-library-review-draft.md           markers=0
library-draft-week3-d3-vs-chart-library-criteria.md      markers=0
qa-review-d3-curriculum-completion-criteria.md           markers=0
```

The evidence was false and the gate passed it. Any rule expressed as a
verification name is unenforced by construction.

## Scope

In scope:

- make the cycle's output contract explicit and deliver it to synthesis;
- have the server validate the synthesized output against that contract and
  re-request once on violation;
- give acceptance a typed check vocabulary the server runs itself;
- keep unverifiable verifications expressible, but record them as attested
  rather than verified.

Out of scope:

- shell or script execution as a check type;
- a plugin mechanism for custom checks;
- re-verifying already-completed runs;
- automatic retry beyond the single synthesis re-request (task-level retry
  already exists through acceptance recovery).

## Part A: The cycle output contract

### Preparation

`CyclePreparer` currently returns `str | None`. It returns
`CyclePreparation | None` instead:

```python
@dataclass(frozen=True)
class CyclePreparation:
    instruction: str
    output_contract_id: str | None = None
```

`HookRunner.prepare_team_cycle` returns
`CyclePreparation(instruction=<prepared text>, output_contract_id="library_draft")`
for a `knowledge_request` source, and `CyclePreparation(instruction=...)` with
no contract for a `hook` source. `TeamCycleDispatcher` keeps using
`preparation.instruction` where it uses the string today, and stores
`output_contract_id` next to the effective instruction.

### Storage

`TeamRunService.set_cycle_effective_instruction` gains an
`output_contract_id: str | None` parameter and writes it into the same
`execution_metadata_json` `semantic_source` object that already holds
`effective_instruction`. A matching `get_cycle_output_contract_id(cycle_id)`
reads it back. No migration: the column and the JSON object already exist.

### Contract registry

One module-level registry keyed by contract id:

```python
@dataclass(frozen=True)
class OutputContract:
    id: str
    instructions: str
    validate: Callable[[str], None]   # raises ValueError on violation
```

The single initial entry is `library_draft`, whose `instructions` is
`library_draft_output_contract()` and whose `validate` calls
`parse_library_draft_response`. An unknown id behaves as no contract.

### Synthesis

`_leader_synthesis` looks up the cycle's contract. With no contract the prompt
is unchanged. With a contract, `SYNTHESIS_PROMPT`'s option 1 — the "concise
plain-text summary" — is replaced by the contract's instructions, so the prompt
no longer asks for two contradictory things. Option 2, the `ask_user`
resolution, stays: a contract must not prevent the leader from surfacing a
decision only the user can make.

The result is validated before it is returned:

- validation passes, or the result is a `UserDecisionResolution` → return it
  unchanged;
- validation fails → send one corrective follow-up naming the violation and
  requiring the contract-shaped output only, then validate again;
- the second result is returned whether or not it validates. Settlement records
  `draft_contract_violation` and the Archive request card shows the failure
  banner, exactly as it does today.

The corrective follow-up is a `cycle_synthesis_repair` operation, a new stage
that mirrors the existing `cycle_planning_repair` — the same shape the system
already uses when the leader's output was rejected and must be asked for again.
A separate stage rather than a second `cycle_synthesis` ordinal, because the
synthesis ordinal is derived from resolved user-decision requests and must keep
that meaning.

## Part B: Typed acceptance checks

### Schema

`required_verifications` accepts objects as well as the plain strings it takes
today:

```json
"required_verifications": [
  {"name": "marker-format",
   "check": {"type": "file_contains", "path": "draft.md", "value": "<library_draft>"}},
  "source-url-verification"
]
```

A plain string is equivalent to `{"name": "<string>"}` — a verification with no
check, i.e. attested. Stored `acceptance_json` from before this change parses
unchanged, so in-flight runs keep working.

`TaskAcceptance.required_verifications` becomes a tuple of
`RequiredVerification(name: str, check: VerificationCheck | None)`. The name
stays unique within a task.

`api/team_runs.py` serializes the new shape: each required verification becomes
`{"name": ..., "check": {...} | null}` instead of a bare string, so the UI can
tell a checked requirement from an attested one.

### Check vocabulary

Four file-based types. No process execution.

| type | fields | passes when |
| --- | --- | --- |
| `file_nonempty` | `path` | the file has non-whitespace content |
| `file_contains` | `path`, `value` | `value` occurs in the file text |
| `file_matches` | `path`, `pattern` | the regex matches somewhere in the file text |
| `json_parses` | `path` | the file text parses as JSON |

`path` is validated the same way a deliverable path is: relative, no `..`, no
symlink component, resolving inside the workspace. A check whose file is
missing fails rather than erroring.

### Evaluation

In `TeamAcceptanceService.evaluate`, after the existing deliverable checks:

- a verification with a `check` is decided by the server. The worker's reported
  `status` for that name is ignored entirely — reporting `passed` for a check
  the server fails does not change the outcome, and the worker may omit the
  entry.
- a verification without a check keeps today's rule: the worker must report it
  with `status == "passed"`.
- any failure yields the existing `required_verification_failed` reason, which
  is already in `RECOVERABLE_ACCEPTANCE_REASONS`, so the leader's acceptance
  review and worker retry path applies unchanged.

The evidence recorded on success gains a per-verification mode:

```json
"verifications": {
  "marker-format": {"mode": "verified", "status": "passed", "evidence": "file_contains matched"},
  "source-url-verification": {"mode": "attested", "status": "passed", "evidence": "<worker text>"}
}
```

and a task-level `"attested_only": true` when the task has no server-run check.

### Prompts

`PLANNING_PROMPT` and `ACCEPTANCE_REVIEW_PROMPT` document the object form and
the four check types, and instruct the leader to express a verification as a
check whenever it can be decided from a file, using a bare name only for
something that genuinely cannot be. `WORKER_PROMPT` is unchanged: the worker
still reports its own verifications, and the server overrides the ones it can
decide.

### Safety

File reads are capped at 1 MB; a larger file fails the check rather than being
read. A `pattern` that fails to compile fails the check. Checks never execute
anything and never read outside the workspace.

## UI

`TeamRunDetail` shows an `ATTESTED` badge on a task whose acceptance result
carries `attested_only`, so a completed task with no machine-checked evidence
is visibly different from one with it. Nothing blocks on the badge — an
attested-only task still completes.

## Error Handling

A check that raises for an unexpected reason (OS error, decode error) fails the
verification with its reason recorded; it never propagates out of `evaluate`
and never breaks the run. Contract validation failures in synthesis are handled
by the re-request path above and never raise past `_leader_synthesis`.

## Testing

Test-driven, in this order:

- `tests/test_team_acceptance.py` — each of the four check types passing and
  failing; a server-run check failing while the worker reported `passed`; the
  plain-string back-compat form; attested-only marking; a missing file, an
  oversized file, and an invalid regex each failing rather than raising.
- `tests/test_teams.py` — acceptance JSON round-trips both forms; duplicate
  names still rejected.
- `tests/test_team_runtime.py` — synthesis with a contract uses the contract
  instructions instead of the plain-summary option; a violating first response
  triggers exactly one corrective follow-up; a second violation is returned
  as-is; a cycle with no contract is unchanged.
- `tests/test_hook_runner.py` — `prepare_team_cycle` returns
  `output_contract_id="library_draft"` for a knowledge request and none for a
  hook; end-to-end, a leader that emits the marker produces a draft.
- `frontend/.../TeamRunDetail.test.jsx` — the `ATTESTED` badge renders for an
  attested-only task and not otherwise.
