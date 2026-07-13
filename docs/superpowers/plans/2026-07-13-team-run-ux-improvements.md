# Team Run UX Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix agent avatars, prevent leader/member overlap, let users add work to an in-flight or finished team run, show run progress more clearly, and surface what agents share as identifiable documents.

**Architecture:** Backend changes live in `teams.py` (snapshot + backfill), `team_runtime.py` (drain loop, race-safe synthesize, `resume`, `add_work`), and `api/team_runs.py` (`add-work` endpoint). Frontend changes live in `TeamRunForm` (leader≠member), `TeamRunDetail` (progress, shared documents, add-work input), the API client, and `GatewayApp` (handler wiring). Styles go in the React app's active stylesheet, `static/styles.css`.

**Tech Stack:** Python 3.13 + FastAPI + SQLite (`Database`), pytest / pytest-asyncio; React 18 (JSX, not TSX) + Vite + Vitest + Testing Library.

## Global Constraints

- Backend package import root is `personal_agent_gateway` (under `src/`). Run tests from the repo root `personal-agent-gateway/`.
- The React app is served from `frontend/` and imports its stylesheet from `../../src/personal_agent_gateway/static/styles.css` (see `frontend/src/main.jsx:5`). That file **is** the active stylesheet — edit it for CSS. The rest of `src/personal_agent_gateway/static/` (legacy HTML/JS UI) must not be edited.
- Frontend components are `.jsx`. Match the existing Atomic-Design layout under `frontend/src/components/**`.
- Timestamps come from `personal_agent_gateway.teams._now()` — never call `datetime.now` directly in new team code; reuse `_now`.
- CSS palette variables: `--c-black`, `--c-white`, `--c-bg #e8e8e8`, `--c-panel #f0f0f0`, `--c-grey`, `--c-dark`, `--c-warn`, `--c-ok`, `--c-danger`; borders `--bd` (3px), `--bd-sm` (2px), `--bd-in` (1px). Brutalist light theme — match it.
- `add-work` / `resume` are supported for `run_mode == "plan_and_execute"` runs only. Other modes return HTTP 409.
- Persona snapshots freeze at run start; `avatar` is presentation-only and is the one field allowed to be backfilled into existing snapshots.

---

## File Structure

- `src/personal_agent_gateway/teams.py` — add `avatar` to `_persona_snapshot`; add `TeamRunService.backfill_agent_avatars()`.
- `src/personal_agent_gateway/team_runtime.py` — drain loop in `_execute`; `_execute_and_synthesize` (replaces `_synthesize`); `resume`; `add_work`; `ADD_WORK_PROMPT`.
- `src/personal_agent_gateway/api/team_runs.py` — `AddWorkRequest` + `POST /{id}/add-work`.
- `src/personal_agent_gateway/app.py` — one-line backfill call at startup.
- `frontend/src/api/client.js` — `addWork(id, instruction)`.
- `frontend/src/components/organisms/TeamRunForm/index.jsx` — leader≠member.
- `frontend/src/components/organisms/TeamRunDetail/index.jsx` — phase stepper, richer lanes, colored activity, shared documents panel, add-work input.
- `frontend/src/components/containers/GatewayApp/index.jsx` — `handleAddWork` + prop wiring.
- `src/personal_agent_gateway/static/styles.css` — styles for stepper, lane states, activity kinds, documents/handoffs, add-work.
- Tests: `tests/test_teams.py`, `tests/test_team_runtime.py`, `tests/test_api_team_runs.py`, and the co-located `*.test.jsx` files.

---

## Task 1: Avatar in persona snapshot (backend)

**Files:**
- Modify: `src/personal_agent_gateway/teams.py:391-401` (`_persona_snapshot`)
- Test: `tests/test_teams.py`

