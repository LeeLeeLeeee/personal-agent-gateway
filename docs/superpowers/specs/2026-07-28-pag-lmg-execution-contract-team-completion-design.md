# PAG–LMG Execution Contract and Team Completion Design

## Status

- Decision: approved approach B
- Change mode: coordinated breaking change across PAG and LMG
- Scope: Chat, Hook, and Team share one execution contract; semantic completion
  and artifact acceptance apply to Team runs

## Goal

Prevent a provider process that merely returned text from being treated as a
successfully completed Team task. The new design must make declared SPACE
access, effective provider permissions, Windows Codex readiness, task outcome,
and published artifacts agree before a Team run can become `completed`.

## Problem statement

The current flow has four independent meanings of success:

1. PAG resolves a SPACE policy and describes it in the prompt.
2. LMG validates a narrower CLI execution context.
3. LMG reports whether the provider process and stream ended normally.
4. TeamRuntime treats returned model text as successful task completion.

Those meanings are not joined by a checked contract. In the failed Team run
`fe8fa463276044d996cc60cba062d341`, the effective CLI context could not read the
source repository, Codex shell commands failed with
`orchestrator_helper_incomplete`, no integrated document was written, and QA
reported `Not Ready`. The provider process still returned text, so every task
and the run were stored as `completed`.

The artifact packager then archived every file in the isolated workspace,
including Windows cache databases created below a literal `%SystemDrive%`
directory.

## Decisions

### 1. Replace the current CLI SPACE conversion

The current `CLI SPACE Contract Design` intentionally drops the default `home`
read path when it lies outside an isolated workspace. That behavior is
superseded.

PAG will compile a SPACE policy and consumer requirements into an effective
execution specification. Compilation has only two valid results:

- a complete `ExecutionSpec` that LMG can enforce; or
- an `ExecutionContractError` raised before a provider starts.

PAG must never silently remove a requested source path, network requirement,
sandbox boundary, or output requirement.

### 2. Use one execution contract for Chat, Hook, and Team

All PAG consumers will use the same execution compiler and the same LMG wire
contract. Team-only fields such as acceptance criteria remain in PAG and are
not moved into LMG.

This preserves the responsibility boundary:

| Responsibility | Owner |
|---|---|
| Persona, Team, SPACE, task acceptance criteria | PAG |
| Source staging and artifact publication | PAG |
| Effective execution-path and option validation | LMG |
| Provider capability and platform readiness | LMG |
| Provider process and stream outcome | LMG |
| Semantic task and Team-run outcome | PAG |

### 3. Make the protocol change fail closed

LMG will advertise protocol version `2.0`. PAG will require exactly the
supported major version before enabling Chat, Hook, or Team execution.

There is no long-lived compatibility translation between protocol `1.1` and
`2.0`. During deployment, intake is stopped, both services are upgraded, the
database migration and readiness checks run, and intake is reopened only after
the integration suite passes.

### 4. Separate provider outcome from task outcome

`run.completed` means only that the provider process and normalized stream
completed without a provider-level error. It does not mean that a Team task met
its goal.

Team tasks use a separate structured `TaskOutcome`:

```json
{
  "status": "completed",
  "summary": "Created and verified the D3 guide.",
  "reason_code": null,
  "deliverables": [
    {
      "path": "outputs/d3-guide.md",
      "kind": "document"
    }
  ],
  "verifications": [
    {
      "name": "markdown-link-check",
      "status": "passed",
      "evidence": "exit code 0"
    }
  ]
}
```

Allowed task outcome statuses are:

- `completed`: the agent claims the acceptance criteria are satisfied;
- `blocked`: required capability, input, permission, or user decision is
  unavailable;
- `failed`: execution occurred but the task or its verification failed.

An absent or malformed outcome is `blocked` with reason
`invalid_task_outcome`; free-form natural language is stored as diagnostic
content but cannot authorize a completed state.

### 5. Verify completion in PAG

The Team planner must attach acceptance criteria to every required task.
Acceptance criteria contain:

- required deliverable paths or path patterns;
- required verification names;
- whether the task is required or optional.

PAG changes a task to `completed` only when all of the following hold:

1. the provider outcome is `run.completed`;
2. the structured task outcome is `completed`;
3. every declared deliverable exists as a regular file;
4. every deliverable resolves inside the effective workspace;
5. every required verification reports `passed`;
6. input snapshot integrity remains valid;
7. artifact publication succeeds.

Run status follows required-task acceptance:

| Condition | Run status |
|---|---|
| All required tasks accepted | `completed` |
| Required tasks accepted and only optional tasks failed | `completed_with_failures` |
| Any required task is blocked | `blocked` |
| Any required task failed acceptance or execution | `failed` |
| User decision is required | `waiting_for_user` |

Leader synthesis is descriptive. It cannot override these state transitions.
QA is modeled as required acceptance when a run requests QA; a `Not Ready`
result prevents completion.

### 6. Stage unsupported external inputs

