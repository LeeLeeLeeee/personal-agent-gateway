# npm Start Frontend Build Design

## Context

The repository root `npm start` currently delegates directly to
`scripts/start_local_runtime.ps1`. Frontend changes must be built separately
with `npm --prefix frontend run build`, so starting the runtime can serve an
outdated frontend bundle.

## Goal

Make the default root start command build the frontend before starting the
PAG/LMG runtime while preserving an explicit fast path that skips the build.

## Command contract

- `npm run build:frontend` runs the existing frontend production build.
- `npm start` runs `build:frontend` and starts the runtime only if the build
  succeeds.
- `npm run start:no-build` invokes the existing Windows PowerShell runtime
  launcher directly.
- `npm stop` remains unchanged.

## Architecture

Only the root `package.json` composes these commands. The frontend package
continues to own its Vite build, and `scripts/start_local_runtime.ps1`
continues to own runtime identity checks, configuration, process management,
and health checks.

The default start command uses npm script composition:

```text
npm start
  -> npm run build:frontend
  -> scripts/start_local_runtime.ps1
```

The command chain stops when the frontend build returns a non-zero exit code,
so a failed build cannot start PAG or LMG.

## Documentation

Update the Windows runtime section in `README.md` to list the three start/build
commands and explain when to use the no-build path.

## Verification

Automated verification must not start PAG or LMG.

1. Assert the exact root package script values before and after the change.
2. Run `npm run build:frontend`.
3. Confirm `npm run` registers `build:frontend`, `start`, `start:no-build`,
   and `stop`.
4. Parse the PowerShell launchers without executing them.
5. Run the existing relevant test suites and check the final diff.

## Non-goals

- Do not add a frontend-build switch to the PowerShell launcher.
- Do not add npm dependencies or a root lockfile.
- Do not change runtime startup, shutdown, or health-check behavior.
- Do not start the long-running runtime from a Codex-managed command.
