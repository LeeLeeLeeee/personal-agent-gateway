# Activate Typed Acceptance Checks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the leader actually emit server-runnable checks, and make the difference between a verified and a merely attested task visible on screen.

**Architecture:** Part B1 built the machinery and shipped it deliberately inert — the vocabulary, the runner, the schema, the server-side evaluation and the API shape all exist, but nothing teaches the leader to use them and nothing renders them. This plan turns it on: three prompts learn the check vocabulary, and `TeamRunDetail` renders each verification's check plus an `ATTESTED` badge for a task the server never machine-checked. It also closes one diagnostic defect B1 parked.

**Tech Stack:** Python 3.12, FastAPI, pytest; React 18 + Vite + Vitest + Testing Library.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-03-team-output-contract-enforcement-design.md`, Part B. Part B1 (`docs/superpowers/plans/2026-08-03-typed-acceptance-checks.md`) is merged; this plan is its activation half.
- Backend interpreter and commands run from the repo root (or the worktree root, if one is in use):
  - Test: `.venv/Scripts/python.exe -m pytest tests/<file> -v`
- Frontend commands run from `frontend/`: `npm test -- <path>`, `npm run build`. If `node_modules` is missing in your worktree, run `npm install` there first.
- The repository is NOT clean at baseline. On main, `pytest -q` is roughly **32 failed / ~1261 passed / 2 skipped**; the failures live in `tests/test_runtime_factory_headless.py`, `tests/test_team_cycle_recovery.py`, `tests/test_api_agents.py`, `tests/test_api_dashboard.py`, and some flake between runs. `ruff check .` reports **227 pre-existing findings**. Judge by the delta, never by absolute green. Do not run the whole suite while iterating; do not run `ruff check .` — lint only the files you touch. The frontend suite, by contrast, must be fully green: 340 passed across 40 files on main.
- The four check types are exactly `file_nonempty`, `file_contains`, `file_matches`, `json_parses`. Do not add a fifth. A check's `path` is workspace-relative; `file_contains` takes `value`, `file_matches` takes `pattern` (max 200 characters), the other two take neither.
- Attested verifications stay legal and stay accepted. The point is to make the leader prefer a check when one can decide the question, not to forbid prose verifications.
- Two serialization shapes coexist and must not be merged: **canonical** (DB and ledger; a check-less verification collapses to a bare string) and **explicit** (API and run-result package; always `{"name","check"}`). The UI consumes the explicit form.
- Korean Conventional Commit subjects.

---

### Task 1: The prompts learn the check vocabulary

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py` — `PLANNING_PROMPT` (~line 87), `ACCEPTANCE_REVIEW_PROMPT` (~line 130), `ADD_WORK_PROMPT` (~line 186)
- Test: `tests/test_team_runtime.py`

**Interfaces:**
- Consumes: nothing new — the shapes were already accepted by `parse_required_verifications` in B1.
- Produces: no code interface. The three prompts document the object form and instruct the leader to prefer a check when a file can decide the question.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_team_runtime.py`:

```python
def test_planning_prompts_teach_the_check_vocabulary() -> None:
    for prompt in (PLANNING_PROMPT, ADD_WORK_PROMPT, ACCEPTANCE_REVIEW_PROMPT):
        assert "file_nonempty" in prompt
        assert "file_contains" in prompt
        assert "file_matches" in prompt
        assert "json_parses" in prompt
        assert '"check"' in prompt
```

Import the three constants from `personal_agent_gateway.team_runtime`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_team_runtime.py -k check_vocabulary -v`
Expected: FAIL — none of the three prompts mentions a check type today

- [ ] **Step 3: Write the implementation**

In `PLANNING_PROMPT`, replace the acceptance line

```
   "required_verifications":["verification-name"]}}}}
```

with the object form and the guidance:

```
   "required_verifications":[{{"name":"verification-name","check":null}}]}}}}
   A verification may carry a check the server runs itself. Prefer one whenever a
   file can decide the question; use "check":null only for something no file can
   settle. Available checks, each with a workspace-relative "path":
   {{"type":"file_nonempty","path":"p"}}
   {{"type":"file_contains","path":"p","value":"substring"}}
   {{"type":"file_matches","path":"p","pattern":"regex, at most 200 characters"}}
   {{"type":"json_parses","path":"p"}}
   A check you supply decides the outcome; your own claim about it is ignored.
```

