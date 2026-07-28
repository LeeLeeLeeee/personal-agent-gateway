# npm Local Runtime Launcher Design

## Context

On Windows, PAG and LMG are operated as one local runtime bundle through
`scripts/start_local_runtime.ps1` and `scripts/stop_local_runtime.ps1`.
The current PowerShell command is inconvenient to copy and is easy to break
when its absolute path wraps across lines.

## Goals

- Allow a user in the PAG repository root to start the PAG/LMG bundle with
  `npm start`.
- Allow the same user to stop only the processes tracked by the bundle
  launcher with `npm stop`.
- Keep the existing PowerShell scripts as the single owners of runtime
  configuration, identity checks, health checks, process tracking, and
  shutdown.
- Make the Windows-only nature of these npm commands explicit.

## Non-goals

- Do not add npm commands or a `package.json` to LMG.
- Do not support standalone LMG startup.
- Do not replace the existing frontend package or move its dependencies.
- Do not make Codex-managed Windows commands suitable for long-running
  runtime startup.
- Do not add npm dependencies or a root lockfile.

## Design

Add a private, dependency-free `package.json` to the PAG repository root.
It exposes only two scripts:

- `start` invokes `scripts/start_local_runtime.ps1` with Windows PowerShell,
  no profile, and execution-policy bypass.
- `stop` invokes `scripts/stop_local_runtime.ps1` with the same PowerShell
  options.

Both commands use paths relative to the PAG repository root. `npm start`
therefore starts both PAG and LMG, while `npm stop` delegates safe shutdown to
the existing tracked-process implementation.

The root package is separate from `frontend/package.json`. Frontend commands
continue to run from the frontend directory without changes.

## Platform and execution contract

The root npm commands intentionally call `powershell.exe` and are Windows-only.
They must be entered by the user in a normal PowerShell or cmd session.
Running `npm start` through Codex does not escape the Codex Windows Job and is
still prohibited by the repository agent instructions.

## Documentation

Update the Windows local-runtime section in `README.md` with the short npm
commands and state that the direct PowerShell commands remain available.

## Verification

Because the production change is package configuration only, verify it without
starting the servers:

1. Parse the root `package.json` as JSON.
2. Use `npm run` to confirm that `start` and `stop` are registered.
3. Parse both referenced PowerShell files to confirm valid syntax.
4. Confirm no root `package-lock.json` was created.
5. Confirm the LMG working tree was not changed.

## Success criteria

- `npm start` at the PAG repository root resolves to the existing bundled
  launcher.
- `npm stop` at the PAG repository root resolves to the existing bundled
  stop script.
- No LMG standalone npm interface exists.
- No server is started during automated verification.
