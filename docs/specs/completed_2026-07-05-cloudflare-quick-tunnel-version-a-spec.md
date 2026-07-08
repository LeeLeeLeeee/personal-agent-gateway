# Personal Agent Web Gateway Version A Spec

## Status

Ready for user review.

## Objective

Discord나 Hermes gateway를 경유하지 않고, 외부 브라우저에서 개인용 웹 페이지로 Mac의 로컬 agent 실행 엔진을 사용할 수 있게 한다.

초기 버전은 개인 단일 사용자용이다. 도메인은 구매하지 않고 Cloudflare Quick Tunnel의 임시 `trycloudflare.com` URL을 사용한다.

## Scope

### Included

- Local web app bound only to `127.0.0.1`.
- Cloudflare Quick Tunnel exposing the local web app through a temporary HTTPS URL.
- Single-user chat UI.
- Server-side access token check for every page and API request.
- One active conversation session at a time.
- Conversation transcript persists across backend restarts.
- Minimal custom agent runtime.
- Basic response streaming or near-streaming delivery.
- Explicit approval UI for local shell commands.
- Small filesystem tool surface limited to the configured workspace root.
- Minimal run documentation for local web app and tunnel startup.

### Excluded

- Custom domain.
- Cloudflare Access policy.
- Multi-user accounts.
- User-specific permissions.
- Persistent audit dashboard.
- Public registration or invitation flow.
- Direct public exposure of any local agent/runtime port.
- Hermes gateway integration.
- Full autonomous browser control.
- Background task scheduling.
- Multi-agent orchestration.
- Arbitrary filesystem writes by the agent.

## Assumptions

- The web app and agent runtime run on the same Mac.
- The only intended user is the Mac owner.
- The temporary Cloudflare Quick Tunnel URL may change on every tunnel restart.
- The web app is the only service exposed through the tunnel.
- Model provider credentials and local tool state remain on the Mac.
- Conversation transcript is stored locally on the Mac.
- The app must not read or display secret files unless explicitly allowed by the runtime policy.

## Recommended Approach

Build a standalone web gateway with a minimal local agent runtime.

```text
External browser
 -> Cloudflare Quick Tunnel
 -> 127.0.0.1:8787 web app
 -> local agent runtime
 -> approved Mac tools/filesystem/shell actions
```

This keeps the public entrypoint and local execution engine under our control. Hermes may be used as a reference for patterns, but it is not part of the runtime path.

## Alternative Approaches

### Hermes Runtime Reuse

Use Hermes as the local agent runtime behind the web gateway.

Pros:
- Existing local tool, approval, and session behavior.
- Less runtime code to write.

Cons:
- Couples the product to Hermes internals.
- Makes the execution boundary less clear.
- Does not match the goal of owning the agent engine.

### Tailscale-Only Local Web UI

Expose the web app only inside a Tailscale private network.

Pros:
- Smaller internet attack surface.
- No public temporary URL.

Cons:
- Requires Tailscale on every client device.
- Does not match the chosen Cloudflare Quick Tunnel direction.

## Architecture

### Components

#### Web UI

Responsibilities:
- Render chat history.
- Accept user input.
- Show assistant responses.
- Show pending shell approval prompts.
- Show connection and error state.
- Require token before any app content is visible.

#### Web Backend

Responsibilities:
- Bind to `127.0.0.1:8787`.
- Serve the UI.
- Validate access token on all routes.
- Forward user messages to the local agent runtime.
- Stream or poll agent responses back to the browser.
- Keep one active session state for Version A.
- Restore the last active transcript after backend restart.
- Never expose raw runtime internals directly to the browser.

#### Local Agent Runtime

Responsibilities:
- Maintain the active conversation state.
- Append user messages, assistant messages, tool requests, approvals, and tool results to local transcript storage.
- Call the configured model provider.
- Decide when to request a local tool action.
- Request approval before shell execution.
- Execute only approved local tools.
- Return text responses and tool results to the backend.
- Keep execution local-only.

Initial tool surface:
- `shell.run`: run a command after explicit user approval.
- `fs.read`: read files under the configured workspace root.
- `fs.list`: list files under the configured workspace root.

Excluded from Version A:
- Unapproved shell execution.
- Direct access outside the configured workspace root.
- Long-running background process management.
- Arbitrary filesystem writes.

#### Cloudflare Quick Tunnel

Responsibilities:
- Create a temporary public HTTPS URL.
- Forward HTTPS traffic to `http://127.0.0.1:8787`.
- Avoid opening inbound router/firewall ports.

Command shape:

```bash
cloudflared tunnel --url http://127.0.0.1:8787
```

## Security Model

Version A has no Cloudflare Access because there is no purchased domain. The app must therefore enforce its own authentication.

### Required Controls

- Web app binds only to `127.0.0.1`, never `0.0.0.0`.
- A strong random access token is required.
- Token is checked on:
  - HTML entry route.
  - API routes.
  - Streaming routes.
  - Approval routes.
- Token must not be committed to git.
- Token must be loaded from environment or a local ignored config file.
- Transcript storage must remain local and must not include access tokens.
- No local runtime port is exposed through Cloudflare directly.
- Shell execution always requires one-time approval.
- Filesystem access is restricted to the configured workspace root.
- Filesystem path checks must use path resolution, not string prefix checks.

