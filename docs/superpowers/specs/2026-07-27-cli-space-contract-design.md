# CLI SPACE Contract Design

## Goal

Prevent ordinary sessions and Hooks from sending a CLI execution request that
LMG must reject because its read path is outside the execution workspace.

## Decision

PAG will convert an effective SPACE policy into a CLI-safe execution context in
one shared helper. The helper will be used by ordinary sessions and headless
Hook runtimes; Team execution will keep the same containment rule.

For Codex and Claude, a read path is included in `read_roots` only when it is
inside the selected workspace. The default `home` policy therefore produces an
empty CLI read-root list when the workspace is isolated. A selected external
path is rejected in PAG with an actionable domain error instead of reaching
LMG as HTTP 422.

## Boundaries

- LMG continues to reject external CLI read roots. Its security boundary is not
  weakened.
- SPACE paths remain available to PAG-local tools; this change only normalizes
  the execution payload sent to LMG.
- This change does not stage or copy external files into a workspace.

## Error handling

PAG will preserve LMG's stable `invalid_execution_path` response code rather
than reducing every 422 response to `remote_gateway_http_422`.

## Verification

- Default `home` plus isolated workspace produces no CLI read roots for Codex
  and Claude session and Hook runtimes.
- A read path inside the workspace is retained.
- An external selected read path raises a PAG validation error before an LMG
  request is made.
- LMG's `invalid_execution_path` response is exposed as a stable PAG error.
