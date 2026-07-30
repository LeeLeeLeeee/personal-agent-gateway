# Provider Capability and Readiness Recovery Design

## Goal

Prevent a transient LMG status or model-catalog lookup failure from failing
an active Team task before Codex or Claude is invoked.

The design separates the stable execution contract from current provider
readiness, freezes the contract for a Team cycle, retries only transient
status failures, and persists provider waiting so recovery survives process
restarts.

## Problem

`AgentRegistry.catalog()` currently refreshes the entire provider descriptor
from `GET /v1/models`. A failed refresh replaces the previous usable catalog
with unavailable descriptors. Team model creation then rejects the task
because the descriptor is unavailable or its execution capability snapshot is
missing.

This couples three different concerns:

- capability: what the provider supports;
- readiness: whether the provider can start a run now;
- admission: whether the gateway currently has capacity for a new run.

Run `76c8d75eabe24dff864377ae8891987b` demonstrated the failure mode. Two
tasks completed, then three tasks assigned across Claude and Codex failed
before an LMG session was created. The frozen Team SPACE policy was
`read_mode=all`; it did not cause the failure.

## Definitions

### Capability

A relatively stable execution contract:

- supported network modes;
- supported sandbox modes;
- supported permission modes;
- resume support;
- external read-root support.

Capability changes when the provider implementation or relevant configuration
changes. It does not disappear because a health check times out.

### Readiness

A transient provider state:

- binary and credentials are usable;
- provider preflight succeeds;
- the gateway can reach the provider.

### Admission

A transient capacity state:

- global execution capacity is available;
- provider-specific capacity is available;
- the admission queue can accept the run.

### Last-known-good Snapshot

The most recent capability and model catalog that passed protocol validation.
A retryable refresh failure may make the snapshot stale, but does not erase it.

## Scope

The change spans the local-model-gateway and personal-agent-gateway
repositories.

In scope:

- capability/readiness state separation;
- last-known-good snapshot retention;
- bounded status retries;
- Team-cycle capability freezing;
- persisted `waiting_for_provider` recovery;
- automatic recovery and delayed user warning;
- focused backend and frontend verification.

Out of scope:

- automatic reassignment to a different persona or provider;
- retrying invalid execution contracts;
- changing Team acceptance recovery;
- changing SPACE or rules snapshot behavior;
- generic retry of ambiguous model-run timeouts.

## Architecture

### LMG Capability Source

LMG keeps provider execution capabilities independent from live readiness.
The provider registry already supplies the execution contract without running
the model.

`GET /v1/models` returns:

- execution capabilities;
- provider-specific readiness;
- gateway admission state;
- snapshot freshness metadata.

Model-catalog detection refreshes asynchronously or behind the existing cache.
If refresh fails and a last-known-good catalog exists, LMG returns that catalog
with:

- `snapshot_status: "stale"`;
- the original `detected_at`;
- a stable refresh error code;
- HTTP 200.

LMG returns `503 capabilities_unavailable` only when it has never produced a
valid snapshot. It logs the refresh duration and stable error code without
logging secrets.

### PAG Provider Snapshot Store

`AgentRegistry` keeps the last-known-good descriptor catalog under a lock.
Refresh results are handled as follows:

- success: replace the snapshot and mark it fresh;
- retryable failure: retain the snapshot and mark it stale;
- unauthorized or protocol failure: expose a hard readiness error and do not
  treat the refresh as recoverable;
- no prior snapshot: expose unavailable providers.

Provider descriptors expose capability and readiness separately. Compiling an
execution context validates the frozen capability only. Live readiness is
checked by the status/recovery boundary and ultimately by `POST /v1/runs`.

### Team Cycle Freeze

Before planning or executing cycle tasks, PAG resolves every provider used by
the cycle roster and freezes its execution capability snapshot in
`team_run_cycles.execution_metadata_json`.

Each task in the cycle compiles execution from this frozen snapshot. It does
not refresh the global provider catalog on the task hot path.

If a new cycle has no usable snapshot for one of its providers, it does not
start task execution. It enters `waiting_for_provider` and follows the same
recovery flow described below.

### Provider Recovery Coordinator

A small provider-recovery service owns retry classification, waiting metadata,
and atomic state transitions. `TeamCycleLoop` calls the service during its
existing tick instead of sleeping inside a running Team worker.

This keeps responsibilities separate:

- `AgentRegistry`: provider snapshot and freshness;
- execution compiler: capability validation;
- recovery service: readiness retry and waiting transitions;
- `TeamRuntime`: semantic task execution and Lead review;
- `TeamCycleLoop`: scheduled recovery checks.

## Retry Policy

### Status and Capability Refresh

A status lookup makes at most three attempts in total.

- retry delays: 0.5 seconds, then 1.5 seconds;
- retryable: connection failure, request timeout, HTTP 502/503/504,
  `capabilities_unavailable`, and gateway `not_ready`;
- non-retryable: unauthorized, protocol mismatch, malformed capability data,
  and unsupported protocol versions.

When a last-known-good snapshot exists, refresh retries run outside the active
task hot path. The task uses its cycle snapshot immediately.

### Model Run Admission

