# Personal Agent Gateway UI/UX Brief

## One-Line Product Definition

Personal Agent Gateway is a private local agent control console: a single user can access their local Codex CLI, local tools, scheduled jobs, captures, media processing, and generated artifacts through a secure browser UI.

## Audience

The user is the machine owner. They are technical enough to understand workspace, shell commands, ffmpeg, cron, and local files, but the UI should reduce memory load and make local actions visible before they run.

Do not design this as a public SaaS, team product, or marketing site.

## Design Goal

Make the app feel like a calm developer operations console for a personal local agent.

The user should always understand:

- Am I authenticated?
- What workspace is the agent using?
- Is the agent running something right now?
- What command or local action is waiting for my approval?
- What jobs have run?
- What artifacts were created?
- Where can I inspect, reuse, download, or attach those artifacts?

## Product Areas

### 1. Auth

Design a single-user OTP-first auth flow. The normal browser login should use a Google Authenticator-compatible 6-digit code, not a required token field.

Required states:

- First-time OTP setup screen with QR code and verification code input.
- Login screen with 6-digit OTP input.
- Successful login state.
- Invalid OTP error.
- OTP not configured state.
- Cookie/session expired state.
- Logout action.
- Recovery-code flow for lost authenticator device.
- Subtle security note: do not share the tunnel URL, setup token, recovery codes, or local auth files.

Behavior:

- If the user enters through `?token=...` for setup or recovery, the app should remove the token from the visible URL after validation.
- Token input should not be part of the default login screen.
- Higher security options such as token + OTP may be exposed later in Settings, but should not be the default UX.
- After login, the user lands in the app shell, not a marketing page.

### 2. App Shell

Recommended layout:

- Left sidebar for navigation.
- Top status bar.
- Main content area.
- Optional right drawer for current job, approval, logs, or artifact preview.

Navigation:

- Chat
- Jobs
- Schedules
- Capabilities
- Artifacts
- Settings

Top status bar should show:

- Workspace
- Provider/model
- Session status
- Running jobs count
- Pending approvals count
- Tunnel/local status if available

The visual style should be dense, quiet, and tool-like. Avoid landing-page hero sections, oversized cards, and marketing copy.

### 3. Chat

Chat is the main conversational control surface, but it should not hide execution details.

The chat page should include:

- Session list or compact session switcher.
- Message transcript.
- Composer.
- Agent activity timeline.
- Pending approval panel.
- Recent artifacts from the current session.

Message types:

- User message.
- Assistant response.
- Agent activity.
- Tool/job proposal.
- Approval request.
- Error state.

Important interaction:

When the agent wants to run a local action, show a job proposal:

```text
Capability: ffmpeg.extract-audio
Input: ./videos/demo.mov
Output: ./data/artifacts/audio/demo.m4a
Risk: Medium
Command preview: ffmpeg ...
```

Then provide:

- Approve
- Deny
- Edit options
- View details

### 4. Jobs

Jobs are the execution history and current activity center.

Job list should support:

- Status filter: draft, waiting approval, running, succeeded, failed, canceled.
- Source filter: chat, manual, schedule.
- Capability filter.
- Search by title, command, output, artifact.

Job detail should show:

- Title and status.
- Capability.
- Source session or schedule.
- Input summary.
- Command preview.
- Approval decision.
- Live or recent logs.
- Artifacts created.
- Retry or run again action when safe.

Statuses should be visually distinct but not noisy:

- Waiting approval
- Running
- Succeeded
- Failed
- Canceled

### 5. Schedules

Schedules are recurring job templates. Do not expose only raw cron strings.

Schedule list should show:

- Name.
- Capability.
- Human-readable schedule.
- Raw cron expression.
- Enabled/paused state.
- Last run status.
- Next run time.

Schedule detail/create flow should include:

- Select capability.
- Configure inputs.
- Pick frequency using friendly controls.
- Show generated cron expression.
- Show approval/security policy.
- Preview the job that will run.

Examples:

- Every day at 09:00, compress videos in a folder.
- Every Friday at 18:00, generate a workspace summary.
- Every 30 minutes, capture a browser page.

### 6. Capabilities

Capabilities are the user's map of what the gateway can do.

