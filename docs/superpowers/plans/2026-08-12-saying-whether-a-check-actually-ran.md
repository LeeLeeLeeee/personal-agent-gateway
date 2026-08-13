# Saying Whether A Check Actually Ran Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a worker report that it could not confirm a verification, instead of being forced to pick `passed` or `failed`, and make that admission visible rather than silently accepted.

**Architecture:** One new field on the worker's per-verification report — `checked` — separated from `status`. The parser accepts the new shape and reads the old one as `checked: true`. The gate stops treating a `checked: false` report as satisfying a required verification, records it as unverified instead of rejecting the task, and the existing build-evidence view surfaces it per task and in the run rollup.

**Tech Stack:** Python 3.13, FastAPI, SQLite, pytest; React (Vite) with Vitest.

**Spec:** `docs/superpowers/specs/2026-08-12-saying-whether-a-check-actually-ran-design.md`

## Global Constraints

- Execution stays with the worker. This plan adds **no** check kind that compiles, runs a command, or spawns a process, and does not touch `ShellRunner` or `run_verification_check`. The gate remains a reader.
- An unchecked required verification means the task is **accepted** and recorded as unverified. Refusing it would kill every run in an environment without the dependencies installed, which is the environment the motivating run was in.
- A required verification the worker **omits entirely** must still fail the task exactly as it does today. The new field must not turn a missing report into a tolerated one.
- `checked: true` with a null `status` is a malformed outcome and must be rejected — that combination is a worker trying to have it both ways.
- Frontend copy in this area is Korean. A gate-run check renders `파일 내용 확인`, a worker-asserted one `워커 신고`; the new state renders `미확인`. Never render any of them as `검증됨`.
- Run tests with `PYTHONPATH=src python -m pytest ... -q -p no:randomly` from the repo root. Quote any `-k` expression containing spaces — unquoted, pytest runs zero tests and reports success.
- Lint with `python -m ruff check <files>` using the project's ruff (`python -m ruff`, 0.15.20). A `.venv/Scripts/ruff.exe` 0.16.0 also exists in this checkout and reports ten pre-existing findings on these files; it is not the project's linter.
- Backend baseline is **21 pre-existing failures** in `tests/test_api_agents.py` (4), `tests/test_api_dashboard.py` (1), `tests/test_runtime_factory_headless.py` (16). Judge by delta.
- Full backend suite takes about 15 minutes. Run it in the **foreground** once, at the end. Per-task runs use the files the task touches.

---

## Task 1: The report shape

The dataclass and parser first, so the shape is settled before anything reads it.

**Files:**
- Modify: `src/personal_agent_gateway/team_outcomes.py` — `VerificationStatus` (line 9), `VerificationEvidence` (line 19), `_parse_verifications` (line 105)
- Test: `tests/test_team_outcomes.py`

**Interfaces:**
- Produces: `VerificationEvidence(name: str, status: VerificationStatus | None, evidence: str, checked: bool)`. `VerificationStatus` stays `Literal["passed", "failed"]`; `status` becomes optional and is `None` exactly when `checked` is `False`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_team_outcomes.py`. Read the top of that file for how it builds an outcome payload and which helper parses it — the parser entry point is `parse_task_outcome`, and these tests should go through it rather than calling `_parse_verifications` directly, so the shape is pinned where callers actually enter.

```python
def test_a_worker_can_report_that_it_could_not_check():
    """The motivating run's worker ran `npx --no-install tsc --version`, could not
    use it, and had nowhere to say so -- the schema allowed only passed or failed,
    so it wrote the fact into a Markdown file nothing reads."""
    outcome = parse_task_outcome(
        json.dumps(
            {
                "status": "completed",
                "summary": "wrote the screens",
                "reason_code": None,
                "deliverables": [{"path": "a.tsx", "kind": "file"}],
                "verifications": [
                    {
                        "name": "frontend-typechecks",
                        "checked": False,
                        "status": None,
                        "evidence": "npx --no-install tsc: typescript-unavailable",
                    }
                ],
            }
        )
    )

    (verification,) = outcome.verifications
    assert verification.checked is False
    assert verification.status is None
    assert "typescript-unavailable" in verification.evidence


