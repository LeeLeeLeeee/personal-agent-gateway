# Personal Agent Web Gateway

## Purpose

Personal Agent Web Gateway is a local-first web gateway for operating a personal
agent from a browser. It serves the app on a loopback host, protects every app
page, static asset, and API with a shared web token, and runs agent filesystem and
shell work inside the configured workspace root.

The default agent backend is the local Codex CLI. The gateway does not need an
OpenAI API key for the default path because it calls `codex exec` on the Mac and
reuses the local Codex login/configuration.

## Non-goals

This is not a public multi-user service, an unauthenticated agent endpoint, or a
general remote shell. It does not remove the need to protect the web token,
local Codex credentials, local machine, workspace files, or tunnel URL.

## Current Architecture

```text
Remote browser
  -> Cloudflare Quick Tunnel public HTTPS URL
  -> local loopback FastAPI gateway
  -> token-protected web UI and API
  -> AgentRuntime
  -> CodexModelClient
  -> local `codex exec --json`
  -> local workspace filesystem and shell access controlled by Codex
```

The gateway binds only to `127.0.0.1` or `localhost`. Cloudflare Quick Tunnel is
the only public ingress in the current Version A setup. The tunnel forwards
traffic to the loopback server, and the gateway still requires the shared web
token before serving pages, static assets, or APIs.

Main modules:

- `src/personal_agent_gateway/app.py`: FastAPI app, routes, static HTML, and
  provider selection.
- `src/personal_agent_gateway/auth.py`: shared-token authentication from query
  string, bearer token, or HttpOnly cookie.
- `src/personal_agent_gateway/runtime.py`: conversation runtime, transcript
  loading/saving, provider invocation, and sensitive value redaction.
- `src/personal_agent_gateway/model_client.py`: provider clients. The default
  `CodexModelClient` invokes local `codex exec --json`; `OpenAIModelClient`
  remains available for explicit API-backed runs.
- `src/personal_agent_gateway/transcript.py`: JSONL transcript persistence and
  `active.json` restart pointer.
- `src/personal_agent_gateway/tools.py` and
  `src/personal_agent_gateway/approval.py`: local filesystem/shell tool support
  and browser approval flow used by the OpenAI provider path.
- `scripts/run_local.sh`: starts the local loopback server.
- `scripts/run_tunnel.sh`: starts a Cloudflare Quick Tunnel to the local server.

## Technical Basis

- Local-first control: the browser never talks directly to Codex or the
  filesystem. It talks to the local gateway, and the gateway invokes the local
  agent engine.
- Loopback binding: `AGENT_WEB_HOST` is restricted to `127.0.0.1` or
  `localhost` so the app is not directly exposed on the LAN.
- Tunnel ingress: Cloudflare Quick Tunnel provides a temporary public HTTPS URL
  without buying a domain or creating a named Cloudflare project.
- Token gate: the tunnel URL is not treated as secret. Every page, static file,
  and API request still requires `AGENT_WEB_TOKEN`.
- Codex execution: the default provider runs `codex exec --json` as a local
  subprocess. JSONL events are parsed and the final agent message is returned to
  the web UI.
- Codex credentials: local Codex CLI authentication/configuration is reused.
  The gateway does not pass an OpenAI API key in the default `codex` provider.
- Workspace boundary: `AGENT_WORKSPACE_ROOT` is passed as the Codex working
  directory and is also the filesystem/shell boundary for the OpenAI provider
  tool path.
- Restart persistence: transcripts are stored on disk and `active.json` points
  to the current session after a gateway restart.

## Implemented Features

- Token-protected web UI.
- Token-protected API endpoints.
- Query-token login that sets an HttpOnly `agent_web_token` cookie.
- Local transcript persistence in `AGENT_SESSION_DIR`.
- Active session restore after process restart.
- Reset action that clears the active session.
- Default local Codex provider through `codex exec --json`.
- Optional OpenAI API provider for explicit `AGENT_MODEL_PROVIDER=openai` runs.
- Browser approval flow for shell tool calls in the OpenAI provider path.
- Cloudflare Quick Tunnel script for no-domain external access.
- Sensitive value redaction for configured token/API-key values in runtime
  records.
- Unit tests for config/auth, app routing, transcript persistence, runtime
  behavior, tools, and model clients.

## Current Limitations

- Cloudflare Quick Tunnel URLs are temporary. The URL changes when the tunnel is
  restarted.
- Quick Tunnel is suitable for personal testing, not uptime guarantees or a
  stable production endpoint.
- The Codex provider currently starts a `codex exec` subprocess per request. It
  sends the web transcript as context, but it does not yet resume a Codex thread
  ID with `codex exec resume`.
- The Codex provider currently returns the final assistant message only. It does
  not stream intermediate JSONL events to the browser yet.
- Browser approval is not wired into the Codex provider. Command execution in
  that path is controlled by `AGENT_CODEX_SANDBOX` and
  `AGENT_CODEX_APPROVAL_POLICY`.