Capability page should show grouped tool cards or rows:

- Agent
- Files
- Shell
- Media / ffmpeg
- Capture
- Scheduling
- Reports

Each capability should show:

- Name.
- Short description.
- Risk level.
- Required inputs.
- Output artifact type.
- Whether approval is required.
- Run manually action when applicable.

This page should make the system feel understandable rather than magical.

### 7. Artifacts and Storage

Artifacts are first-class outputs. This is the storage area for captures, videos, audio, logs, reports, and generated files.

Artifact list should support:

- Recent view.
- Type filters: images, videos, audio, logs, reports, archives.
- Search.
- Tags or metadata later.
- Source job/session links.

Artifact detail should show:

- Preview.
- Metadata.
- File path.
- Size and MIME type.
- Created time.
- Source job.
- Source session.
- Download action.
- Copy path action.
- Attach to current chat action.
- Ask agent about this artifact action.

Image viewer:

- Large preview.
- Zoom in/out.
- Fit to screen.
- Open/download.
- Attach to chat.

Video/audio viewer:

- Browser playback.
- Download.
- Source job link.
- Related converted versions.

Log/report viewer:

- Monospace readable content.
- Copy.
- Download.
- Link back to job.

### 8. Capture UX

Capture should be treated as a capability that creates image artifacts.

Capture entry points:

- From Chat: "capture screen and analyze it".
- From Capabilities: manual capture tools.
- From a Schedule: repeat browser/page captures.

Capture types:

- Full screen capture.
- Window capture.
- Browser page capture.
- Region capture later.

After capture:

- Save image as artifact.
- Show thumbnail in Chat and Artifacts.
- Let user attach it to the conversation.
- Let user ask the agent to analyze it.

### 9. ffmpeg UX

Media tools should be understandable without requiring the user to write ffmpeg commands.

Common flows:

- Inspect media metadata.
- Convert video format.
- Compress video.
- Extract audio.
- Create thumbnail.
- Batch process folder later.

Each flow should show:

- Input file.
- Output file.
- Options.
- Estimated action summary.
- Command preview.
- Approval button.
- Result artifact.

The command preview is important because this is a local machine tool.

## Recommended Screens

### Login

Purpose: authenticate quickly and safely.

Content:

- Product name.
- 6-digit OTP input.
- Continue button.
- Small security note.
- Error state.

First-time setup content:

- QR code for Google Authenticator.
- Manual setup key fallback.
- Verification code input.
- Recovery codes shown once after successful setup.

### Chat

Purpose: talk to the local agent and approve proposed work.

Layout:

- Session rail or compact session drawer.
- Main transcript.
- Activity timeline integrated between messages.
- Composer fixed at bottom.
- Pending approval panel near composer or right drawer.

### Jobs

Purpose: know what happened, what is running, and what failed.

Layout:

- Filterable table/list.
- Status badges.
- Right detail drawer or detail route.

### Schedules

Purpose: manage repeated local tasks.

Layout:

- Schedule list.
- Create schedule wizard or form.
- Human-readable frequency controls.
- Run history.

### Capabilities

Purpose: understand and manually start available tools.

Layout:

- Category sections.
- Capability rows/cards.
- Risk and approval indicators.

### Artifacts

Purpose: browse and reuse generated local outputs.

Layout:

- Type filters.
- Grid/list toggle.
- Preview drawer/detail route.
- Strong viewer for image/video/audio/log/report.

### Settings

Purpose: inspect local configuration.

Content:

- Workspace root.
- Artifact root.
- Provider/model.
- Auth status.
- ffmpeg path.
- Capture availability.
- Scheduler status.
- Security settings.

## Tone and Visual Direction

Use a restrained developer-console tone:

- Clear hierarchy.
- Compact controls.
- Readable tables/lists.
- Stable composer and approval panel.
- No marketing hero.
- No decorative gradients or oversized product cards.
- Emphasize status, provenance, and user control.

The app should feel trustworthy because it explains what it is doing.

## Key UX Principle

Chat creates intent. Jobs execute work. Artifacts preserve results.

Do not let local execution disappear inside chat bubbles. Every local action should become a visible job, and every output worth keeping should become an artifact.
