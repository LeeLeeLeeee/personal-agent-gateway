# Repository Agent Instructions

## Windows local runtime

- On Windows, do not start PAG, LMG, or `scripts/start_local_runtime.ps1`
  as a long-running process from a Codex-managed command. Those processes
  can inherit the Codex Windows Job lifetime and exit when the command or
  user turn ends.
- When asked to start PAG or LMG, resolve
  `scripts/start_local_runtime.ps1` from this repository root and give the
  user its absolute path plus a copyable command for a normal PowerShell
  window. For this checkout, the command is:

  ```powershell
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\Administrator\playground\personal-agent-gateway\scripts\start_local_runtime.ps1"
  ```

- Do not retry the long-running launch through Explorer parenting, COM,
  WMI, or direct Job breakaway.
- After the user starts the runtime, read-only checks of health endpoints,
  listener PIDs, state, and logs are allowed.
- One-shot work that may end with the Codex command remains allowed,
  including `pytest`, builds, and short diagnostics.

## Running the backend suite

The authoritative run is serial and takes about 12 minutes:

```
PYTHONPATH=src python -m pytest -q -p no:randomly
```

`pytest-xdist` is installed and `-n auto` finishes the same suite in about
2 minutes, which is worth it while iterating. **It is not the authoritative
run**, because three tests fail under xdist and nobody has found out why:

- `tests/test_api_team_runs.py::test_worktree_delivery_commits_and_applies_to_space_repository`
- `tests/test_api_team_runs.py::test_worktree_delivery_resolves_conflict_before_applying`
- `tests/test_api_team_runs.py::test_worktree_delivery_auto_resolves_generated_doc_indexes`

What is known, so the next person does not repeat it: each one fails when run
**alone** under `-n 2`, so it is not a race between tests; it is something about
the worker environment itself. Each passes serially. The repository's own
cleanliness is not the cause — they fail with a clean working tree. The failure
is a delivery apply returning `409` after the test removes the file that made it
dirty, so git still reports the worktree as dirty inside a worker.

Use `-n auto` for fast feedback, then confirm serially before claiming the suite
is green. Do not mark these three skipped under xdist: a fast path that silently
drops tests is worse than a slow one.

## Linting

```
python -m ruff check src/ tests/ evaluation/
```

Rule selection is pinned in `pyproject.toml` rather than inherited from ruff's
defaults, which change between versions — 0.15 and 0.16 disagreed by 254
findings on this tree before it was pinned. Any ruff at or above the declared
floor now gives the same answer.
