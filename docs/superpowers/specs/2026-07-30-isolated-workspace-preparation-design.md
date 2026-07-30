# Isolated Workspace Preparation Design

## Goal

Every isolated execution must hand LMG an existing session workspace
directory, including executions whose read policy does not require source
staging.

## Responsibility

PAG owns creation of isolated workspaces because their paths are generated
from PAG-controlled session or run identifiers. LMG remains a validation
boundary: it canonicalizes the supplied path, requires an existing directory,
and enforces the allowed-root policy without creating caller-supplied paths.

`ExecutionContextFactory.for_session()` is the common preparation boundary for
chat and headless executions. Team execution already creates its workspace
before using the same factory, so the shared preparation is idempotent.

## Behavior

Before compiling an isolated execution, PAG creates `consumer_workspace` with
parents when necessary. This applies to both `read_mode="all"` and
`read_mode="none"`; bounded `selected` reads may still create the workspace as
part of source staging.

Failure to create the directory becomes an `ExecutionContractError` with the
stable code `invalid_execution_path`. The diagnostic does not expose the
underlying filesystem error.

PAG does not create paths for `write_mode="full_access"` or
`write_mode="worktree"`. Those modes refer to user-configured or previously
prepared directories, and missing paths must continue to fail LMG validation.

## Data Flow

1. The runtime resolves the effective SPACE policy and a consumer workspace.
2. `ExecutionContextFactory.for_session()` creates the consumer workspace only
   for isolated writes.
3. The execution compiler selects read roots, sandbox, permissions, and
   staging metadata.
4. PAG submits the execution contract to LMG.
5. LMG validates that the workspace exists and is inside an allowed root
   before starting the provider.

## Focused Verification

- `all + isolated` creates the workspace before returning the compiled
  execution.
- `none + isolated` creates the workspace without staging inputs.
- `full_access` does not create a missing configured workspace.
- Workspace creation failure reports `invalid_execution_path`.
- Only the focused execution-context tests are required for this change.