Apply the same replacement to `ADD_WORK_PROMPT`'s acceptance line (~186) and to `ACCEPTANCE_REVIEW_PROMPT`'s `revise_acceptance` shape (~130). For the acceptance-review prompt keep its single-line JSON style — put the check guidance on following lines rather than inlining it into the JSON example.

The last sentence matters more than it looks: without it a leader that writes a check will still self-report it as passed and may then argue with the server's verdict during acceptance review.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_team_runtime.py -v`
Expected: PASS. Existing prompt-content assertions elsewhere in that file may pin the old acceptance line — update them to the new text, and say in your report which ones you touched.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "feat: 계획·검토 프롬프트에 검증 체크 어휘 도입"
```

---

### Task 2: `TeamRunDetail` shows the check and the attested badge

**Files:**
- Modify: `frontend/src/components/organisms/TeamRunDetail/index.jsx` (the verifications list at ~line 212-230, and the task row/dialog header where the badge goes)
- Modify: `src/personal_agent_gateway/static/styles.css`
- Test: `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`

**Interfaces:**
- Consumes: the explicit API form `{"name", "check"}` and `task.acceptance_result.evidence` carrying `verifications[name].mode` (`"verified"` / `"attested"`) plus `attested_only`.
- Produces: no code interface.

- [ ] **Step 1: Write the failing tests**

Add to `TeamRunDetail.test.jsx`, alongside the existing fixtures (which already carry an explicit unchecked verification, an explicit checked one, and a bare string):

```jsx
  it("shows what a server-run check verified", async () => {
    // render a task whose acceptance carries
    //   {name: "marker", check: {type: "file_contains", path: "draft.md", value: "<library_draft>"}}
    // and whose acceptance_result.evidence.verifications.marker.mode === "verified"
    expect(screen.getByText(/file_contains/)).toBeInTheDocument();
    expect(screen.getByText(/draft\.md/)).toBeInTheDocument();
    expect(screen.getByText("VERIFIED")).toBeInTheDocument();
  });

  it("marks a task nothing machine-checked as attested", async () => {
    // same task but every verification check:null and evidence.attested_only === true
    expect(screen.getByText("ATTESTED")).toBeInTheDocument();
  });

  it("does not mark an attested badge on a task with a server-run check", async () => {
    expect(screen.queryByText("ATTESTED")).not.toBeInTheDocument();
  });
```

Fill each body from the file's existing setup — it already renders the task dialog from a fixture and opens it; copy that. Write them concretely; do not commit the comments above as the whole test.

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npm test -- src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`
Expected: FAIL — the check object is not rendered and no badge exists

- [ ] **Step 3: Write the implementation**

3a. In the verifications list, render the check and the mode. Keep the defensive string read that B1 added — the string shape is still legal and still arrives from older stored rows:

```jsx
                  {acceptance.required_verifications.map((item) => {
                    const name = typeof item === "string" ? item : item.name;
                    const check = typeof item === "string" ? null : item.check;
                    const verification = verificationByName.get(name);
                    const mode = verificationModes[name];
                    return (
                      <li key={name}>
                        <span className="mono">{name}</span>
                        {" · "}
                        <span className="mono">
                          {String(verification?.status || "missing").toUpperCase()}
                        </span>
                        {mode ? (
                          <>
                            {" · "}
                            <span className="mono">{mode.toUpperCase()}</span>
                          </>
                        ) : null}
                        {check ? (
                          <div className="mono team-task-check">
                            {`${check.type} · ${check.path}`}
                            {check.value ? ` · ${check.value}` : ""}
                            {check.pattern ? ` · ${check.pattern}` : ""}
                          </div>
                        ) : null}
                        {verification?.evidence ? ` · ${verification.evidence}` : ""}
                      </li>
                    );
                  })}
```

`verificationModes` comes from the acceptance result, read next to the existing `acceptanceResult`:

```jsx
  const verificationModes = Object.fromEntries(
    Object.entries(acceptanceResult.evidence?.verifications || {})
      .map(([name, item]) => [name, item?.mode])
      .filter(([, mode]) => Boolean(mode))
  );
```

3b. Add the badge where the dialog already shows the task's status, so it reads as a property of the task rather than of one verification:

```jsx
              {acceptanceResult.evidence?.attested_only ? (
                <span className="mono team-task-attested">ATTESTED</span>
              ) : null}
```

3c. Add the two CSS rules in `src/personal_agent_gateway/static/styles.css`, next to the other `team-task-` rules:

```css
.team-task-check {
    color: var(--c-grey);
    font-size: 8px;
    letter-spacing: 0.7px;
}
.team-task-attested {
    border: var(--bd-sm);
    border-color: var(--c-warn);
    padding: 2px 5px;
    color: var(--c-warn);
    font-size: 8px;
    letter-spacing: 0.8px;
}
```

Check the neighbouring `team-task-` rules before writing these and match their conventions; if `--bd-sm` is not what the neighbours use for a small badge border, use whatever they use.

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npm test` then `npm run build`
Expected: PASS for the whole frontend suite, and a successful build.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/organisms/TeamRunDetail/index.jsx \
  frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx \
  src/personal_agent_gateway/static/styles.css
git commit -m "feat: Team Run 상세에 검증 체크와 미검증 배지 표시"
```

---

### Task 3: A rejection that never ran the checks must not blame them

**Files:**
- Modify: `src/personal_agent_gateway/team_acceptance.py` (`rejected_verification_names`)
- Test: `tests/test_team_acceptance.py`, `tests/test_team_model_effects.py`

**Interfaces:**
- Consumes: the rejection evidence B1 added.
- Produces: no new interface; `rejected_verification_names` stops reporting a checked verification the server never evaluated.

**Why this task exists:** B1's final review found and parked it. When a task is rejected before the verification loop runs — `task_not_completed`, `required_output_missing`, `undeclared_deliverable`, `unsafe_deliverable` — the rejection evidence has no `verifications` key, so every checked verification falls through to "not passed" and is listed in `rejected_verifications`. The leader's recovery history then blames checks the server never ran. Both comparison sides compute it identically, so replay digests are unaffected; this is diagnostic accuracy only, which is why it was parked rather than blocking B1.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_team_acceptance.py`:

```python
def test_a_deliverable_rejection_does_not_blame_unevaluated_checks(tmp_path: Path) -> None:
    workspace = _workspace_with_report(tmp_path, "prose\n<library_draft>{}</library_draft>")
    task = _task(verifications=(RequiredVerification("marker", _marker_check()),))

    result = TeamAcceptanceService().evaluate(
        task, _outcome(deliverables=()), workspace
    )

    assert result.accepted is False
    assert result.reason_code == "required_output_missing"
    assert rejected_verification_names(task, result) == []
```

Import `rejected_verification_names` from `personal_agent_gateway.team_acceptance`. If its current signature differs from `(task, result)`, match the real one — read it first — and keep the assertion's meaning: nothing is blamed.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_team_acceptance.py -k blame -v`
Expected: FAIL — `["marker"]` is returned because the evidence carries no `verifications` key

- [ ] **Step 3: Write the implementation**

In `rejected_verification_names`, treat a missing `verifications` map as "the checks were never evaluated" rather than as "they all failed": when the rejection evidence has no `verifications` key at all, report only the attested verifications the worker failed to report as passed, and no checked ones. When the map is present, keep today's behaviour exactly — a checked verification is blamed if and only if the server recorded it as failed.

Read the function before changing it and keep both call sites' inputs identical, since the producer and the comparator must stay in step.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_team_acceptance.py tests/test_team_model_effects.py tests/test_team_runtime.py -v`
Expected: PASS. `tests/test_team_model_effects.py` holds the replay tests that would catch a producer/comparator divergence.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/team_acceptance.py tests/
git commit -m "fix: 검증을 실행하지 않은 반려가 체크를 탓하지 않도록 수정"
```

---

### Task 4: Full verification

- [ ] **Step 1: Run the backend suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: no new failures against the baseline in Global Constraints. Report the counts and name any failure outside the four known files — those are yours to fix.

- [ ] **Step 2: Run the frontend suite and build**

Run (from `frontend/`): `npm test` then `npm run build`
Expected: fully green, and a successful build.

- [ ] **Step 3: Lint the changed files**

Run ruff over the backend files you changed; confirm no new findings and that `ruff check .` still reports 227.

- [ ] **Step 4: Sanity-check the activation by hand**

The point of this plan is that a leader now emits checks. That cannot be asserted in a unit test, so read the three prompts once as a whole and confirm each one, on its own, gives a leader everything needed to write a valid check: the four type names, the field each type takes, the `path` being workspace-relative, the 200-character pattern cap, and that a supplied check decides the outcome. Say in your report what you found — this step is a read, not an edit.

- [ ] **Step 5: Commit any fixes**

Skip if nothing needed fixing.