**Interfaces:**
- Produces: `_persona_snapshot(persona)` now returns a dict that includes key `"avatar"` (str). Consumed by the FE via `agent.persona_snapshot.avatar`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_teams.py`:

```python
def test_persona_snapshot_includes_avatar(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService

    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [], avatar="person01")
    run = teams.create_team_run("goal", leader.id, [], "planning_only", 1)

    agent = teams.list_agents(run.id)[0]
    assert agent.persona_snapshot["avatar"] == "person01"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_teams.py::test_persona_snapshot_includes_avatar -v`
Expected: FAIL with `KeyError: 'avatar'`.

- [ ] **Step 3: Add the field**

In `src/personal_agent_gateway/teams.py`, edit `_persona_snapshot` to add the `avatar` key:

```python
def _persona_snapshot(persona: Persona) -> dict[str, object]:
    return {
        "id": persona.id,
        "name": persona.name,
        "role": persona.role,
        "description": persona.description,
        "responsibilities": persona.responsibilities,
        "constraints": persona.constraints,
        "default_backend": persona.default_backend,
        "default_model": persona.default_model,
        "avatar": persona.avatar,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_teams.py::test_persona_snapshot_includes_avatar -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/teams.py tests/test_teams.py
git commit -m "fix: include avatar in team agent persona snapshot"
```

---

## Task 2: Backfill avatar into existing agent snapshots (backend)

**Files:**
- Modify: `src/personal_agent_gateway/teams.py` (new method on `TeamRunService`)
- Modify: `src/personal_agent_gateway/app.py:75-79` (startup call)
- Test: `tests/test_teams.py`

**Interfaces:**
- Produces: `TeamRunService.backfill_agent_avatars() -> int` — for each `team_agents` row whose stored snapshot lacks `"avatar"` and whose `persona_id` still resolves, injects that persona's `avatar`. Returns the number of rows updated. Idempotent.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_teams.py`:

```python
def test_backfill_agent_avatars_populates_missing(tmp_path):
    import json
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService

    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [], avatar="tech03")
    run = teams.create_team_run("goal", leader.id, [], "planning_only", 1)
    agent = teams.list_agents(run.id)[0]

    # Simulate a legacy snapshot with no avatar key.
    snapshot = dict(agent.persona_snapshot)
    snapshot.pop("avatar", None)
    db.execute(
        "update team_agents set persona_snapshot_json = ? where id = ?",
        (json.dumps(snapshot, ensure_ascii=False, sort_keys=True), agent.id),
    )
    assert "avatar" not in teams.list_agents(run.id)[0].persona_snapshot

    updated = teams.backfill_agent_avatars()

    assert updated == 1
    assert teams.list_agents(run.id)[0].persona_snapshot["avatar"] == "tech03"
    # Idempotent: a second pass changes nothing.
    assert teams.backfill_agent_avatars() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_teams.py::test_backfill_agent_avatars_populates_missing -v`
Expected: FAIL with `AttributeError: 'TeamRunService' object has no attribute 'backfill_agent_avatars'`.

- [ ] **Step 3: Implement the method**

In `src/personal_agent_gateway/teams.py`, add this method to `TeamRunService` (place it after `set_run_status`, before `_create_agent`):

```python
    def backfill_agent_avatars(self) -> int:
        updated = 0
        for row in self._db.fetchall(
            "select id, persona_id, persona_snapshot_json from team_agents"
        ):
            snapshot = json.loads(row["persona_snapshot_json"])
            if "avatar" in snapshot:
                continue
            try:
                persona = self._personas.get_persona(row["persona_id"])
            except KeyError:
                continue
            snapshot["avatar"] = persona.avatar
            self._db.execute(
                "update team_agents set persona_snapshot_json = ?, updated_at = ? where id = ?",
                (json.dumps(snapshot, ensure_ascii=False, sort_keys=True), _now(), row["id"]),
            )
            updated += 1
        return updated
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_teams.py::test_backfill_agent_avatars_populates_missing -v`
Expected: PASS.

- [ ] **Step 5: Call the backfill at startup**

In `src/personal_agent_gateway/app.py`, right after the `app.state.team_runtime = TeamRuntime(...)` block (currently ending at line 79), add:

```python
    app.state.team_run_service.backfill_agent_avatars()
```

- [ ] **Step 6: Verify the full backend suite still passes**

Run: `python -m pytest tests/test_teams.py tests/test_app_team_factory.py -v`
Expected: PASS (no regressions from the startup call).

- [ ] **Step 7: Commit**

```bash
git add src/personal_agent_gateway/teams.py src/personal_agent_gateway/app.py tests/test_teams.py
git commit -m "feat: backfill avatar into existing team agent snapshots at startup"
```

---

## Task 3: Leader cannot also be a member (frontend)

**Files:**
- Modify: `frontend/src/components/organisms/TeamRunForm/index.jsx:27-126`
- Test: `frontend/src/components/organisms/TeamRunForm/TeamRunForm.test.jsx`

**Interfaces:**
- Consumes: existing `personas` prop and `onSubmit` callback (unchanged signature).
- Produces: the member button for the current leader renders `disabled` with a `LEADER` role label; selecting a new leader auto-removes it from `memberPersonaIds`.

- [ ] **Step 1: Write the failing tests**

Add to `frontend/src/components/organisms/TeamRunForm/TeamRunForm.test.jsx` inside the `describe`:

```javascript
  it("disables the current leader in the member list", async () => {
    render(<TeamRunForm personas={personas} onSubmit={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /select tech lead as leader/i }));

    const leaderAsMember = screen.getByRole("button", { name: /tech lead is the leader/i });
    expect(leaderAsMember).toBeDisabled();
  });

  it("deselects a member when it becomes the leader", async () => {
    const onSubmit = vi.fn();
    render(<TeamRunForm personas={personas} onSubmit={onSubmit} />);

    await userEvent.click(screen.getByRole("button", { name: /toggle qa tester as member/i }));
    // Promote QA Tester to leader; it must drop out of the member set.
    await userEvent.click(screen.getByRole("button", { name: /select qa tester as leader/i }));
    await userEvent.click(screen.getByRole("button", { name: /start team run/i }));

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
      leader_persona_id: "p2",
      member_persona_ids: []
    }));
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run src/components/organisms/TeamRunForm/TeamRunForm.test.jsx`
Expected: FAIL — no button labelled "tech lead is the leader"; the deselect test submits `member_persona_ids: ["p2"]`.

- [ ] **Step 3: Add the auto-deselect effect**

In `frontend/src/components/organisms/TeamRunForm/index.jsx`, after the existing leader-default `useEffect` (ends at line 36), add:

```javascript
  useEffect(() => {
    setMemberPersonaIds((prev) => prev.filter((id) => id !== leaderPersonaId));
  }, [leaderPersonaId]);
```

- [ ] **Step 4: Render the leader as a disabled member**

Replace the member `personas.map(...)` block (lines 103-124) with:

```javascript
            {personas.map((persona) => {
              const isLeader = persona.id === leaderPersonaId;
              const active = memberPersonaIds.includes(persona.id);
              return (
                <button
                  key={persona.id}
                  type="button"
                  disabled={isLeader}
                  aria-pressed={active}
                  aria-label={isLeader ? `${persona.name} is the leader` : `Toggle ${persona.name} as member`}
                  className={`team-run-member${active ? " active" : ""}${isLeader ? " is-leader" : ""}`}
                  onClick={() => { if (!isLeader) toggleMember(persona.id); }}
                >
                  <span className="team-run-member-top">
                    <span className="team-run-check">{isLeader ? "" : active ? "✓" : ""}</span>
                    <PersonaMark persona={persona} />
                    <span className="team-run-member-title">
                      <span className="team-run-member-name">{persona.name}</span>
                      <span className="team-run-member-role">{isLeader ? "LEADER" : persona.role || "—"}</span>
                    </span>
                  </span>
                </button>
              );
            })}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `npx vitest run src/components/organisms/TeamRunForm/TeamRunForm.test.jsx`
Expected: PASS (all five tests, including the three pre-existing ones).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/organisms/TeamRunForm/index.jsx frontend/src/components/organisms/TeamRunForm/TeamRunForm.test.jsx
git commit -m "feat: prevent the leader persona from also being selected as a member"
```

---

## Task 4: Drain loop in `_execute` (backend runtime)

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py:125-145` (`_execute`)
- Test: `tests/test_team_runtime.py`

**Interfaces:**
- Consumes: `TeamRunService.list_tasks`, `set_task_status`, `set_agent_status`, `append_message` (existing).
- Produces: `_execute(run, leader, workers)` re-queries pending tasks every iteration and assigns them round-robin until none remain, so tasks created mid-execution are picked up.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_team_runtime.py`:

```python
@pytest.mark.asyncio
async def test_execute_drains_task_added_during_execution(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.model_client import ModelResponse
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService

    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)

    plan = '[{"title":"T1","description":"d1"}]'
    state = {"injected": False}
    models = {}

    def factory(agent):
        if agent.role == "leader":
            if agent.id not in models:
                models[agent.id] = ScriptedModel([plan, "summary"])
            return models[agent.id]

        class WorkerModel:
            async def complete(self, messages):
                if not state["injected"]:
                    state["injected"] = True
                    teams.create_task(run.id, "T2", "d2")
                return ModelResponse(content="did it", tool_calls=[])

        return WorkerModel()

    runtime = TeamRuntime(teams=teams, model_factory=factory)
    result = await runtime.start(run.id)

    assert result.status == "completed"
    assert {t.title: t.status for t in teams.list_tasks(run.id)} == {
        "T1": "completed",
        "T2": "completed",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_team_runtime.py::test_execute_drains_task_added_during_execution -v`
Expected: FAIL — `T2` stays `pending` because the current `_execute` reads a one-shot snapshot.

- [ ] **Step 3: Rewrite `_execute` as a drain loop**

Replace `_execute` (lines 125-145) in `src/personal_agent_gateway/team_runtime.py` with:

```python
    async def _execute(self, run: TeamRun, leader: TeamAgent, workers: list[TeamAgent]) -> None:
        counter = 0
        while True:
            pending = [task for task in self._teams.list_tasks(run.id) if task.status == "pending"]
            if not pending:
                return
            task = pending[0]
            worker = workers[counter % len(workers)]
            counter += 1
            self._teams.set_task_status(task.id, "in_progress")
            self._teams.set_agent_status(worker.id, "running")
            try:
                result = await self._run_task(run, leader, worker, task)
                self._teams.append_message(
                    run.id, worker.id, None, "agent_output", result, {"task_id": task.id}
                )
                self._teams.set_task_status(task.id, "completed", result=result)
                self._teams.set_agent_status(worker.id, "completed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._teams.set_task_status(task.id, "failed", error_message=str(exc))
                self._teams.set_agent_status(worker.id, "failed")
            await self._publish(
                {"type": "team.task.updated", "team_run_id": run.id, "task_id": task.id}
            )
```

- [ ] **Step 4: Run the new test and the existing runtime suite**

Run: `python -m pytest tests/test_team_runtime.py -v`
Expected: PASS — the new test plus all pre-existing runtime tests (drain loop is behavior-compatible for the single-pass case).

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "feat: drain pending tasks in team execute loop to absorb mid-run additions"
```

---

## Task 5: Race-safe synthesize loop + `resume` (backend runtime)

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py` — `start` (lines 59-96), replace `_synthesize` (lines 225-238) with `_execute_and_synthesize`, add `resume`.
- Test: `tests/test_team_runtime.py`

**Interfaces:**
- Produces:
  - `_execute_and_synthesize(run, leader, workers) -> TeamRun` — drains, synthesizes, and if new `pending` tasks appeared during synthesis, loops back to execute before finalizing.
  - `resume(team_run_id: str) -> TeamRun` — sets a terminal run back to `running`, publishes `team.run.reopened`, then runs `_execute_and_synthesize`.
- Consumes: `_execute` (Task 4), `_leader_synthesis`, `_terminal_status`, `_find_leader`, `_find_workers`, `_settle_canceled` (existing).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_team_runtime.py`:

```python
@pytest.mark.asyncio
async def test_task_added_during_synthesis_is_executed_before_terminal(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.model_client import ModelResponse
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService

    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)

    plan = '[{"title":"T1","description":"d1"}]'
    models = {}

    def factory(agent):
        if agent.role == "leader":
            if agent.id not in models:
                class LeaderModel:
                    def __init__(self): self.calls = 0
                    async def complete(self, messages):
                        self.calls += 1
                        if self.calls == 1:
                            return ModelResponse(content=plan, tool_calls=[])
                        if self.calls == 2:
                            # First synthesis pass: user work lands mid-synthesis.
                            teams.create_task(run.id, "T2", "d2")
                            return ModelResponse(content="interim", tool_calls=[])
                        return ModelResponse(content="final summary", tool_calls=[])
                models[agent.id] = LeaderModel()
            return models[agent.id]
        return FakeModel("worker done")

    runtime = TeamRuntime(teams=teams, model_factory=factory)
    result = await runtime.start(run.id)

    assert result.status == "completed"
    assert result.summary == "final summary"
    assert {t.title: t.status for t in teams.list_tasks(run.id)} == {
        "T1": "completed",
        "T2": "completed",
    }


@pytest.mark.asyncio
async def test_resume_runs_added_tasks_on_terminal_run(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService

    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)

    plan = '[{"title":"T1","description":"d1"}]'
    runtime = TeamRuntime(
        teams=teams,
        model_factory=_factory_by_role([plan, "summary1", "summary2"], ["r1", "r2"]),
    )
    first = await runtime.start(run.id)
    assert first.status == "completed"

    # Simulate add-work having created a new pending task, then reopen.
    teams.create_task(run.id, "T2", "d2")
    resumed = await runtime.resume(run.id)

    assert resumed.status == "completed"
    assert {t.title: t.status for t in teams.list_tasks(run.id)} == {
        "T1": "completed",
        "T2": "completed",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_team_runtime.py::test_task_added_during_synthesis_is_executed_before_terminal tests/test_team_runtime.py::test_resume_runs_added_tasks_on_terminal_run -v`
Expected: FAIL — first test finalizes before running T2; second fails with `AttributeError: ... 'resume'`.

- [ ] **Step 3: Restructure `start` to call `_execute_and_synthesize`**

In `src/personal_agent_gateway/team_runtime.py`, replace the body of `start` from the `await self._execute(run, leader, workers)` / `return await self._synthesize(run, leader)` lines (85-86) with a single call. The final two statements of the `try` block become:

```python
            return await self._execute_and_synthesize(run, leader, workers)
```

(Everything above it in `start` — planning, the non-`plan_and_execute` early return, and the no-workers failure — stays unchanged.)

- [ ] **Step 4: Replace `_synthesize` with `_execute_and_synthesize`**

Delete the existing `_synthesize` method (lines 225-238) and add in its place:

```python
    async def _execute_and_synthesize(self, run: TeamRun, leader: TeamAgent, workers: list[TeamAgent]) -> TeamRun:
        while True:
            await self._execute(run, leader, workers)
            tasks = self._teams.list_tasks(run.id)
            status = _terminal_status(tasks)
            if status == "failed":
                run = self._teams.set_run_status(run.id, "failed", error_message="All tasks failed")
                self._teams.set_agent_status(leader.id, "failed")
                await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": "All tasks failed"})
                return run
            run = self._teams.set_run_status(run.id, "summarizing")
            summary = await self._leader_synthesis(run, leader, tasks)
            if any(task.status == "pending" for task in self._teams.list_tasks(run.id)):
                run = self._teams.set_run_status(run.id, "running")
                continue
            run = self._teams.set_run_status(run.id, status, summary=summary)
            self._teams.set_agent_status(leader.id, "completed")
            await self._publish({"type": "team.run.completed", "team_run_id": run.id})
            return run

    async def resume(self, team_run_id: str) -> TeamRun:
        run = self._teams.get_team_run(team_run_id)
        leader: TeamAgent | None = None
        try:
            leader = _find_leader(self._teams.list_agents(run.id))
            run = self._teams.set_run_status(run.id, "running")
            leader = self._teams.set_agent_status(leader.id, "running")
            await self._publish({"type": "team.run.reopened", "team_run_id": run.id})
            workers = _find_workers(self._teams.list_agents(run.id))
            if not workers:
                error = "resume has no worker agents"
                run = self._teams.set_run_status(run.id, "failed", error_message=error)
                self._teams.set_agent_status(leader.id, "failed")
                await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": error})
                return run
            return await self._execute_and_synthesize(run, leader, workers)
        except asyncio.CancelledError:
            if run is not None:
                self._settle_canceled(run)
            raise
        except Exception as exc:  # noqa: BLE001
            run = self._teams.set_run_status(run.id, "failed", error_message=str(exc))
            if leader is not None:
                self._teams.set_agent_status(leader.id, "failed")
            await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": str(exc)})
            return run
```

- [ ] **Step 5: Run the full runtime suite**

Run: `python -m pytest tests/test_team_runtime.py -v`
Expected: PASS — the two new tests plus every pre-existing test (`test_synthesis_summary_from_leader` still sees exactly one `synthesis` message for the single-pass case).

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "feat: race-safe synthesize loop and resume() for team runs"
```

---

## Task 6: `add_work` runtime method (leader decomposition)

**Files:**
- Modify: `src/personal_agent_gateway/team_runtime.py` — add `ADD_WORK_PROMPT` and `add_work`.
- Test: `tests/test_team_runtime.py`

**Interfaces:**
- Produces: `add_work(team_run_id: str, instruction: str) -> list[TeamTask]` — asks the leader (resuming its session) to decompose `instruction` into tasks, inserts them as `pending`, publishes `team.task.created` per task, appends a `plan_note` message, and returns the created tasks.
- Consumes: `_model_factory`, `_parse_task_plan`, `TeamRunService.create_task`, `set_agent_session`, `append_message`, `_find_leader` (existing).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_team_runtime.py`:

```python
@pytest.mark.asyncio
async def test_add_work_creates_pending_tasks_from_instruction(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService

    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)

    decomposition = '[{"title":"Extra A","description":"da"},{"title":"Extra B","description":"db"}]'
    runtime = TeamRuntime(teams=teams, model_factory=lambda _agent: FakeModel(decomposition))

    created = await runtime.add_work(run.id, "please also do A and B")

    assert [task.title for task in created] == ["Extra A", "Extra B"]
    assert {t.title: t.status for t in teams.list_tasks(run.id)} == {
        "Extra A": "pending",
        "Extra B": "pending",
    }
    assert any(m.kind == "plan_note" for m in teams.list_messages(run.id))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_team_runtime.py::test_add_work_creates_pending_tasks_from_instruction -v`
Expected: FAIL with `AttributeError: ... 'add_work'`.

- [ ] **Step 3: Add the prompt constant**

In `src/personal_agent_gateway/team_runtime.py`, after `MEDIATION_PROMPT` (ends at line 43), add:

```python
ADD_WORK_PROMPT = """You are the leader agent for a personal-agent-gateway Team Run.
The user is adding work to an in-flight run. Break the request into concrete tasks.
Return ONLY a JSON array of task objects. Each object must have "title" and "description".
Goal: {goal}
Existing tasks: {existing_titles}
User request: {instruction}"""
```

- [ ] **Step 4: Add the `add_work` method**

Add to the `TeamRuntime` class (place after `resume`):

```python
    async def add_work(self, team_run_id: str, instruction: str) -> list[TeamTask]:
        run = self._teams.get_team_run(team_run_id)
        leader = _find_leader(self._teams.list_agents(run.id))
        leader_agent = self._teams.get_agent(leader.id)
        model = self._model_factory(leader_agent)
        existing = ", ".join(task.title for task in self._teams.list_tasks(run.id)) or "(none)"
        prompt = ADD_WORK_PROMPT.format(goal=run.goal, existing_titles=existing, instruction=instruction)
        response = await model.complete([{"role": "user", "content": prompt}])
        if response.upstream_session_id:
            self._teams.set_agent_session(leader_agent.id, response.upstream_session_id)
        try:
            specs = _parse_task_plan(response.content)
        except ValueError:
            retry = await model.complete(
                [{"role": "user", "content": prompt + "\nReturn ONLY a JSON array. No prose, no code fences."}]
            )
            if retry.upstream_session_id:
                self._teams.set_agent_session(leader_agent.id, retry.upstream_session_id)
            specs = _parse_task_plan(retry.content)
        created: list[TeamTask] = []
        for spec in specs:
            task = self._teams.create_task(run.id, spec["title"], spec["description"])
            created.append(task)
            await self._publish({"type": "team.task.created", "team_run_id": run.id, "task_id": task.id})
        self._teams.append_message(
            run.id, leader.id, None, "plan_note", f"Added {len(created)} task(s) from user request.", {}
        )
        return created
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_team_runtime.py::test_add_work_creates_pending_tasks_from_instruction -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/personal_agent_gateway/team_runtime.py tests/test_team_runtime.py
git commit -m "feat: add_work runtime method to decompose user requests into tasks"
```

---

## Task 7: `add-work` API endpoint

**Files:**
- Modify: `src/personal_agent_gateway/api/team_runs.py` — add `AddWorkRequest` and `POST /{team_run_id}/add-work`.
- Test: `tests/test_api_team_runs.py`

**Interfaces:**
- Consumes: `runtime.add_work` (Task 6), `runtime.resume` (Task 5), `registry.is_running/register/finish` (existing).
- Produces: `POST /api/team-runs/{id}/add-work` body `{ "instruction": str }`. 404 unknown run; 409 if `run_mode != "plan_and_execute"` or status is `draft`. For active runs, decomposes and lets the live loop absorb the tasks. For terminal runs, decomposes then starts a background `resume`. Returns `{ "team_run": {...} }`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_api_team_runs.py`:

```python
def test_add_work_rejects_non_execute_mode(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Lead")
    run = client.post(
        "/api/team-runs",
        json={
            "goal": "g",
            "leader_persona_id": leader_id,
            "member_persona_ids": [],
            "run_mode": "planning_only",
            "max_workers": 1,
        },
    ).json()["team_run"]

    resp = client.post(f"/api/team-runs/{run['id']}/add-work", json={"instruction": "x"})
    assert resp.status_code == 409


def test_add_work_rejects_draft_run(tmp_path: Path) -> None:
    client = authenticated_client(tmp_path)
    leader_id = create_persona(client, "Lead")
    member_id = create_persona(client, "Worker")
    run = client.post(
        "/api/team-runs",
        json={
            "goal": "g",
            "leader_persona_id": leader_id,
            "member_persona_ids": [member_id],
            "run_mode": "plan_and_execute",
            "max_workers": 1,
        },
    ).json()["team_run"]

    resp = client.post(f"/api/team-runs/{run['id']}/add-work", json={"instruction": "x"})
    assert resp.status_code == 409  # draft: run not started yet


async def test_add_work_reopens_terminal_run(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path))
    gate = asyncio.Event()
    gate.set()  # never block
    _inject_gated_team_runtime(app, gate)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.cookies.set("agent_session", "test-session")
        leader_id = await _async_create_persona(client, "Lead")
        member_id = await _async_create_persona(client, "Worker")
        created = (
            await client.post(
                "/api/team-runs",
                json={
                    "goal": "g",
                    "leader_persona_id": leader_id,
                    "member_persona_ids": [member_id],
                    "run_mode": "plan_and_execute",
                    "max_workers": 1,
                },
            )
        ).json()["team_run"]
        run_id = created["id"]
        registry = app.state.team_run_registry

        await client.post(f"/api/team-runs/{run_id}/start")
        await _poll_until(lambda: not registry.is_running(run_id))
        before = len((await client.get(f"/api/team-runs/{run_id}/tasks")).json()["tasks"])

        resp = await client.post(f"/api/team-runs/{run_id}/add-work", json={"instruction": "also do Y"})
        assert resp.status_code == 200

        await _poll_until(lambda: not registry.is_running(run_id))
        after = (await client.get(f"/api/team-runs/{run_id}/tasks")).json()["tasks"]
        assert len(after) == before + 1
        final = (await client.get(f"/api/team-runs/{run_id}")).json()["team_run"]
        assert final["status"] in {"completed", "completed_with_failures"}
        assert all(task["status"] in {"completed", "failed"} for task in after)
```

Note: `GatedModel.content` defaults to `'[{"title": "T", "description": "D"}]'`, which parses as a plan during planning and add-work, and is harmless as a worker result / summary string. That single fixture drives the whole flow.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_api_team_runs.py::test_add_work_rejects_non_execute_mode tests/test_api_team_runs.py::test_add_work_rejects_draft_run tests/test_api_team_runs.py::test_add_work_reopens_terminal_run -v`
Expected: FAIL — endpoint returns 404/405 (route missing).

- [ ] **Step 3: Add the request model and endpoint**

In `src/personal_agent_gateway/api/team_runs.py`, add the request model after `CreateTeamRunRequest` (line 21):

```python
class AddWorkRequest(BaseModel):
    instruction: str
```

Add this endpoint after `start_team_run` (after line 83):

```python
@router.post("/{team_run_id}/add-work")
async def add_work(
    request: Request, team_run_id: str, payload: AddWorkRequest, _session: None = session_dependency
) -> dict[str, object]:
    service = request.app.state.team_run_service
    registry = request.app.state.team_run_registry
    runtime = request.app.state.team_runtime
    try:
        run = service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    if run.run_mode != "plan_and_execute":
        raise HTTPException(status_code=409, detail="Additional work is only supported for plan_and_execute runs")
    if run.status == "draft":
        raise HTTPException(status_code=409, detail="Start the run before adding work")

    await runtime.add_work(team_run_id, payload.instruction)

    if run.status in _TERMINAL and not registry.is_running(team_run_id):
        async def _resume_and_finish() -> None:
            try:
                await runtime.resume(team_run_id)
            finally:
                registry.finish(team_run_id)

        task = asyncio.create_task(_resume_and_finish())
        registry.register(team_run_id, task)

    return {"team_run": _team_run_payload(service.get_team_run(team_run_id))}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_api_team_runs.py -v`
Expected: PASS — the three new tests plus all pre-existing ones.

- [ ] **Step 5: Commit**

```bash
git add src/personal_agent_gateway/api/team_runs.py tests/test_api_team_runs.py
git commit -m "feat: POST /team-runs/{id}/add-work endpoint (drain when active, resume when terminal)"
```

---

## Task 8: Add-work frontend (client + handler + input)

**Files:**
- Modify: `frontend/src/api/client.js` — add `addWork`.
- Modify: `frontend/src/components/containers/GatewayApp/index.jsx` — add `handleAddWork`, pass to `TeamRunDetail`.
- Modify: `frontend/src/components/organisms/TeamRunDetail/index.jsx` — accept `onAddWork`, render the input.
- Test: `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`

**Interfaces:**
- Produces:
  - `api.addWork(id, instruction) -> Promise<object|null>` (POSTs the instruction).
  - `TeamRunDetail` new prop `onAddWork(instruction: string)`. When present, renders an "Add work" textarea + button; the button label is `재개하며 요청` when the run is terminal, else `추가 업무 요청`.
- Consumes: existing `Button` atom, `useState`.

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`:

```javascript
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

// ... inside the existing describe block:

  it("submits additional work through onAddWork", async () => {
    const onAddWork = vi.fn();
    render(
      <TeamRunDetail
        onAddWork={onAddWork}
        detail={{
          run: { id: "r1", goal: "Design", status: "running", run_mode: "plan_and_execute" },
          agents: [],
          tasks: [],
          messages: []
        }}
      />
    );

    await userEvent.type(screen.getByLabelText("Additional work"), "also write docs");
    await userEvent.click(screen.getByRole("button", { name: "추가 업무 요청" }));

    expect(onAddWork).toHaveBeenCalledWith("also write docs");
  });

  it("labels the add-work button for reopening a finished run", () => {
    render(
      <TeamRunDetail
        onAddWork={vi.fn()}
        detail={{
          run: { id: "r1", goal: "Design", status: "completed", run_mode: "plan_and_execute" },
          agents: [],
          tasks: [],
          messages: []
        }}
      />
    );
    expect(screen.getByRole("button", { name: "재개하며 요청" })).toBeInTheDocument();
  });
```

(`vi` is imported here; the top-level `import { describe, expect, it } from "vitest";` stays.)

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`
Expected: FAIL — no "Additional work" field / button.

- [ ] **Step 3: Add the client method**

In `frontend/src/api/client.js`, add after `startTeamRun` (line 221):

```javascript
  async addWork(id, instruction) {
    return jsonOrNull(await fetch(`/api/team-runs/${encodeURIComponent(id)}/add-work`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ instruction })
    }));
  },
