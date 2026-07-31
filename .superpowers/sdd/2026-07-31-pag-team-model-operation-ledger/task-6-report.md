# Task 6 Implementation Report

## Outcome

Connected the cycle-backed Team model operation ledger to provider waiting,
ambiguous interruption/reconciliation, startup recovery, dispatcher marker
handling, the explicit Resume API, and one shared application service graph.

## Implementation

- Added operation-aware provider recovery:
  - atomic `invoking -> waiting_for_provider` plus Team source-state transition;
  - one-shot operation claim with stage-specific source restoration;
  - atomic ambiguous interruption;
  - strict LMG session identity reconciliation for explicit user Resume only;
  - startup classification for prepared, invoking, completed, waiting, and
    ambiguous operations.
- Added `ProviderRecoveryClaim.operation_id`,
  `ProviderOperationWaiting`, `AmbiguousOperationNotReconcilable`, and
  `OperationReconcileResult`.
- Routed Runtime provider exhaustion and ambiguous invocation markers through
  persisted recovery transitions before dispatcher handling.
- Preserved waiting cycles without settlement and preserved ambiguous cycles
  under the existing interrupted-series policy.
- Added startup scheduling:
  - prepared operations resume;
  - completed operations enter Runtime for local application without another
    model call;
  - add-work operations use the persisted effective instruction and the same
    add-work -> resume orchestration path;
  - ambiguous operations are never scheduled.
- Added the explicit Resume reconciliation gate. No-match, duplicate,
  provider/consumer identity mismatch, and loader failure remain interrupted
  and return `409 ambiguous_operation_not_reconcilable`.
- Created one shared `TeamModelOperationService`,
  `TeamModelEffectService`, `TeamModelInvoker`, and `TeamProviderRecovery` in
  `create_app()` and injected those exact instances into Runtime and the
  dispatcher.
- Replaced production whole-cycle metadata writes with the owned provider and
  agent metadata setters.
- Excluded operation-backed runs from the legacy generic startup interrupt so
  operation reconciliation remains authoritative.

## TDD Evidence

Initial provider recovery RED:

```text
10 failed, 10 passed
```

The failures covered the missing operation-aware constructor, waiting claim,
ambiguous reconciliation, and startup reconciliation paths. Dispatcher marker,
API strict Resume, startup scheduling, generic interrupt exclusion, and
internal ambiguous Resume guards were each added as focused RED regressions
before their implementation.

## Final Focused Verification

The exact brief test set was split by file group after the combined final
command exceeded the Windows command timeout:

```text
169 passed
  tests/test_migrations.py
  tests/test_team_model_operations.py
  tests/test_remote_model_client.py
  tests/test_team_model_invoker.py
  tests/test_team_model_effects.py
  tests/test_team_provider_recovery.py

153 passed
  tests/test_team_runtime.py

49 passed
  tests/test_team_cycle_dispatcher.py
  tests/test_app_team_factory.py

52 passed
  tests/test_api_team_runs.py
```

Total focused evidence: **423 passed**.

```text
ruff check <brief file list>
All checks passed!

rg -n "set_cycle_execution_metadata" src/personal_agent_gateway
src/personal_agent_gateway\teams.py:561:    def set_cycle_execution_metadata(

git diff --check
PASS
```

No full test suite was run.

## Notes

- The two existing cancel-during-add-work API regressions now use the same
  ready registry fixture already used by other API tests. Without it, the local
  environment's unavailable LMG stopped provider capability freeze before the
  test's fake Runtime could be reached.
- Cycle execution metadata continues to contain semantic source and owned
  provider/agent snapshots only; operation continuation and receipts stay in
  the operation ledger/domain rows.

## Fix Round 1

### Review Findings Addressed

- Made continuous cancellation one atomic boundary for open model operations,
  cycle requests, cycles, tasks, agents, AUTO series, hooks, decisions, and the
  Team Run. Provider-waiting cycle/task states are now included, and every open
  operation transitions to `canceled` before the transaction commits.
- Added a startup guard that cancels any lingering open operation whose
  run/cycle/request source is already canceled. Startup reconciliation cannot
  rewrite canceled source state to `interrupted`.
- Routed explicit Resume through the dispatcher's shared operation-stage path.
  Initial add-work and add-work repair ordinal 2 both use
  `continue_cycle(add_work -> resume)` with the persisted instruction and exact
  claimed operation; all other stages use `resume`.
- Routed decision-answer background Resume through dispatcher marker
  observation. `ProviderOperationWaiting` is consumed without settlement and
  `AmbiguousModelOperation` applies the existing interrupted-series pause
  policy instead of becoming an unobserved background exception.
- Added production-path integration coverage for dispatcher provider wait,
  invalid-plan repair wait, Worker-applied Lead wait/claim, Worker-applied Lead
  ambiguity, completed-operation startup local apply, invoking-operation
  startup interruption, cancellation, and initial/repair explicit add-work
  Resume.

### RED Evidence

```text
3 failed, 23 deselected
  waiting cancellation left the operation waiting_for_provider
  invoking cancellation was reconciled to interrupted
  canceled source startup was reconciled to interrupted

2 failed, 6 passed, 46 deselected
  initial and repair add-work explicit Resume called resume(), not continue_cycle()

1 failed, 54 deselected
  AUTO decision-answer ambiguity timed out waiting for paused_interrupted
  Task exception was never retrieved: AmbiguousModelOperation
```

### Focused Verification

The Task 6 brief test files were split to avoid the Windows command timeout:

```text
55 passed
  tests/test_team_provider_recovery.py
  tests/test_team_cycle_dispatcher.py

59 passed
  tests/test_api_team_runs.py

156 passed
  tests/test_team_runtime.py

168 passed
  tests/test_migrations.py
  tests/test_team_model_operations.py
  tests/test_remote_model_client.py
  tests/test_team_model_invoker.py
  tests/test_team_model_effects.py
  tests/test_app_team_factory.py
```

Total focused evidence: **438 passed**.

```text
ruff check <Task 6 brief files plus team_cycles.py>
All checks passed!

rg -n "set_cycle_execution_metadata" src/personal_agent_gateway
src/personal_agent_gateway\teams.py:561:    def set_cycle_execution_metadata(

git diff --check
PASS
```

No full test suite was run.