- Authentication is single shared-token auth. There are no per-user accounts,
  roles, revocation lists, or audit dashboards.
- No named Cloudflare Tunnel, custom domain, access policy, or Cloudflare Zero
  Trust login is configured in this version.

## Security model

- `AGENT_WEB_HOST` must be `127.0.0.1` or `localhost`.
- `AGENT_WEB_TOKEN` is required for all app pages, static assets, and APIs.
- Authentication accepts `?token=...`, a bearer token, or the
  `agent_web_token` cookie.
- The first successful request with `?token=...` sets an HttpOnly
  `agent_web_token` cookie.
- Filesystem tools are limited to `AGENT_WORKSPACE_ROOT`.
- Shell commands require explicit browser approval and run in
  `AGENT_WORKSPACE_ROOT`.
- Transcripts persist under `AGENT_SESSION_DIR`, but token and API key values
  should not be written to transcripts.
- With `AGENT_MODEL_PROVIDER=codex`, the gateway calls the local `codex exec`
  CLI and reuses the local Codex login/configuration.
- With `AGENT_MODEL_PROVIDER=codex`, shell approval behavior is delegated to the
  Codex CLI sandbox and approval policy.

## Environment setup

Create a virtual environment, install the project in editable mode, and create a
local environment file:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
```

Generate a strong web token:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Edit `.env` and set:

- `AGENT_WEB_HOST`: `127.0.0.1` or `localhost`.
- `AGENT_WEB_PORT`: local port. The default is `8787`.
- `AGENT_WEB_TOKEN`: strong random token generated above.
- `AGENT_WORKSPACE_ROOT`: root directory available to filesystem and shell tools.
- `AGENT_MODEL_PROVIDER`: `codex` by default. `openai` remains available for
  explicit API-backed runs.
- `AGENT_MODEL`: `default` to use the local Codex default model, or a specific
  model passed to `codex exec -m`.
- `AGENT_SESSION_DIR`: transcript/session storage directory.
- `AGENT_CODEX_BIN`: Codex CLI binary. Defaults to `codex`.
- `AGENT_CODEX_SANDBOX`: Codex sandbox mode. Defaults to `workspace-write`.
- `AGENT_CODEX_APPROVAL_POLICY`: Codex approval policy. Defaults to `never`.
- `AGENT_CODEX_TIMEOUT_SECONDS`: request timeout for local Codex execution.

Optional current-shell convenience:

```bash
source .env
```

The app loads `.env` internally, so `source .env` is not required for the app to
read it. `.env.example` uses plain `KEY=value` lines, and the run scripts use
shell expansion for host and port before Python starts. For script-visible
overrides such as `AGENT_WEB_PORT`, use inline env or auto-export the file:

```bash
AGENT_WEB_PORT=8788 scripts/run_local.sh
AGENT_WEB_PORT=8788 scripts/run_tunnel.sh
set -a; source .env; set +a
```

## Local run

After the editable install and environment setup:

```bash
scripts/run_local.sh
```

Open `http://127.0.0.1:8787/?token=<AGENT_WEB_TOKEN>` by default. If using a
different port, make sure the script sees the exported or inline
`AGENT_WEB_PORT`.

## Cloudflare Quick Tunnel run

Run the local server through the tunnel script:

```bash
scripts/run_tunnel.sh
```

The tunnel points to `http://127.0.0.1:${AGENT_WEB_PORT}`.

Cloudflare Quick Tunnel gives a generated
`https://<random>.trycloudflare.com` URL. No domain purchase is required. The URL
changes when the tunnel restarts. The web token is still required because the URL
can be accessed by anyone who knows it.

Use the generated URL with the token:

```text
https://<random>.trycloudflare.com/?token=<AGENT_WEB_TOKEN>
```

## How restart persistence works

Transcripts are stored under `AGENT_SESSION_DIR`. The active session pointer is
`active.json`, which lets the gateway resume the active session after restart.
Reset clears the active session.

## Shell approval behavior

For `AGENT_MODEL_PROVIDER=openai`, shell tool calls still use the browser
approval flow.

For `AGENT_MODEL_PROVIDER=codex`, command execution is handled by local
`codex exec` using `AGENT_CODEX_SANDBOX` and `AGENT_CODEX_APPROVAL_POLICY`.

## Troubleshooting

- If the server refuses to start, confirm `AGENT_WEB_HOST` is `127.0.0.1` or
  `localhost`.
- If the browser shows an auth error, pass `?token=<AGENT_WEB_TOKEN>`, send a
  bearer token, or clear the `agent_web_token` cookie and authenticate again.
- If port `8787` is busy, use an exported or inline `AGENT_WEB_PORT` for the
  run script.
- If the tunnel URL stops working, restart `scripts/run_tunnel.sh` and use the
  new `https://<random>.trycloudflare.com` URL.
- If files are missing from tool access, check that they are under
  `AGENT_WORKSPACE_ROOT`.
- If session state is stale, use reset to clear the active session pointer.