Explicit pre-stream failures are safe to retry because LMG confirms that the
provider run did not start:

- `provider_not_ready`;
- `provider_unavailable`;
- `capacity_exceeded`.

The same total-attempt and delay policy applies.

A generic model-run timeout is not automatically replayed. It may be
ambiguous whether the provider accepted the run. If an upstream session exists,
PAG resumes that session. Otherwise PAG reconciles LMG session state; an
unknown outcome remains interrupted instead of starting duplicate work.

### Non-retryable Execution Failures

These fail normally and never enter provider recovery:

- unauthorized;
- protocol mismatch;
- unsupported execution capability;
- invalid execution path;
- invalid frozen SPACE contract.

## Persisted State

Add `waiting_for_provider` to Team run, cycle, and task status contracts.
While waiting:

- the current agent uses its existing `waiting` status;
- the cycle request remains dispatching and is not settled;
- the current task is not marked failed;
- the orchestrator releases its in-memory run registration.

Provider recovery metadata is stored in cycle execution metadata:

```json
{
  "provider_recovery": {
    "provider": "claude",
    "task_id": "task-id",
    "reason_code": "provider_not_ready",
    "attempts": 3,
    "first_failed_at": "RFC3339 timestamp",
    "next_retry_at": "RFC3339 timestamp",
    "warning_visible_at": "RFC3339 timestamp"
  }
}
```

The warning threshold is two minutes after `first_failed_at`.

## State Flow

```text
running task
  -> explicit retryable pre-stream failure
  -> bounded immediate retries
  -> waiting_for_provider
  -> periodic readiness check
     -> still unavailable: persist next_retry_at
     -> ready: atomically claim recovery
               restore task to pending
               restore cycle and run to running
               resume the same cycle once
```

Recovery uses a compare-and-set transition from `waiting_for_provider` to
`running`. Concurrent loop ticks or a manual `RESUME` therefore cannot schedule
the same cycle twice.

On process startup, reconciliation preserves `waiting_for_provider` instead of
converting it to `interrupted`. The next loop tick resumes recovery checks.

## Lead Responsibility

Infrastructure recovery does not invoke the Lead. Before provider execution
there is no task result for the Lead to inspect, and the Lead may depend on the
same unavailable provider.

The Lead remains responsible only after a model produced a result:

- acceptance evidence is missing;
- required outputs are undeclared or invalid;
- rules are violated;
- the result requires worker revision or another semantic attempt.

Provider reassignment is not automatic. It changes the frozen execution plan
and remains an explicit future policy decision.

## User Experience

Before two minutes:

- the Task Board shows `WAITING FOR PROVIDER`;
- no failed-task card is created;
- the cycle remains active;
- a concise provider and next-check timestamp is shown.

After two minutes:

- the waiting state is emphasized;
- `RESUME` performs an immediate guarded readiness check;
- `CANCEL` follows the existing cancellation flow;
- automatic background checks continue.

Raw gateway diagnostics and secrets are never shown. Stable reason codes are
available in detail views and logs.

## Observability

Record:

- provider;
- snapshot status and age;
- refresh duration;
- stable refresh error code;
- retry attempt number;
- transition into and out of `waiting_for_provider`;
- automatic versus manual recovery trigger.

Do not record local tokens, provider credentials, raw stderr, or unredacted
provider responses.

## Focused Verification

### LMG

- A successful detection creates a fresh snapshot.
- A retryable refresh failure returns the last-known-good snapshot as stale.
- A first-ever detection failure returns `503 capabilities_unavailable`.
- Capability output remains present when readiness or admission is false.
- Refresh logs contain duration and stable code without sensitive details.

### PAG Provider Registry

- A retryable refresh failure preserves the prior capability snapshot.
- Hard authentication and protocol failures are not treated as stale success.
- Concurrent refresh calls cannot replace a newer successful snapshot with a
  failed result.
- Capability compilation does not depend on transient readiness.

### Team Recovery

- A cycle freezes all required provider capabilities before task execution.
- Explicit retryable pre-stream errors receive exactly three total attempts.
- Exhausted retries transition the same task, cycle, and run to
  `waiting_for_provider`.
- The cycle request is not settled while waiting.
- Provider recovery resumes the same task and cycle once.
- Concurrent loop ticks do not schedule duplicate execution.
- Restart reconciliation preserves provider waiting.
- Non-retryable execution errors fail without provider waiting.
- Result and acceptance failures still follow the Lead recovery path.

### Frontend

- The Task Board displays provider waiting separately from failed and blocked.
- The warning changes after two minutes.
- Manual `RESUME` and `CANCEL` actions are available only in the applicable
  state.

Only related LMG tests, PAG provider/Team recovery tests, affected frontend
tests, and the frontend production build are required during implementation.
Full-suite execution requires a separate risk-based decision.

## Success Criteria

- A transient capability/status lookup failure cannot erase a usable snapshot.
- An active Team cycle does not refresh provider capabilities per task.
- A provider outage does not become a semantic task failure.
- Recovery survives PAG restart and resumes the same cycle at most once.
- Lead retry remains reserved for actual task-result problems.
- A persistent outage becomes visible to the user after two minutes without
  stopping automatic recovery checks.
