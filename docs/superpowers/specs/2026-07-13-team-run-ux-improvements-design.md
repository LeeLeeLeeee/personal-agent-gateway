# Team Run UX Improvements — Design

- Date: 2026-07-13
- Scope: personal-agent-gateway Persona Agent Teams — the "team run" lifecycle UI and runtime
- Status: approved for planning

## Summary

Five improvements to the team run experience, delivered as one spec:

1. **Avatar fix** — agent avatars never render; root cause is a missing snapshot field.
2. **Leader ≠ member** — the same persona can currently be picked as both leader and member.
3. **Request additional work mid-run + reopen** — no way to add work once a run is planned.
4. **Better progress display** — phase stepper, richer agent lanes, live-feeling activity.
5. **Identifiable shared documents** — surface what agents actually share as "documents".

These share the `TeamRunDetail` surface and the `team_runtime` execution loop, so they ship
together rather than as five isolated patches.

## Current State (verified against code)

- `team_runtime.TeamRuntime.start()` runs `plan → execute → synthesize → terminal` as a single
  `asyncio` task. Tasks are created only during `_plan`. `_execute` iterates a **one-shot snapshot**
  of `list_tasks()` (`team_runtime.py:126`), so tasks added later are never picked up.
- Agents are pure LLM `model.complete()` calls. `workspace_root` / `workspace_path` exist in the DB
  but the runtime writes **no files**. The only things agents "share" are:
  - `agent_output` messages (a worker's task result),
  - `query` / `answer` messages (worker→leader question, leader→worker answer, via `_mediate`).
- `_persona_snapshot()` (`teams.py:391`) omits `avatar`. `TeamRunDetail` and `TeamTaskCard` read
  `persona_snapshot.avatar`, so it is always `undefined` → always initials fallback.
- `TeamRunForm` lists every persona in both the leader group and the member group; nothing prevents
  a persona being leader and member simultaneously.
- SSE: `GatewayApp` (`index.jsx:260`) refetches the selected run's detail on **any** `team.*` event
  whose `team_run_id` matches. New event types are picked up with no extra wiring.

## Design

### 1. Avatar fix (backend)

- Add `"avatar": persona.avatar` to the dict returned by `_persona_snapshot()` (`teams.py:391`).
- FE already reads `persona_snapshot.avatar` correctly — no FE change.
- **Snapshot semantics:** snapshots freeze at run start, so this only affects **new** runs.
  To make existing runs show avatars, add a one-time, cosmetic backfill: for existing
  `team_agents` rows whose `persona_id` still resolves to a live persona, inject that persona's
  `avatar` into the stored `persona_snapshot_json`. Avatar is presentation-only, not behavioral,
  so backfilling it does not violate the intent of snapshotting.

### 2. Leader ≠ member (frontend)

- In `TeamRunForm`, render the current leader persona in the member list as **disabled** with a
  `LEADER` badge; it cannot be toggled as a member.
- When the leader changes, if the new leader was already selected as a member, auto-remove it from
  `memberPersonaIds`.

### 3. Additional work mid-run + reopen (backend + frontend)

**Runtime (`team_runtime.py`):**

- Replace the one-shot snapshot in `_execute` with a **drain loop**: on each iteration, re-query
  pending tasks and assign the next one to a worker (round-robin via a running counter). Stop when
  no pending tasks remain. This absorbs tasks added while the run is executing.
- Wrap the execute/synthesize sequence so it closes the "finished synthesizing just as work was
  added" race:
  ```
  loop:
    execute-drain (until no pending tasks)
    synthesize
    if new pending tasks appeared during synthesize: continue
    else: finalize terminal status; break
  ```
- Add `resume(team_run_id)`: for a run in a terminal status, set it back to `running` and re-enter
  the loop above (run the newly-added pending tasks, then re-synthesize). Workers left in
  `completed` may be reassigned.

**API (`api/team_runs.py`):** `POST /api/team-runs/{id}/add-work` with body `{ "instruction": str }`
(async endpoint):

1. Ask the leader agent (resuming its existing session) to decompose `instruction` into one or more
   tasks; insert them as `pending` and publish `team.task.created` per task.
2. If `registry.is_running(id)` → the live drain loop absorbs them; no further action.
   Otherwise (terminal/idle) → register a `resume` task via the runtime.

Publish `team.run.reopened` when a terminal run is resumed so the UI refetches.

**Frontend (`TeamRunDetail` + `GatewayApp`):**

- Add an "additional work" textarea + submit control to `TeamRunDetail`. Label reads "요청" while the
  run is active and "재개하며 요청" while it is terminal.
- `TeamRunDetail` takes an `onAddWork(instruction)` prop; `GatewayApp` wires it to `api.addWork(id, instruction)`.
  The existing `team.*` SSE refetch reflects the result.

### 4. Progress display (frontend, `TeamRunDetail`)

- **Phase stepper** across the top: `Planning → Executing → Summarizing → Done`, mapped from
  `run.status`, with the current phase highlighted. (`planning`→Planning; `running`→Executing;
  `summarizing`→Summarizing; terminal statuses→Done.)
- **Richer agent lanes:** visually emphasize a running worker (pulse/border), show its current task
  title, distinguish leader vs worker, and make waiting / running / completed states clearly
  differentiated.
- **Live-feeling activity timeline:** newest entries surfaced (top or auto-scroll), color-coded by
  `kind` (`plan_note` / `query` / `answer` / `agent_output` / `synthesis`).

(Overall progress bar intentionally out of scope — not requested.)

### 5. Shared documents panel (frontend, `TeamRunDetail`)

No filesystem; re-frame existing messages as documents:

- **Documents:** each `agent_output` message = a result document produced by an agent. Show owner
  (avatar + name) and the linked task title.
- **Shared / handoff flow:** pair each `query` (worker→leader) with its `answer` (leader→worker) so
  the user can identify who shared / requested what from whom.
- **Final summary:** unchanged.

## Data Flow / SSE

New event types (`team.run.reopened`, and reused `team.task.created`) are handled by the existing
`team.*` refetch in `GatewayApp` (`index.jsx:260`). Minimal new wiring.

## Testing

**Backend (pytest — extend `test_team_runtime.py`, `test_api_team_runs.py`):**

- Drain loop absorbs a pending task added while the run is executing.
- Synthesize-then-new-pending race: work added during synthesize is still executed before terminal.
- `resume` on a terminal run executes newly-added tasks and re-synthesizes.
- `add-work` asks the leader to decompose an instruction and creates pending tasks; routes to drain
  (running) vs resume (terminal).
- `_persona_snapshot` includes `avatar`; backfill populates existing snapshots whose persona survives.

**Frontend (vitest):**

- Member list disables the current leader and auto-deselects on leader change.
- Phase stepper maps each `run.status` to the right active step.
- Shared documents panel renders `agent_output` as documents and pairs `query`/`answer`.
- `onAddWork` fires with the entered instruction.

## Out of Scope

- Real filesystem workspace / agent file-writing tools.
- Overall progress percentage bar.
- Changes to persona library, chat, or non-team screens.