def test_a_checked_verification_still_carries_its_result():
    outcome = parse_task_outcome(
        json.dumps(
            {
                "status": "completed",
                "summary": "s",
                "reason_code": None,
                "deliverables": [],
                "verifications": [
                    {
                        "name": "pytest",
                        "checked": True,
                        "status": "passed",
                        "evidence": "42 passed",
                    }
                ],
            }
        )
    )

    (verification,) = outcome.verifications
    assert verification.checked is True
    assert verification.status == "passed"


def test_an_old_shape_report_reads_as_checked():
    """Stored outcomes predate this field. At the time, a bare status *was* the
    worker's claim to have checked, so reading it as checked=True preserves the
    meaning and avoids a migration."""
    outcome = parse_task_outcome(
        json.dumps(
            {
                "status": "completed",
                "summary": "s",
                "reason_code": None,
                "deliverables": [],
                "verifications": [
                    {"name": "pytest", "status": "passed", "evidence": "42 passed"}
                ],
            }
        )
    )

    (verification,) = outcome.verifications
    assert verification.checked is True
    assert verification.status == "passed"


@pytest.mark.parametrize(
    "verification",
    [
        # checked with no result: trying to have it both ways.
        {"name": "n", "checked": True, "status": None, "evidence": "e"},
        # not checked, but claiming a result anyway.
        {"name": "n", "checked": False, "status": "passed", "evidence": "e"},
        # a status that is not one of the two allowed values.
        {"name": "n", "checked": True, "status": "skipped", "evidence": "e"},
        # checked is not a boolean.
        {"name": "n", "checked": "yes", "status": "passed", "evidence": "e"},
        # an unrelated extra key.
        {"name": "n", "checked": True, "status": "passed", "evidence": "e", "x": 1},
    ],
)
def test_incoherent_verification_reports_are_rejected(verification):
    with pytest.raises(TaskOutcomeError):
        parse_task_outcome(
            json.dumps(
                {
                    "status": "completed",
                    "summary": "s",
                    "reason_code": None,
                    "deliverables": [],
                    "verifications": [verification],
                }
            )
        )
```

Add `TaskOutcomeError` and `parse_task_outcome` to that file's imports if they are not already there, and `json`/`pytest` likewise.

- [ ] **Step 2: Run the tests and watch them fail**

Run: `PYTHONPATH=src python -m pytest tests/test_team_outcomes.py -q -p no:randomly -k "could_not_check or still_carries or old_shape or incoherent"`
Expected: FAIL — the first four raise `TaskOutcomeError`, because the parser pins the key set to exactly `{"name", "status", "evidence"}` and rejects `checked` outright.

- [ ] **Step 3: Widen the dataclass**

```python
@dataclass(frozen=True)
class VerificationEvidence:
    name: str
    status: VerificationStatus | None
    evidence: str
    checked: bool = True
```

`checked` defaults to `True` so the many existing constructions in tests and
fixtures keep meaning what they meant. Leave `VerificationStatus` as
`Literal["passed", "failed"]` — the third state is the absence of a status, not a
third status value.

- [ ] **Step 4: Teach the parser both shapes**

Replace the key-set check and the field validation in `_parse_verifications`. It
currently pins the key set exactly, so accept two combinations rather than
loosening it to a subset check:

```python
        if not isinstance(raw, dict) or set(raw) not in (
            {"name", "status", "evidence"},
            {"name", "checked", "status", "evidence"},
        ):
            raise TaskOutcomeError()
        name = raw["name"]
        status = raw["status"]
        evidence = raw["evidence"]
        checked = raw.get("checked", True)
        if not isinstance(checked, bool):
            raise TaskOutcomeError()
        # checked and status carry different facts, so the two have to agree:
        # a check that ran has a result, and one that did not cannot have one.
        if checked and status not in {"passed", "failed"}:
            raise TaskOutcomeError()
        if not checked and status is not None:
            raise TaskOutcomeError()
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(evidence, str)
            or not evidence.strip()
        ):
            raise TaskOutcomeError()
```

and pass `checked` through to the constructor:

```python
        verifications.append(
            VerificationEvidence(
                normalized_name, status, evidence.strip(), checked=checked
            )
        )
