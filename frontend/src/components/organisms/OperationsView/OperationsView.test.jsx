import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../../api/client.js";
import { OperationsView } from "./index.jsx";

const data = {
  intake_open: true,
  access_mode: "restricted",
  diagnostics: {
    bind_host: "127.0.0.1",
    cookie_secure: false,
    tunnel_mode: "not_reported",
    workspace_writable: true
  },
  health: [
    { name: "database", ready: true, detail: "ready" },
    { name: "worker", ready: false, detail: "not running" }
  ],
  items: [
    {
      domain: "team_run",
      id: "r1",
      title: "Ship safely",
      status: "interrupted",
      resumable: true,
      retryable: false,
      target: { screen: "teams", team_run_id: "r1" }
    },
    {
      domain: "job",
      id: "j1",
      title: "Inspect",
      status: "failed",
      resumable: false,
      retryable: true,
      target: { screen: "jobs", job_id: "j1" }
    }
  ],
  backups: [
    {
      id: "backup-1",
      created_at: "2026-07-15T00:00:00Z",
      schema_version: 2,
      database_size_bytes: 1024,
      profile: "database-only",
      recoverability: {
        database: "included",
        auth: "metadata-only",
        hook_secrets: "reference-only"
      }
    }
  ]
};

function props(overrides = {}) {
  return {
    data,
    loading: false,
    error: null,
    onRefresh: vi.fn(),
    onEmergencyStop: vi.fn(),
    onResumeIntake: vi.fn(),
    onCreateBackup: vi.fn(),
    onVerifyBackup: vi.fn(),
    onOpenTarget: vi.fn(),
    onResumeItem: vi.fn(),
    onRetryItem: vi.fn(),
    onRelogin: vi.fn(),
    ...overrides
  };
}

describe("OperationsView", () => {
  it("shows component health and preserves domain deep-link actions", async () => {
    const viewProps = props();
    render(<OperationsView {...viewProps} />);

    expect(screen.getByText(/RESTRICTED/)).toBeInTheDocument();
    expect(screen.getByText(/COOKIE · INSECURE/)).toBeInTheDocument();
    expect(screen.getByText(/TUNNEL · NOT REPORTED/)).toBeInTheDocument();
    expect(screen.getByText(/WORKSPACE WRITE · AVAILABLE/)).toBeInTheDocument();
    expect(screen.getByText("not running")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /open Ship safely/i }));
    await userEvent.click(screen.getByRole("button", { name: /resume Ship safely/i }));
    await userEvent.click(screen.getByRole("button", { name: /retry Inspect/i }));

    expect(viewProps.onOpenTarget).toHaveBeenCalledWith(data.items[0].target);
    expect(viewProps.onResumeItem).toHaveBeenCalledWith(data.items[0]);
    expect(viewProps.onRetryItem).toHaveBeenCalledWith(data.items[1]);
  });

  it("confirms emergency stop and exposes backup actions", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const viewProps = props();
    render(<OperationsView {...viewProps} />);

    await userEvent.click(screen.getByRole("button", { name: /emergency stop/i }));
    await userEvent.click(screen.getByRole("button", { name: /create backup/i }));
    await userEvent.click(screen.getByRole("button", { name: /verify backup-1/i }));

    expect(screen.getByText(/database-only/)).toBeInTheDocument();
    expect(screen.getByText(/Not fully recoverable.*auth: metadata-only/)).toBeInTheDocument();
    expect(viewProps.onEmergencyStop).toHaveBeenCalledTimes(1);
    expect(viewProps.onCreateBackup).toHaveBeenCalledTimes(1);
    expect(viewProps.onVerifyBackup).toHaveBeenCalledWith("backup-1");
  });

  it("keeps error detail, recovery action, and correlation id visible", async () => {
    const writeText = vi.fn();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText }
    });
    const error = new ApiError({
      status: 500,
      code: "internal_error",
      detail: "Internal Server Error",
      retryable: true,
      correlationId: "corr-500"
    });
    const viewProps = props({ data: null, error });
    render(<OperationsView {...viewProps} />);

    expect(screen.getByText("Internal Server Error")).toBeInTheDocument();
    expect(screen.getByText(/Existing local data was not cleared/)).toBeInTheDocument();
    expect(screen.getByText(/corr-500/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /copy correlation id/i }));
    expect(writeText).toHaveBeenCalledWith("corr-500");
    await userEvent.click(screen.getByRole("button", { name: /retry request/i }));
    expect(viewProps.onRefresh).toHaveBeenCalledTimes(1);
  });
});