### Recommended Token Behavior

- First request may use `?token=...`.
- Backend sets an `HttpOnly`, `SameSite=Strict`, secure cookie.
- Later requests use the cookie.
- API requests may also accept `Authorization: Bearer ...` for simple clients.

### Residual Risks

- The temporary URL is public while the tunnel is running.
- Anyone with the URL can attempt authentication.
- If the web token leaks, the attacker may send prompts to the local agent UI.
- Shell approval mistakes can execute local commands on the Mac.
- Filesystem read scope must be implemented carefully to avoid path traversal.
- Local transcripts may contain sensitive prompts, file excerpts, command output, and model responses.

## Data Flow

### Normal Chat

```text
1. Browser loads temporary Cloudflare URL.
2. Backend validates token.
3. User submits message.
4. Backend sends message to the local agent runtime.
5. Runtime calls the model provider and approved tools.
6. Backend returns streamed or final response.
7. UI appends response to chat.
```

### Shell Approval Flow

```text
1. Agent runtime requests approval for a shell command.
2. Backend records pending approval for the active session.
3. UI renders approve/deny controls.
4. User selects an approval decision.
5. Backend validates token again.
6. Runtime executes or aborts the command.
7. Runtime reports the result or denial back into the conversation.
```

Commands must be displayed exactly before approval. Approval applies to one command only in Version A.

## Session Model

Version A starts with one active session.

Rules:
- A browser reload should keep the active session if the backend process is still running.
- A backend restart restores the last active transcript.
- `/new` or reset can be represented as a clear-session action after the basic chat loop works.
- Reset does not delete transcript files by default; it starts a new active transcript.

## Transcript Persistence

Version A persists the conversation transcript locally so a backend restart does not lose chat context.

Persisted event types:
- User message.
- Assistant message.
- Tool request.
- Approval decision.
- Tool result or denial.
- Runtime error shown to the user.

Storage requirements:
- Stored under `AGENT_SESSION_DIR`.
- Local-only file or database.
- Append-only event format preferred for easier recovery.
- Access token and cookie values must never be written.
- Tool command text and command output may be written because they are needed for auditability.

## Configuration

Required local configuration:

```text
AGENT_WEB_HOST=127.0.0.1
AGENT_WEB_PORT=8787
AGENT_WEB_TOKEN=<strong random token>
AGENT_WORKSPACE_ROOT=/Users/iyeonghyeon/works
AGENT_MODEL_PROVIDER=codex
AGENT_MODEL=default
AGENT_SESSION_DIR=<path>
AGENT_CODEX_BIN=codex
AGENT_CODEX_SANDBOX=workspace-write
AGENT_CODEX_APPROVAL_POLICY=never
AGENT_CODEX_TIMEOUT_SECONDS=600
```

Optional later configuration:

```text
AGENT_WEB_LOG_LEVEL=info
AGENT_WEB_ALLOWED_ORIGIN=<trycloudflare origin>
```

## Failure Handling

- Missing token: return `401`.
- Invalid token: return `403`.
- Model provider unavailable: show a clear provider error.
- Active run in progress: reject with a busy state in Version A.
- Tunnel disconnected: local app remains running; user restarts `cloudflared`.
- Response stream interrupted: UI shows partial response and a retry option.
- Denied shell approval: runtime reports the denial to the model and asks it to continue without that command.
- Filesystem path outside workspace: reject the tool call and record the rejection.
- Transcript storage unavailable: reject new chat requests and show a storage error.

## Testing Criteria

### Local

- App starts on `127.0.0.1:8787`.
- App rejects unauthenticated page requests.
- App rejects unauthenticated API requests.
- App accepts a valid token.
- A chat message reaches the local agent runtime and returns a response.
- A second message continues the same session.
- A backend restart restores the previous transcript.
- The app does not bind to `0.0.0.0`.
- A shell command pauses for approval before execution.
- A denied shell command is not executed.
- A filesystem read outside the workspace root is rejected.

### Tunnel

- `cloudflared tunnel --url http://127.0.0.1:8787` creates a public URL.
- The public URL requires the app token.
- A valid token allows chat from an external browser.
- Restarting the tunnel changes or may change the URL without breaking the local app.

### Security

- Secrets are not committed.
- No agent runtime port is directly exposed.
- Shell execution requires explicit approval.
- Filesystem access is contained to the workspace root.
- Transcript storage does not contain web access tokens.

## Implementation Plan Boundary

After this spec is approved, write a separate implementation plan. The plan should include:

- Exact model provider integration method.
- Agent loop design.
- Tool call format.
- File layout.
- Transcript storage format.
- Local run commands.
- Verification commands.

No application code should be written before the implementation plan is approved.

## Fixed Decisions For Version A

- Runtime stack: Python FastAPI backend with a minimal static web UI.
- Agent runtime: custom minimal local runtime owned by this project.
- Model integration: provider adapter selected during implementation planning.
- Streaming transport: Server-Sent Events if model integration supports incremental output; otherwise final-response polling for the first implementation.
- Session count: one active session.
- Transcript persistence: enabled in Version A and restored after backend restart.
- Auth: app-owned strong token with cookie after first validation.
- Tunnel mode: Cloudflare Quick Tunnel only.
- Dangerous actions: shell commands require one-time approval; unsupported dangerous tools are blocked.