```

- [ ] **Step 4: Update `TeamRunDetail` imports and signature**

In `frontend/src/components/organisms/TeamRunDetail/index.jsx`, change the top imports to add `useState` and `Button`:

```javascript
import { useState } from "react";
import { StatusBadge } from "../../atoms/StatusBadge/index.jsx";
import { Button } from "../../atoms/Button/index.jsx";
import { TeamTaskCard } from "../../molecules/TeamTaskCard/index.jsx";

const TEAM_TASK_COLUMNS = ["pending", "in_progress", "blocked", "completed", "failed"];
const TERMINAL_STATUSES = ["completed", "completed_with_failures", "failed", "canceled"];
```

Change the component signature and add local state at the top of the function body:

```javascript
export function TeamRunDetail({ detail, onAddWork }) {
  const [workInput, setWorkInput] = useState("");
  const run = detail?.run;

  if (!run) {
    return <div className="team-run-empty mono">No team run selected.</div>;
  }
```

- [ ] **Step 5: Render the add-work section**

In `frontend/src/components/organisms/TeamRunDetail/index.jsx`, insert this block just before the closing `</section>` (after the `team-activity-results` div, currently line 177):

```javascript
      {onAddWork ? (
        <div className="team-add-work">
          <div className="team-section-head">
            <span className="mono team-section-label">Add work</span>
            <span className="team-section-rule" />
          </div>
          <textarea
            className="team-add-work-input"
            aria-label="Additional work"
            value={workInput}
            onChange={(event) => setWorkInput(event.target.value)}
            placeholder="추가로 요청할 업무를 자연어로 적어주세요"
          />
          <Button
            variant="primary"
            disabled={!workInput.trim()}
            onClick={() => { onAddWork(workInput.trim()); setWorkInput(""); }}
          >
            {TERMINAL_STATUSES.includes(run.status) ? "재개하며 요청" : "추가 업무 요청"}
          </Button>
        </div>
      ) : null}
```

- [ ] **Step 6: Run the component test**

Run: `npx vitest run src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`
Expected: PASS (new tests + the two pre-existing render tests, which pass no `onAddWork` so the section is absent).

- [ ] **Step 7: Wire the handler in `GatewayApp`**

In `frontend/src/components/containers/GatewayApp/index.jsx`, add this handler next to `handleSelectTeamRun` (near line 811):

```javascript
  async function handleAddWork(instruction) {
    if (!selectedTeamRunId || !instruction.trim()) return;
    try {
      const result = await api.addWork(selectedTeamRunId, instruction.trim());
      if (!result) {
        toast("Failed to add work", "error");
        return;
      }
      setTeamRunDetail(await api.teamRunDetail(selectedTeamRunId));
      toast("추가 업무를 전달했습니다", "success");
    } catch (_error) {
      toast("Failed to add work", "error");
    }
  }
```

Pass it to the detail view — change the render at line 916 from `<TeamRunDetail detail={teamRunDetail} />` to:

```javascript
            <TeamRunDetail detail={teamRunDetail} onAddWork={handleAddWork} />
```

- [ ] **Step 8: Verify the GatewayApp suite still passes**

Run: `npx vitest run src/components/containers/GatewayApp/GatewayApp.test.jsx`
Expected: PASS (no regressions).

- [ ] **Step 9: Commit**

```bash
git add frontend/src/api/client.js frontend/src/components/organisms/TeamRunDetail/index.jsx frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx frontend/src/components/containers/GatewayApp/index.jsx
git commit -m "feat: add-work input in team run detail wired to the API"
```

---

## Task 9: Progress display — phase stepper, richer lanes, colored activity (frontend + CSS)

**Files:**
- Modify: `frontend/src/components/organisms/TeamRunDetail/index.jsx`
- Modify: `src/personal_agent_gateway/static/styles.css`
- Test: `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`

**Interfaces:**
- Produces: a phase stepper (`Planning → Executing → Summarizing → Done`) with `aria-current="step"` on the active phase; agent lanes carry `team-lane-<status>` and `team-lane-leader` classes; activity rows carry a `tl-kind-<kind>` class.

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`:

```javascript
  it("marks the current phase in the stepper", () => {
    render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "Design", status: "summarizing", run_mode: "plan_and_execute" },
          agents: [],
          tasks: [],
          messages: []
        }}
      />
    );
    expect(screen.getByText("Summarizing").closest(".team-phase")).toHaveAttribute("aria-current", "step");
    expect(screen.getByText("Planning").closest(".team-phase")).not.toHaveAttribute("aria-current");
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx -t "marks the current phase"`
Expected: FAIL — no `.team-phase` elements.

- [ ] **Step 3: Add the phase model and helper**

In `frontend/src/components/organisms/TeamRunDetail/index.jsx`, add near the other module-level constants (after `TERMINAL_STATUSES`):

```javascript
const RUN_PHASES = [
  { key: "planning", label: "Planning", statuses: ["planning"] },
  { key: "executing", label: "Executing", statuses: ["running"] },
  { key: "summarizing", label: "Summarizing", statuses: ["summarizing"] },
  { key: "done", label: "Done", statuses: ["completed", "completed_with_failures", "failed", "canceled"] }
];

function phaseIndex(status) {
  const index = RUN_PHASES.findIndex((phase) => phase.statuses.includes(status));
  return index < 0 ? 0 : index;
}
```

- [ ] **Step 4: Render the stepper**

Insert the stepper immediately after the `</header>` (currently line 45), before the `team-run-meta` div:

```javascript
      <div className="team-phase-stepper" aria-label="Run phase">
        {RUN_PHASES.map((phase, index) => {
          const activeIndex = phaseIndex(run.status);
          const isActive = index === activeIndex;
          const isDone = index < activeIndex;
          return (
            <div
              key={phase.key}
              className={`team-phase${isActive ? " active" : ""}${isDone ? " done" : ""}`}
              aria-current={isActive ? "step" : undefined}
            >
              <span className="team-phase-dot" />
              <span className="mono team-phase-label">{phase.label}</span>
            </div>
          );
        })}
      </div>
```

- [ ] **Step 5: Enrich the agent lane class**

Change the lane `<article>` opening tag (currently line 80) to:

```javascript
            <article className={`team-lane team-lane-${agent.status}${agent.role === "leader" ? " team-lane-leader" : ""}`} key={agent.id}>
```

- [ ] **Step 6: Tag activity rows by kind**

Change the timeline row `<div>` (currently line 140) to:

```javascript
                <div className={`tl-row tl-kind-${message.kind}`} key={message.id}>
```

- [ ] **Step 7: Add styles**

Append to `src/personal_agent_gateway/static/styles.css` (after the team block, e.g. before the `@media (max-width: 1100px)` rule at line 2721 — add inside the same file, order does not matter):

```css
.team-phase-stepper {
    display: flex;
    gap: 8px;
    margin: 14px 0 6px;
    flex-wrap: wrap;
}
.team-phase {
    display: flex;
    align-items: center;
    gap: 6px;
    border: var(--bd-sm);
    padding: 5px 10px;
    opacity: 0.45;
}
.team-phase.done {
    opacity: 0.75;
}
.team-phase.active {
    opacity: 1;
    background: var(--c-black);
    color: var(--c-white);
}
.team-phase-dot {
    width: 8px;
    height: 8px;
    border: var(--bd-sm);
    flex: none;
}
.team-phase.active .team-phase-dot {
    background: var(--c-ok);
    border-color: var(--c-white);
}
.team-phase-label {
    font-size: 10px;
    letter-spacing: 1px;
    text-transform: uppercase;
}
.team-lane-running {
    box-shadow: 0 0 0 2px var(--c-ok) inset;
}
.team-lane-failed {
    box-shadow: 0 0 0 2px var(--c-danger) inset;
}
.team-lane-leader .team-lane-head {
    background: var(--c-panel);
}
.tl-kind-query .tl-label {
    color: var(--c-link);
}
.tl-kind-answer .tl-label {
    color: var(--c-ok);
}
.tl-kind-agent_output .tl-label {
    color: var(--c-black);
    font-weight: 700;
}
.tl-kind-synthesis .tl-label {
    color: var(--c-warn);
}
```

- [ ] **Step 8: Run the component test**

Run: `npx vitest run src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`
Expected: PASS (all tests including the new phase test and the earlier render tests).

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/organisms/TeamRunDetail/index.jsx src/personal_agent_gateway/static/styles.css frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx
git commit -m "feat: phase stepper, richer agent lanes, and color-coded activity in team run detail"
```

---

## Task 10: Shared documents panel (frontend + CSS)

**Files:**
- Modify: `frontend/src/components/organisms/TeamRunDetail/index.jsx` — replace the "Results" column with a "Shared Documents" + "Handoffs" panel.
- Modify: `src/personal_agent_gateway/static/styles.css`
- Test: `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`

**Interfaces:**
- Consumes: existing `messages` (kinds `agent_output`, `query`, `answer`), `agents`, `tasks`, and helpers `initials`, `findAgent`, `findTask`.
- Produces: a "Shared Documents" section listing `agent_output` messages as documents (owner + linked task) and a "Shared / Handoffs" list pairing each `query` with its `answer`.

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`:

```javascript
  it("renders shared documents and handoff pairs", () => {
    render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "Design", status: "completed", run_mode: "plan_and_execute" },
          agents: [
            { id: "a1", name: "Lead", role: "leader", status: "completed" },
            { id: "a2", name: "Worker", role: "member", status: "completed" }
          ],
          tasks: [{ id: "t1", title: "Build API", status: "completed" }],
          messages: [
            { id: "m1", kind: "query", sender_agent_id: "a2", content: "which schema?", created_at: "2026-07-13T00:00:00Z" },
            { id: "m2", kind: "answer", sender_agent_id: "a1", content: "use schema X", created_at: "2026-07-13T00:01:00Z" },
            { id: "m3", kind: "agent_output", sender_agent_id: "a2", content: "API built", metadata: { task_id: "t1" }, created_at: "2026-07-13T00:02:00Z" }
          ]
        }}
      />
    );

    expect(screen.getByText("Shared Documents")).toBeInTheDocument();
    expect(screen.getByText("API built")).toBeInTheDocument();
    expect(screen.getByText("Build API")).toBeInTheDocument();
    expect(screen.getByText("which schema?")).toBeInTheDocument();
    expect(screen.getByText("use schema X")).toBeInTheDocument();
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx -t "shared documents"`
Expected: FAIL — no "Shared Documents" text.

- [ ] **Step 3: Add the handoff helper**

In `frontend/src/components/organisms/TeamRunDetail/index.jsx`, add near the other module-level helpers (after `findTask`, line 22):

```javascript
function buildHandoffs(messages) {
  const queries = messages.filter((message) => message.kind === "query");
  const answers = messages.filter((message) => message.kind === "answer");
  return queries.map((query, index) => ({ query, answer: answers[index] || null }));
}
```

- [ ] **Step 4: Replace the Results column with the documents panel**

In the component, replace the entire `<div className="team-results-col"> ... </div>` block (currently lines 151-176) with:

```javascript
        <div className="team-results-col">
          <div className="team-section-head">
            <span className="mono team-section-label">Shared Documents</span>
            <span className="team-section-rule" />
          </div>
          <div className="team-docs">
            {reports.length ? (
              reports.map((message) => {
                const sender = findAgent(agents, message.sender_agent_id);
                const task = findTask(tasks, message.metadata?.task_id);
                const avatar = sender?.persona_snapshot?.avatar;
                return (
                  <article className="team-doc-card" key={message.id}>
                    <div className="team-doc-head">
                      {avatar ? (
                        <img className="team-doc-avatar" src={`/static/avatars/${avatar}.png`} alt="" />
                      ) : (
                        <span className="team-doc-avatar team-doc-avatar-initials mono">{initials(sender?.name)}</span>
                      )}
                      <div className="team-doc-meta">
                        <span className="mono team-doc-owner">{sender ? sender.name : "Agent"}</span>
                        {task ? <span className="team-doc-task">{task.title}</span> : null}
                      </div>
                    </div>
                    <p className="team-doc-body">{message.content}</p>
                  </article>
                );
              })
            ) : (
              <div className="team-task-empty mono">-</div>
            )}
          </div>

          {handoffs.length ? (
            <>
              <div className="team-section-head">
                <span className="mono team-section-label">Shared / Handoffs</span>
                <span className="team-section-rule" />
              </div>
              <div className="team-handoffs">
                {handoffs.map(({ query, answer }) => {
                  const asker = findAgent(agents, query.sender_agent_id);
                  const responder = answer ? findAgent(agents, answer.sender_agent_id) : null;
                  return (
                    <div className="team-handoff" key={query.id}>
                      <div className="team-handoff-q">
                        <span className="mono team-handoff-who">{asker ? asker.name : "Agent"} →</span>
                        <span className="team-handoff-text">{query.content}</span>
                      </div>
                      {answer ? (
                        <div className="team-handoff-a">
                          <span className="mono team-handoff-who">{responder ? responder.name : "Leader"} ↩</span>
                          <span className="team-handoff-text">{answer.content}</span>
                        </div>
                      ) : (
                        <div className="team-handoff-a team-handoff-unanswered mono">no answer (budget/cap reached)</div>
                      )}
                    </div>
                  );
                })}
              </div>
            </>
          ) : null}

          {run.summary ? (
            <div className="team-final-summary">
              <div className="mono team-final-summary-head">FINAL SUMMARY · {leader?.name || ""}</div>
              <div className="team-final-summary-body">{run.summary}</div>
            </div>
          ) : null}
        </div>
```

- [ ] **Step 5: Compute `handoffs` in the component body**

Where `reports` is defined (currently line 35), add the `handoffs` line right after it:

```javascript
  const reports = messages.filter((message) => message.kind === "agent_output");
  const handoffs = buildHandoffs(messages);
```

- [ ] **Step 6: Add styles**

Append to `src/personal_agent_gateway/static/styles.css`:

```css
.team-docs {
    display: flex;
    flex-direction: column;
    gap: 8px;
}
.team-doc-card {
    border: var(--bd);
}
.team-doc-head {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    border-bottom: var(--bd-in);
}
.team-doc-avatar {
    width: 22px;
    height: 22px;
    flex: none;
    border: var(--bd-sm);
    object-fit: cover;
}
.team-doc-avatar-initials {
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 9px;
    font-weight: 700;
}
.team-doc-meta {
    display: flex;
    flex-direction: column;
    min-width: 0;
}
.team-doc-owner {
    font-size: 11px;
    font-weight: 700;
}
.team-doc-task {
    font-size: 10px;
    color: var(--c-grey);
}
.team-doc-body {
    padding: 9px 12px;
    font-size: 12px;
    line-height: 1.5;
    color: var(--c-dark);
    margin: 0;
    white-space: pre-wrap;
}
.team-handoffs {
    display: flex;
    flex-direction: column;
    gap: 8px;
}
.team-handoff {
    border: var(--bd-sm);
    padding: 8px 10px;
}
.team-handoff-q,
.team-handoff-a {
    display: flex;
    gap: 6px;
    font-size: 11.5px;
    line-height: 1.4;
}
.team-handoff-a {
    margin-top: 5px;
    padding-top: 5px;
    border-top: 1px solid var(--c-panel);
}
.team-handoff-who {
    flex: none;
    font-size: 9px;
    letter-spacing: 1px;
    color: var(--c-grey);
}
.team-handoff-unanswered {
    color: var(--c-warn);
    font-size: 10px;
}
```

- [ ] **Step 7: Run the component test**

Run: `npx vitest run src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx`
Expected: PASS (all tests).

- [ ] **Step 8: Run the whole frontend suite**

Run: `npx vitest run`
Expected: PASS — no regressions across the frontend.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/organisms/TeamRunDetail/index.jsx src/personal_agent_gateway/static/styles.css frontend/src/components/organisms/TeamRunDetail/TeamRunDetail.test.jsx
git commit -m "feat: shared documents and handoff panel in team run detail"
```

---

## Final verification

- [ ] **Backend:** `python -m pytest tests/test_teams.py tests/test_team_runtime.py tests/test_api_team_runs.py tests/test_app_team_factory.py -v` → all PASS.
- [ ] **Frontend:** `npx vitest run` → all PASS.
- [ ] **Build:** `cd frontend && npx vite build` → succeeds.

---

## Self-Review Notes

**Spec coverage:**
- #1 Avatar bug → Tasks 1 (snapshot field) + 2 (backfill for existing runs). ✓
- #2 Leader ≠ member → Task 3. ✓
- #3 Add work mid-run + reopen → Tasks 4 (drain), 5 (race-safe loop + resume), 6 (add_work), 7 (endpoint), 8 (FE). ✓
- #4 Progress display → Task 9 (phase stepper + lanes + activity), which matches the three selected elements. ✓
- #5 Shared documents → Task 10. ✓

**Known limitation (documented, accepted per spec's personal-tool scope):** if `add-work` lands in the sub-second window after the run loop's final pending-check but before the registry deregisters, the new tasks remain `pending` and are drained by the *next* `add-work` (which then triggers `resume` because `is_running` is false). No work is lost; it may wait for the next request. Concurrent leader model calls (an `add_work` decomposition firing while the same leader is mid-synthesis) are possible but human-paced and out of scope for hardening.

**Type consistency:** `_execute_and_synthesize`, `resume`, `add_work` names are used identically across runtime (Tasks 5-6) and API (Task 7). `onAddWork` and `api.addWork` names match across Task 8. `team-phase` / `tl-kind-<kind>` / `team-doc-*` class names match between JSX (Tasks 9-10) and CSS.
