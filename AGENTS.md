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
