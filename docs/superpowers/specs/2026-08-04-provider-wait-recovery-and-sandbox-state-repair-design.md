# Provider Waiting Recovery and Sandbox State Repair Design

## Goal

Recover Codex provider readiness after a corrupt Windows sandbox ACL state file,
and make `waiting_for_provider` Team Runs resume automatically once the affected
provider is ready.

## Scope

- Back up the corrupt Codex sandbox state file before removing only that file.
- Verify recovery with the existing sandbox canary.
- Poll persisted provider-waiting cycles every 30 seconds.
- Resume the same claimed model operation and cycle exactly once after its
  assigned provider reports ready.
- Keep polling while the provider remains unavailable and persist the next
  check timestamp.

## Non-goals

- Do not disable Codex sandboxing or change its global permission policy.
- Do not change Team membership, provider assignment, task ownership, or the
  immediate three-attempt model-admission retry policy.
- Do not restart or mutate unrelated Team Runs.

## Design

`TeamCycleLoop` will own scheduled recovery checks, as established by the
existing provider-recovery design. On each 30-second tick it will consider only
cycles whose persisted `provider_recovery.next_retry_at` is due. The recovery
service will read the current descriptor for the affected provider.

- If the provider is still not ready, the cycle stays
  `waiting_for_provider` and its next retry is moved forward by 30 seconds.
- If the provider is ready, the recovery service atomically claims the open
  waiting operation, restores the related task/run/cycle state, and the
  dispatcher resumes that operation.
- The claim is compare-and-set, so concurrent ticks cannot schedule duplicate
  work. Restart reconciliation leaves waiting operations intact for the next
  loop tick.

The one-time sandbox repair backs up
`C:\\Users\\Administrator\\.codex\\.sandbox\\deny_read_acl_state.json` with a
timestamped `.bak` suffix, removes the corrupt original, and reruns the
read-only `codex sandbox -- cmd.exe /d /c exit 0` canary. A successful canary
must recreate valid state before PAG/LMG is restarted or the waiting Run is
allowed to recover.

## Verification

- A failing test proves a due and ready provider resumes exactly once.
- A failing test proves an unready provider remains waiting and is rescheduled.
- Existing model-invoker, provider-recovery, cycle-loop, and dispatcher tests
  remain green.
- Before touching the sandbox state file, inspect it and write a timestamped
  backup. Verify the canary exit status and resulting state file validity.
