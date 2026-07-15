import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SettingsView } from "./index.jsx";

const settings = {
  workspace_root: "C:/ws", artifact_root: "./data/artifacts", session_dir: "./data/sessions", temp_dir: "./data/temp",
  provider: "codex", model: "default", totp_configured: true,
  ffmpeg_binary: "ffmpeg", ffprobe_binary: "ffprobe", capture_binary: "", job_worker_concurrency: 2,
  effective_job_concurrency: 1,
  codex_sandbox: "workspace-write", codex_approval_policy: "never", codex_timeout_seconds: 120, cookie_secure: false,
  session_authenticated: true, bind_host: "127.0.0.1", tunnel_mode: "not_reported",
  worker_alive: true, scheduler_alive: false, automation_ready: false,
  automation_unavailable_reason: "Scheduler is not running",
  team_review_supported: false, team_execution_mode: "sequential",
  agent_availability: [
    { id: "codex", available: true, error: null },
    { id: "claude", available: false, error: "not found" }
  ],
  access_mode: "restricted", workspace_writable: true,
  audit_enabled: true, audit_retention_days: 90, schema_version: 2
};

const authSessions = [
  { id: "current-session", current: true, last_seen_at: "2026-07-15T01:00:00Z", idle_expires_at: "2026-07-15T02:00:00Z" },
  { id: "other-session", current: false, last_seen_at: "2026-07-15T00:30:00Z", idle_expires_at: "2026-07-15T01:30:00Z" }
];

describe("SettingsView", () => {
  it("renders grouped read-only values from the backend", () => {
    render(<SettingsView settings={settings} />);
    expect(screen.getByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getByText("Workspace")).toBeInTheDocument();
    expect(screen.getByText("C:/ws")).toBeInTheDocument();
    expect(screen.getByText("AUTHENTICATED")).toBeInTheDocument();
    expect(screen.getByText("NOT REPORTED")).toBeInTheDocument();
    expect(screen.getByText("SEQUENTIAL")).toBeInTheDocument();
    expect(screen.getByText("Scheduler is not running")).toBeInTheDocument();
    expect(screen.getByText("CODEX CLI")).toBeInTheDocument();
    expect(screen.getAllByText("AVAILABLE").length).toBeGreaterThan(0);
  });

  it("omits rows whose value is missing", () => {
    render(<SettingsView settings={{ ...settings, capture_binary: "" }} />);
    expect(screen.queryByText(/CAPTURE BINARY/i)).not.toBeInTheDocument();
  });

  it("distinguishes sessions and confirms full access before changing mode", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const onAccessModeChange = vi.fn();
    const onRevokeSession = vi.fn();
    render(
      <SettingsView
        settings={settings}
        authSessions={authSessions}
        onAccessModeChange={onAccessModeChange}
        onRevokeSession={onRevokeSession}
        onRevokeAllSessions={vi.fn()}
      />
    );

    expect(screen.getByText("CURRENT")).toBeInTheDocument();
    expect(screen.getByText("other-session")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /enable full access/i }));
    await userEvent.click(screen.getByRole("button", { name: /revoke other-session/i }));

    expect(onAccessModeChange).toHaveBeenCalledWith("full_access", true);
    expect(onRevokeSession).toHaveBeenCalledWith("other-session", false);
  });
});