```

Note that `evidence` stays required. A worker that could not check something has
the most to explain, and the motivating run shows it will explain if asked.

- [ ] **Step 5: Run the tests and watch them pass**

Run: `PYTHONPATH=src python -m pytest tests/test_team_outcomes.py -q -p no:randomly`
Expected: all pass. If an existing test in this file constructs
`VerificationEvidence` positionally with three arguments it still works, because
`checked` defaults.

- [ ] **Step 6: Lint and commit**

Run: `python -m ruff check src/personal_agent_gateway/team_outcomes.py tests/test_team_outcomes.py`
Expected: `All checks passed!`

```bash
git add src/personal_agent_gateway/team_outcomes.py tests/test_team_outcomes.py
git commit -m "feat(team-runs): let a verification report say it was not checked"
```

---

## Task 2: The gate records it instead of trusting it

**Files:**
- Modify: `src/personal_agent_gateway/team_acceptance.py` — the verification loop at lines 105-131, and `rejected_verification_names`'s signature at line 166
- Test: `tests/test_team_acceptance.py`

**Interfaces:**
- Consumes: `VerificationEvidence.checked` from Task 1.
- Produces: `AcceptanceResult.evidence` gains `"unverified": [names]`, and a recorded verification's `mode` may now be `"unverified"` alongside the existing `"verified"` and `"attested"`.

- [ ] **Step 1: Write the failing tests**

`tests/test_team_acceptance.py` has `_task(outputs=..., verifications=...)` at line
59 and `_outcome(deliverables=..., verifications=...)` at line 83 — read both and
use them rather than building a `TeamTask` by hand.

```python
def test_an_unchecked_required_verification_is_accepted_and_recorded(tmp_path: Path) -> None:
    """The whole point: the task is not refused, but the admission survives.
    Refusing would kill every run in an environment without the dependencies --
    which is the environment run 699c1915 was in -- and the goal is to stop
    unverified work passing *silently*."""
    workspace = tmp_path / "workspace"
    output = workspace / "outputs" / "report.md"
    output.parent.mkdir(parents=True)
    output.write_text("report", encoding="utf-8")
    task = _task(verifications=(RequiredVerification("frontend-typechecks"),))
    outcome = _outcome(
        verifications=(
            VerificationEvidence(
                "frontend-typechecks",
                None,
                "npx --no-install tsc: typescript-unavailable",
                checked=False,
            ),
        )
    )

    result = TeamAcceptanceService().evaluate(task, outcome, workspace)

    assert result.accepted
    assert result.evidence["unverified"] == ["frontend-typechecks"]
    assert result.evidence["verifications"]["frontend-typechecks"]["mode"] == "unverified"


def test_an_unchecked_verification_is_never_recorded_as_passed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    output = workspace / "outputs" / "report.md"
    output.parent.mkdir(parents=True)
    output.write_text("report", encoding="utf-8")
    task = _task(verifications=(RequiredVerification("frontend-typechecks"),))
    outcome = _outcome(
        verifications=(
            VerificationEvidence("frontend-typechecks", None, "unavailable", checked=False),
        )
    )

    recorded = TeamAcceptanceService().evaluate(task, outcome, workspace).evidence

    assert recorded["verifications"]["frontend-typechecks"]["status"] != "passed"


def test_a_missing_verification_still_fails_the_task(tmp_path: Path) -> None:
    """The new field must not turn an omitted report into a tolerated one."""
    workspace = tmp_path / "workspace"
    output = workspace / "outputs" / "report.md"
    output.parent.mkdir(parents=True)
    output.write_text("report", encoding="utf-8")
    task = _task(verifications=(RequiredVerification("frontend-typechecks"),))
    outcome = _outcome(verifications=())

    result = TeamAcceptanceService().evaluate(task, outcome, workspace)

    assert not result.accepted
    assert result.reason_code == "required_verification_failed"


def test_a_reported_failure_still_fails_the_task(tmp_path: Path) -> None:
    """checked=True with status failed is a real negative result, not an excuse."""
    workspace = tmp_path / "workspace"
    output = workspace / "outputs" / "report.md"
    output.parent.mkdir(parents=True)
    output.write_text("report", encoding="utf-8")
    task = _task(verifications=(RequiredVerification("frontend-typechecks"),))
    outcome = _outcome(
        verifications=(
            VerificationEvidence("frontend-typechecks", "failed", "3 errors", checked=True),
        )
    )

    result = TeamAcceptanceService().evaluate(task, outcome, workspace)

    assert not result.accepted
    assert result.reason_code == "required_verification_failed"


