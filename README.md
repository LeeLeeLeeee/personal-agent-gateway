# Personal Agent Web Gateway

## What this is

Personal Agent Web Gateway is a local-first web gateway for operating a personal
agent from a browser. It serves the app on a loopback host, protects every app
page, static asset, and API with a shared web token, and runs agent filesystem and
shell work inside the configured workspace root.

## What this is not

This is not a public multi-user service, an unauthenticated agent endpoint, or a
general remote shell. It does not remove the need to protect the web token,
OpenAI API key, local machine, workspace files, or tunnel URL.

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
- `AGENT_MODEL_PROVIDER`: model provider.
- `AGENT_MODEL`: model name.
- `AGENT_SESSION_DIR`: transcript/session storage directory.
- `OPENAI_API_KEY`: API key for the configured provider when required.

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

Shell commands do not run automatically. The browser must explicitly approve each
shell command before execution. Approved commands run in `AGENT_WORKSPACE_ROOT`.

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