LMG continues to reject external read-only roots for CLI providers that cannot
enforce them. PAG resolves that capability mismatch before the request reaches
LMG.

For an isolated execution:

```text
<run-root>/workspace/
  _inputs/
    <source-name>/
    manifest.json
  outputs/
  <agent working files>
```

PAG copies each selected external source into `_inputs`, records canonical
origin, size, and SHA-256 hashes in `manifest.json`, and passes only paths
inside the workspace to LMG. After execution, PAG compares the input manifest.
Changes to staged inputs block acceptance with `input_snapshot_modified`.

For worktree and full-access executions, the selected repository or workspace
is already the effective workspace and is not copied. Deliverables may be
normal changed files inside that workspace, but they must still be declared by
the task outcome.

If staging cannot be completed, the run fails before provider invocation with
`source_staging_failed`. PAG does not fall back to an empty source set.

### 7. Publish declared artifacts only

Agents no longer write directly to the central artifact root. The artifact root
is system-owned and does not need to be exposed through LMG.

After task acceptance, PAG's artifact publisher:

1. canonicalizes every declared deliverable;
2. confirms that it is a regular file inside the effective workspace;
3. applies the existing sensitive-file exclusions;
4. computes size and SHA-256;
5. copies the file to the central artifact store;
6. records source path, published path, hash, task ID, cycle ID, and run ID.

The default Team result package contains the run result, verification report,
and declared published artifacts. A full workspace ZIP is removed from normal
results. If retained for diagnostics, it is explicitly requested, marked as a
diagnostic package, and uses the same sensitive-file exclusions.

### 8. Make Windows Codex readiness a provider responsibility

Windows-only behavior remains in LMG and is implemented in Go files selected by
the `_windows.go` suffix. Non-Windows providers do not execute or import the
Windows setup path.

On native Windows:

- the Codex child environment preserves `SYSTEMDRIVE` in addition to the
  existing safe process variables;
- LMG does not override `CODEX_HOME`, so Codex uses the current Windows user's
  normal Codex state and sandbox setup;
- LMG session ownership remains in its metadata store rather than being
  inferred from a separate Codex home;
- Codex readiness checks use the same user identity, environment, working
  directory rules, and sandbox mode as real runs;
- a process-wide single-flight guard prevents concurrent sandbox setup probes;
- a no-model sandbox canary validates command startup before readiness becomes
  true;
- setup, ACL, logon-right, or helper failures return
  `provider_not_ready` with a stable diagnostic code before a model task starts.

On non-Windows systems, the existing configured Codex home behavior remains
unchanged.

Readiness is cached only for the lifetime of the LMG process. A new process
performs the probe again. `/readyz` and provider capability responses expose
the readiness result without exposing sandbox secrets.

### 9. Expose effective capability and execution metadata

LMG provider capability data will include:

- provider name and availability;
- supported sandbox and permission modes;
- external read-only-root support;
- resume support and resume restrictions;
- network-control support;
- platform readiness and stable failure code.

PAG records the following with every consumer run:

- original SPACE policy;
- compiled `ExecutionSpec`;
- staged-input manifest hash;
- provider and platform readiness result;
- provider terminal outcome;
- non-zero tool activity and capability denials;
- structured task outcome;
- acceptance decision and published artifacts.

Tool failures are diagnostic evidence, not an automatic task failure. A task
may recover from an expected failed command, but it must still satisfy its
acceptance criteria.

## Contracts

### PAG execution requirements

PAG derives this internal value from the consumer and effective SPACE policy:

```python
@dataclass(frozen=True)
class ExecutionRequirements:
    source_roots: tuple[Path, ...]
    workspace_mode: Literal["isolated", "worktree", "full_access"]
    workspace_root: Path | None
    network: Literal["required", "denied", "unspecified"]
```

### LMG execution specification

The protocol `2.0` request carries only effective, enforceable values:

```json
{
  "execution": {
    "workspace_root": "C:\\absolute\\run\\workspace",
    "read_roots": [
      "C:\\absolute\\run\\workspace\\_inputs"
    ],
    "sandbox": "workspace-write",
    "approval_policy": "never",
    "permission_mode": "acceptEdits",
    "network": "required"
  }
}
```

LMG validates that requested values are supported by the selected provider.
Unsupported combinations return `422 unsupported_execution_capability`;
invalid paths return `422 invalid_execution_path`; failed platform readiness
returns `503 provider_not_ready`.

### Team task acceptance

Planner output for each task includes:

```json
{
  "title": "Create D3 guide",
  "description": "Write and verify the integrated guide.",
  "required": true,
  "acceptance": {
    "required_outputs": ["outputs/d3-guide.md"],
    "required_verifications": ["markdown-link-check"]
  }
}
```

These values are stored with the task and are immutable after the task starts.
Retry creates a new task record with a copied acceptance snapshot.

## Data and migration

PAG adds:

- `blocked` to Team run and cycle status domains;
- task `required` and `acceptance_json` fields;
- task `outcome_json` and `acceptance_result_json` fields;
- consumer-run execution metadata sufficient to reproduce the effective
  contract and diagnostics.