def test_a_gate_run_check_ignores_the_worker_claim_entirely(tmp_path: Path) -> None:
    """A required verification carrying a check is decided by the gate. A worker
    saying checked=False must not turn that into unverified."""
    workspace = tmp_path / "workspace"
    output = workspace / "outputs" / "report.md"
    output.parent.mkdir(parents=True)
    output.write_text("report", encoding="utf-8")
    task = _task(
        verifications=(
            RequiredVerification(
                "report-written",
                check=VerificationCheck(type="file_contains", path="outputs/report.md", value="report"),
            ),
        )
    )
    outcome = _outcome(
        verifications=(
            VerificationEvidence("report-written", None, "did not run it", checked=False),
        )
    )

    result = TeamAcceptanceService().evaluate(task, outcome, workspace)

    assert result.accepted
    assert result.evidence["verifications"]["report-written"]["mode"] == "verified"
    assert result.evidence["unverified"] == []
```

`VerificationCheck`'s constructor arguments may differ from the keywords above —
read `src/personal_agent_gateway/team_verification_checks.py` and use its real
signature. `_DEFAULT_REQUIRED_VERIFICATIONS` in the test file shows how a
check-less one is built.

- [ ] **Step 2: Run the tests and watch them fail**

Run: `PYTHONPATH=src python -m pytest tests/test_team_acceptance.py -q -p no:randomly -k "unchecked or missing_verification or reported_failure or gate_run_check"`
Expected: the first two FAIL — today `reported.status != "passed"` rejects the task, so it is not accepted and no `unverified` key exists. The last three should already pass; if one fails, stop and say so, because the current behaviour is not what this plan assumes.

- [ ] **Step 3: Split the worker-reported branch**

In the loop, the check-less branch currently rejects anything whose status is not
`passed`. Separate "did not check" from "checked and failed":

```python
            if reported is None:
                return _rejected(
                    "failed",
                    "required_verification_failed",
                    evidence={"verifications": recorded},
                )
            if not reported.checked:
                # Accepted, but recorded rather than counted. The worker told us
                # it could not confirm this; the run should carry that forward
                # instead of reading it as a pass.
                unverified.append(required.name)
                recorded[required.name] = {
                    "mode": "unverified",
                    "status": "unknown",
                    "evidence": reported.evidence,
                }
                continue
            if reported.status != "passed":
                return _rejected(
                    "failed",
                    "required_verification_failed",
                    evidence={"verifications": recorded},
                )
            recorded[required.name] = {
                "mode": "attested",
                "status": reported.status,
                "evidence": reported.evidence,
            }
```

Initialise `unverified: list[str] = []` beside `recorded`, and add it to the
accepted result's evidence:

```python
            evidence={
                "deliverables": sorted(declared),
                "verifications": recorded,
                "attested_only": verified_count == 0,
                "unverified": unverified,
            },
```

Leave `attested_only` alone. It means "no required verification had a runnable
check", which is still true and still what the existing rollup reads.

- [ ] **Step 4: Let a null status through `rejected_verification_names`**

Not in the spec's checklist — found while planning. Three callers build
`{item.name: item.status for item in outcome.verifications}` and pass it here
(`team_runtime.py:2948`, `team_model_effects.py:652` and `:3147`). Its parameter
is annotated `dict[str, str]` and it decides a check-less verification with
`verification_status.get(name) != "passed"`. A `None` status makes that true, so
an unchecked verification gets named in `rejected_verifications`.

**Keep that behaviour** — it is accurate. The list means "required verifications
not confirmed passed", and an unchecked one is exactly that. The review only runs
when the task failed for some other reason, so this adds a name to an existing
rejection rather than creating one. Only the annotation is now a lie:

```python
    verification_status: dict[str, str | None],
```

Pin the behaviour so a later reader does not "fix" it into silence:

```python
def test_an_unchecked_verification_counts_as_not_confirmed_passed() -> None:
    """This list is what the worker is told to go fix. A verification nobody
    confirmed belongs on it -- the review it appears in was triggered by some
    other failure, so this adds a name rather than causing a rejection."""
    names = rejected_verification_names(
        [("typecheck", False)], {"typecheck": None}, {"verifications": {}}
    )

    assert names == ["typecheck"]
```

`rejected_verification_names` is already imported in this test file.

- [ ] **Step 5: Run the tests and watch them pass**

Run: `PYTHONPATH=src python -m pytest tests/test_team_acceptance.py -q -p no:randomly`
Expected: all pass.

- [ ] **Step 6: Lint and commit**

Run: `python -m ruff check src/personal_agent_gateway/team_acceptance.py tests/test_team_acceptance.py`
Expected: `All checks passed!`

```bash
git add src/personal_agent_gateway/team_acceptance.py tests/test_team_acceptance.py
git commit -m "feat(team-runs): record an unchecked verification instead of reading it as a pass"
```

---

## Task 3: Ask the worker for it

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py` — `WORKER_PROMPT` at line 121
- Test: `tests/test_team_runtime.py`

