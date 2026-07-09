import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SettingsView } from "./index.jsx";

const settings = {
  workspace_root: "C:/ws", artifact_root: "./data/artifacts", session_dir: "./data/sessions", temp_dir: "./data/temp",
  provider: "codex", model: "default", totp_configured: true,
  ffmpeg_binary: "ffmpeg", ffprobe_binary: "ffprobe", capture_binary: "", job_worker_concurrency: 2,
  codex_sandbox: "workspace-write", codex_approval_policy: "never", codex_timeout_seconds: 120, cookie_secure: false
};

describe("SettingsView", () => {
  it("renders grouped read-only values from the backend", () => {
    render(<SettingsView settings={settings} />);
    expect(screen.getByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getByText("Workspace")).toBeInTheDocument();
    expect(screen.getByText("C:/ws")).toBeInTheDocument();
    expect(screen.getByText("AUTHENTICATED")).toBeInTheDocument(); // derived from totp_configured
    expect(screen.getByText("LOCAL ONLY")).toBeInTheDocument();    // static Security row
  });

  it("omits rows whose value is missing", () => {
    render(<SettingsView settings={{ ...settings, capture_binary: "" }} />);
    expect(screen.queryByText(/CAPTURE BINARY/i)).not.toBeInTheDocument();
  });
});