Existing completed runs are not reclassified. Their result payloads keep their
historic status and receive no fabricated acceptance record.

The result payload uses the cycle objective when present and the run goal
otherwise. It includes protocol version, effective execution metadata,
acceptance decision, and published artifact hashes.

## Error handling

Failures are assigned to the boundary that can act on them:

| Failure | Owner | Result |
|---|---|---|
| SPACE cannot compile to provider capabilities | PAG | `unsupported_execution_capability` before run |
| Source snapshot cannot be created | PAG | `source_staging_failed` before run |
| Windows Codex sandbox is not ready | LMG | `provider_not_ready` before model invocation |
| Provider process or stream fails | LMG | `run.failed` |
| Provider is cancelled or times out | LMG | `run.aborted` |
| Task outcome is missing or malformed | PAG | task and run `blocked` |
| Required deliverable is absent or unsafe | PAG | task and run `failed` |
| Required QA reports Not Ready | PAG | task and run `failed` |
| Artifact publication fails | PAG | task and run `failed` |

No boundary converts one of these failures to success because explanatory text
was produced.

## Rollout

This is an atomic local-runtime upgrade:

1. stop new Chat, Hook, and Team intake;
2. allow active executions to finish or cancel them explicitly;
3. back up the PAG database and current configuration;
4. deploy LMG protocol `2.0`, capability data, Windows preflight, and new error
   codes;
5. deploy PAG execution compilation, staging, structured task outcomes,
   acceptance, publishing, and database migration;
6. start LMG and require `/readyz` plus provider readiness;
7. start PAG and verify protocol `2.0`;
8. run the cross-repository integration and Windows smoke suites;
9. reopen intake only after all release gates pass.

If a release gate fails, both services remain stopped for intake and are rolled
back together with the database backup. PAG must not run against LMG protocol
`1.1`, and protocol `1.1` PAG must not run against LMG `2.0`.

## Verification strategy

### PAG unit and service tests

- `home` or selected external source is staged instead of silently removed.
- staging failure prevents any LMG request.
- Chat, Hook, and Team use the same execution compiler.
- malformed or missing `TaskOutcome` produces `blocked`.
- natural-language failure text cannot produce `completed`.
- missing, unsafe, or unpublished deliverables prevent completion.
- required QA `Not Ready` prevents completion.
- optional-task failure is the only path to `completed_with_failures`.
- cycle objective is emitted in the result payload.
- only declared deliverables are published and packaged.

### LMG unit and service tests

- protocol version is `2.0`.
- unsupported execution capabilities fail before provider invocation.
- provider readiness has stable public error codes.
- Windows environment preserves `SYSTEMDRIVE`.
- Windows uses the current user's default Codex home.
- concurrent Windows readiness checks execute one sandbox probe.
- failed canary returns `provider_not_ready`.
- non-Windows behavior does not execute the Windows readiness implementation.

### Cross-repository integration tests

- PAG refuses protocol `1.1`.
- LMG rejects malformed protocol `2.0` execution requests.
- an isolated Team run can read a staged source, create a declared output, pass
  QA, publish the artifact, and complete.
- a provider that returns explanatory text after all shell commands fail
  produces a blocked or failed Team run.
- an empty workspace cannot be reported as evidence that a source repository
  contains no matching dependency.
- the published package contains no undeclared files.

### Native Windows smoke test

The runtime is started by the user in a normal PowerShell window, not from a
Codex-managed long-running command. The smoke test verifies:

- LMG readiness is true before PAG intake opens;
- no `orchestrator_helper_incomplete` event occurs;
- no literal `%SystemDrive%` directory is created;
- Codex commands execute under the expected Windows user;
- the known D3 source file is found through staged input;
- the final artifact exists and the run state matches QA.

## Release gates

The breaking change is complete only when:

- PAG and LMG full test suites pass;
- protocol mismatch fails closed in both directions;
- the native Windows smoke test passes twice, including after a full service
  restart;
- the `fe8fa463` failure scenario is reproduced by a regression test and no
  longer ends as `completed`;
- no full-workspace package is produced by default;
- operational documentation describes the new readiness and blocked-state
  diagnostics.

## Superseded and extended documents

- This design supersedes the external-path behavior in
  `2026-07-27-cli-space-contract-design.md`.
- This design extends
  `2026-07-26-pag-lmg-local-integration-hardening-design.md`: provider process
  termination remains LMG-owned, while semantic task completion is explicitly
  PAG-owned.
- The implementation plan
  `2026-07-26-pag-lmg-local-interface-hardening.md` remains historical evidence
  for protocol `1.1`; it must not be appended with protocol `2.0` work.

## Non-goals

- moving PAG domain policy into LMG;
- adding remote or multi-tenant LMG access;
- building a general-purpose workflow engine;
- automatically trusting model-written status text;
- weakening LMG path validation to make unsupported external read roots work;
- automatically modifying Windows account policy, firewall policy, or sandbox
  secrets outside the Codex-supported setup path.