- [ ] **Step 1: Write the failing test**

```python
def test_the_worker_prompt_asks_whether_each_check_actually_ran() -> None:
    """A field the worker is never told about will not get used. The motivating
    run's worker had the fact and wrote it into a Markdown file instead."""
    assert '"checked"' in WORKER_PROMPT
    lowered = WORKER_PROMPT.lower()
    assert "could not" in lowered or "not run" in lowered
    # It must not offer a third status value -- the third state is a null status.
    assert "skipped" not in lowered
    assert "unavailable" not in lowered
```

`WORKER_PROMPT` is already imported in that file's `team_runtime` import block;
confirm it before adding a duplicate.

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly -k "prompt_asks_whether"`
Expected: FAIL — `"checked"` does not appear in the prompt.

- [ ] **Step 3: Change the schema the prompt shows**

The prompt currently ends with the output schema. Change the verifications entry
and add one sentence. Mind that this is a plain (non-f) string with `{{` `}}`
doubling for `.format()`:

```
{{"status":"completed|blocked|failed","summary":"concise result",
"reason_code":"stable-code or null","deliverables":[{{"path":"relative/path",
"kind":"file kind"}}],"verifications":[{{"name":"verification name",
"checked":true,"status":"passed|failed","evidence":"concrete evidence"}}]}}
Set "checked":false with "status":null when you could not actually confirm a
verification -- a tool that is missing, a command that failed to run, a check you
had no way to perform -- and say why in "evidence". Do not report a status you did
not observe.
```

Then confirm the module still imports, since a stray single brace breaks
`.format()` at call time rather than at import:

Run: `PYTHONPATH=src python -c "from personal_agent_gateway.team_runtime import WORKER_PROMPT; print(WORKER_PROMPT.format(persona_snapshot_json='{}', goal='g', task_title='t', task_description='d')[-200:])"`
Expected: it prints the tail of the formatted prompt without raising.

- [ ] **Step 4: Run the runtime suite**

Run: `PYTHONPATH=src python -m pytest tests/test_team_runtime.py -q -p no:randomly`
Expected: all pass. A test elsewhere may pin the prompt's exact text; extend its
expected string rather than reverting the change, and say in your report which
test it was.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "feat(team-runs): ask the worker whether each verification actually ran"
```

---

## Task 4: Show it

**Files:**
- Modify: `src/personal_agent_gateway/team_build_evidence.py` — `task_build_evidence` and `run_build_evidence`
- Modify: `frontend/src/components/organisms/TeamRunDetail/BuildEvidence.jsx`
- Test: `tests/test_team_build_evidence.py`, `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`

**Interfaces:**
- Consumes: `acceptance_result["evidence"]["unverified"]` and a recorded verification whose `mode` is `"unverified"`, from Task 2.
- Produces: `task_build_evidence` gains `"unverified": [names]`; `run_build_evidence` gains `"unverified_task_count": int`.

- [ ] **Step 1: Write the failing backend test**

`tests/test_team_build_evidence.py` has a `_task(tmp_path, **overrides)` helper and
`_fill_team_task_defaults` — read both and use them.

```python
def test_evidence_reports_what_the_worker_could_not_check(tmp_path):
    """The label carries the distinction the gate now records: a check the gate ran,
    a check the worker asserted, and a check nobody confirmed."""
    (tmp_path / "promised.md").write_text("x", encoding="utf-8")
    task = _task(
        tmp_path,
        acceptance_result={
            "evidence": {
                "verifications": {
                    "ran": {"mode": "verified", "status": "passed"},
                    "typecheck": {"mode": "unverified", "status": "unknown"},
                },
                "attested_only": False,
                "unverified": ["typecheck"],
            }
        },
    )

    evidence = task_build_evidence(task, tmp_path)

    assert evidence["unverified"] == ["typecheck"]
    assert {"name": "typecheck", "mode": "unverified", "status": "unknown"} in (
        evidence["verifications"]
    )


def test_the_rollup_counts_tasks_with_something_unconfirmed(tmp_path):
    """This is the number that moves when work goes unchecked. attested_only does
    not: it is true only when a task had zero runnable checks, so it reads 0 for a
    run where every check was a file read."""
    (tmp_path / "promised.md").write_text("x", encoding="utf-8")
    clean = _task(tmp_path, id="t1")
    unconfirmed = _task(
        tmp_path,
        id="t2",
        acceptance_result={
            "evidence": {"verifications": {}, "attested_only": False, "unverified": ["typecheck"]}
        },
    )

    # run_build_evidence takes the already-computed per-task reports, not the
    # tasks, and takes no workspace -- recomputing here doubled the filesystem
    # work on a polled endpoint.
    rollup = run_build_evidence(
        [task_build_evidence(task, tmp_path) for task in (clean, unconfirmed)]
    )

    assert rollup["unverified_task_count"] == 1


def test_an_acceptance_result_without_the_key_reports_none(tmp_path):
    """Every stored acceptance result predates this key."""
    (tmp_path / "promised.md").write_text("x", encoding="utf-8")
    task = _task(tmp_path)

    assert task_build_evidence(task, tmp_path)["unverified"] == []
```

- [ ] **Step 2: Run and watch them fail**

Run: `PYTHONPATH=src python -m pytest tests/test_team_build_evidence.py -q -p no:randomly -k "could_not_check or rollup_counts_tasks or without_the_key"`
Expected: FAIL — `KeyError: 'unverified'`.

- [ ] **Step 3: Carry it through**

In `task_build_evidence`, read the list next to the existing evidence reads:

```python
        "unverified": sorted(
            str(name) for name in (evidence.get("unverified") or [])
        ),
```

In `run_build_evidence`, count tasks that have any:

```python
        "unverified_task_count": sum(1 for item in per_task if item["unverified"]),
```

- [ ] **Step 4: Extend the two exact-dict assertions**

A new rollup key breaks every test that compares the whole dict. Two do — find
them before running, because both fail for the same harmless reason:

- `tests/test_team_build_evidence.py`, `test_run_rollup_counts_what_rests_on_the_workers_word` — `assert rollup == {"task_count": 3, ...}`
- `tests/test_api_team_runs.py:2559` — `assert detail["build_evidence_summary"] == {...}`

Add `"unverified_task_count": 0` to both expected dicts. Zero is correct in
both: neither fixture's acceptance result carries the new key. Extend the
expectations — do not weaken them to subset checks, since naming every key is
what caught the mapper dropping fields before.

- [ ] **Step 5: Run and watch them pass**

Run: `PYTHONPATH=src python -m pytest tests/test_team_build_evidence.py tests/test_api_team_runs.py -q -p no:randomly`
Expected: all pass.

- [ ] **Step 6: Write the failing frontend test**

Add it to the `describe` block in `TeamRunDetail.test.jsx` that holds
`carries build evidence and contests from a /detail response into the view`.
Note the prop is `buildEvidenceSummary` — `api.teamRunDetail` maps the
snake_case body key to camelCase, and it forwards the whole object
(`body?.build_evidence_summary || null`), so the new count rides through the
mapper with no change there. Per-task `build_evidence` stays snake_case,
because tasks pass through wholesale.

```jsx
  it("labels a verification nobody confirmed", async () => {
    render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "G", status: "completed", run_mode: "plan_and_execute" },
          agents: [], messages: [],
          buildEvidenceSummary: {
            task_count: 2, worker_asserted_only_count: 0,
            missing_file_count: 0, unverified_task_count: 1
          },
          tasks: [{
            id: "t1", title: "Build the screens", status: "completed",
            build_evidence: {
              promised: [], declared: [], undeclared_promises: [],
              extra_declarations: [], missing_files: [],
              verifications: [
                { name: "ran", mode: "verified", status: "passed" },
                { name: "typecheck", mode: "unverified", status: "unknown" }
              ],
              unverified: ["typecheck"], worker_asserted_only: false
            }
          }]
        }}
      />
    );

    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));
    expect(screen.getByText(/미확인 1/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Open task Build the screens" }));
    const dialog = screen.getByRole("dialog", { name: "Task details: Build the screens" });
    expect(within(dialog).getByText(/미확인/)).toBeInTheDocument();
    expect(within(dialog).getByText(/파일 내용 확인/)).toBeInTheDocument();
    expect(within(dialog).queryByText("검증됨")).not.toBeInTheDocument();
  });
```

Run: `npm --prefix frontend test -- TeamRunDetail`
Expected: FAIL — `미확인` is not rendered.

- [ ] **Step 7: Render it**

In `BuildEvidence.jsx`, add the mode to the label map and the count to the summary
line. The map currently holds `verified` and `attested`:

```jsx
const MODE_LABEL = { verified: "파일 내용 확인", attested: "워커 신고", unverified: "미확인" };
```

and in `BuildEvidenceSummary`, append to the existing line:

```jsx
      {` · 미확인 ${summary.unverified_task_count ?? 0}`}
```

The `?? 0` matters: a detail payload from before this change has no such key, and
the panel must not render `undefined`.

- [ ] **Step 8: Run the frontend suite**

Run: `npm --prefix frontend test`
Expected: 41 files pass. Up to 2 `ArchiveView` timeout flakes are pre-existing; re-run once to confirm a failure is one of those before treating it as yours.

- [ ] **Step 9: Commit**

```bash
git add src/personal_agent_gateway/team_build_evidence.py \
        tests/test_team_build_evidence.py tests/test_api_team_runs.py \
        frontend/src/components/organisms/TeamRunDetail/
git commit -m "feat(team-runs): show which verifications nobody confirmed"
```

---

## Task 5: Verify and finish

- [ ] **Step 1: Full backend suite, foreground**

Run: `PYTHONPATH=src python -m pytest -q -p no:randomly`
Expected: **21 failed**, in `tests/test_api_agents.py` (4), `tests/test_api_dashboard.py` (1), `tests/test_runtime_factory_headless.py` (16), with passes up by this plan's new tests. Report the failure list, not just the count.

- [ ] **Step 2: Lint**

Run: `python -m ruff check src/personal_agent_gateway/ tests/`
Expected: `All checks passed!`

- [ ] **Step 3: Prove the old shape against real stored data**

The compatibility claim is that stored outcomes read as `checked: true`. Assert it
against payloads actually in the database rather than ones you wrote. Run
`699c1915` — the run this whole design came from — has 11 such rows:

```bash
PYTHONPATH=src python -c "
import sqlite3
from personal_agent_gateway.team_outcomes import parse_task_outcome
c = sqlite3.connect('data/app.sqlite')
rows = c.execute(\"select outcome_json from team_tasks where team_run_id like '699c1915%' and outcome_json like '%verifications%'\").fetchall()
print(len(rows), 'rows')
for (payload,) in rows:
    for v in parse_task_outcome(payload).verifications:
        assert v.checked is True and v.status in {'passed', 'failed'}, (v.name, v.checked, v.status)
print('all parse as checked=True with their original status')
"
```

Expected: `11 rows` and the confirmation line. Record the output. The stored
payload is a plain JSON object with `deliverables`/`verifications` keys, which is
what `parse_task_outcome` takes; if it raises instead, the compatibility claim is
wrong and this plan is broken — stop and report, do not adjust the assertion.

- [ ] **Step 4: Live check**

Restart so the Python changes and the rebuilt bundle both load:

```bash
npm run stop && npm start
```

Then confirm run `699c1915fa764be598586d2f8bb3a170`'s detail payload still reports
its four promised-versus-declared mismatches and now carries
`unverified_task_count: 0` — that run's stored acceptance results have no
`unverified` key, so zero is the correct reading and not a wiring failure. Say
plainly what you could not arrange: producing a real `checked: false` needs a live
worker in an environment missing a tool, which is not worth forcing.

- [ ] **Step 5: Record and finish**

Append what you observed to the spec's Verification section, commit that file
alone, then use `superpowers:finishing-a-development-branch`.

```bash
git add docs/superpowers/specs/2026-08-12-saying-whether-a-check-actually-ran-design.md
git commit -m "docs(team-runs): record what the unchecked-verification path showed"
```

---

## Deliberately not in this plan

- **Any check kind that executes.** Execution stays with the worker; the gate remains a reader. Adding it would mean a new boundary inside the API process, where `evaluate()` is called synchronously from `async` code and `ShellRunner` has no timeout.
- **Making the leader ask for verification worth having.** The motivating run's contract asked for `admin-router-registered` and two like it, all satisfied by a file containing a string; the type check was never a contract item. This plan lets a worker be honest and makes that honesty visible — it does not make the leader demand more.
- **Detecting a false `checked: true`.** A worker can still claim it checked what it did not. Catching that needs the command and exit code as re-runnable evidence, or the gate running the check, and both are pointless while contracts only ask for file reads.
