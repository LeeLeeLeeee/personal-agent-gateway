import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DashboardView } from "./index.jsx";

function jsonResponse(body) {
  return Promise.resolve({
    ok: true,
    status: 200,
    headers: { get: () => null },
    json: () => Promise.resolve(body)
  });
}

const completeReport = {
  detected_at: "2026-07-22T00:00:00Z",
  providers: [
    {
      provider: "codex",
      label: "Codex",
      available: true,
      availability_error: null,
      version: "1.2.3",
      model: "gpt-5",
      weekly_limit: 1000,
      used: 600,
      remaining: 400,
      reset_at: "2026-07-27T00:00:00Z",
      usage_status: "ok",
      usage_source: "local",
      note: null
    }
  ]
};

const operationsPayload = {
  intake_open: true,
  access_mode: "restricted",
  diagnostics: { workspace_writable: true },
  health: [
    { name: "worker", ready: true, detail: "ready" },
    { name: "scheduler", ready: false, detail: "not running" }
  ],
  items: [
    {
      id: "run-1",
      domain: "team_run",
      title: "Release dashboard",
      status: "running",
      updated_at: "2026-07-22T09:00:00Z",
      target: { screen: "teams", team_run_id: "run-1" }
    },
    {
      id: "job-1",
      domain: "job",
      title: "Retry export",
      status: "failed",
      updated_at: "2026-07-22T10:00:00Z",
      retryable: true,
      target: { screen: "jobs", job_id: "job-1" }
    }
  ]
};

describe("DashboardView", () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });

  it("calls the dashboard usage API and renders provider usage as a card and gauge", async () => {
    fetch
      .mockResolvedValueOnce(await jsonResponse(completeReport))
      .mockResolvedValueOnce(await jsonResponse(operationsPayload));

    render(<DashboardView />);

    expect(screen.getByText("사용량을 불러오는 중입니다.")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Codex" })).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith("/api/dashboard/usage");
    expect(fetch).toHaveBeenCalledWith("/api/operations");
    expect(screen.getByText("1,000")).toBeInTheDocument();
    expect(screen.getByText("400")).toBeInTheDocument();
    expect(screen.getByText("600 / 1,000 (60%)")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Codex 주간 사용량" })).toHaveAttribute(
      "aria-valuenow",
      "600"
    );
  });

  it("shows uncollected and unavailable providers without inventing a gauge", async () => {
    fetch.mockResolvedValueOnce(await jsonResponse({
      detected_at: "2026-07-22T00:00:00Z",
      providers: [
        {
          provider: "codex",
          label: "Codex",
          available: true,
          version: "1.2.3",
          model: "gpt-5",
          weekly_limit: null,
          used: null,
          remaining: null,
          reset_at: null,
          usage_status: "unconfirmed",
          note: "확정된 사용량 소스가 없습니다."
        },
        {
          provider: "claude",
          label: "Claude",
          available: false,
          availability_error: "not found",
          version: "",
          model: "",
          weekly_limit: null,
          used: null,
          remaining: null,
          reset_at: null,
          usage_status: "unavailable",
          note: "not found"
        }
      ]
    })).mockResolvedValueOnce(await jsonResponse(operationsPayload));

    render(<DashboardView />);

    expect(await screen.findByText("사용량 데이터가 아직 수집되지 않았습니다.")).toBeInTheDocument();
    expect(screen.getByText("확정된 사용량 소스가 없습니다.")).toBeInTheDocument();
    expect(screen.getByText("이 에이전트는 현재 실행할 수 없습니다.")).toBeInTheDocument();
    expect(screen.getByText("not found")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("shows an error and retries the API request", async () => {
    fetch
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(await jsonResponse(operationsPayload))
      .mockResolvedValueOnce(await jsonResponse(completeReport));

    render(<DashboardView />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("사용량을 불러오지 못했습니다.");
    expect(alert).toHaveTextContent("Network request failed");

    await userEvent.click(screen.getByRole("button", { name: "다시 시도" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
    expect(await screen.findByRole("heading", { name: "Codex" })).toBeInTheDocument();
  });

  it("shows a clear empty state when no local agents are returned", async () => {
    fetch
      .mockResolvedValueOnce(await jsonResponse({ detected_at: "2026-07-22T00:00:00Z", providers: [] }))
      .mockResolvedValueOnce(await jsonResponse(operationsPayload));

    render(<DashboardView />);

    expect(await screen.findByText("표시할 로컬 에이전트가 없습니다.")).toBeInTheDocument();
  });

  it("renders active work, system status, and attention items from operations separately from usage", async () => {
    const onOpenTarget = vi.fn();
    fetch
      .mockResolvedValueOnce(await jsonResponse(completeReport))
      .mockResolvedValueOnce(await jsonResponse(operationsPayload));

    render(<DashboardView onOpenTarget={onOpenTarget} />);

    expect(await screen.findByRole("heading", { name: "운영 현황" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Release dashboard" })).toBeInTheDocument();
    expect(screen.getByText("scheduler 상태를 확인하세요.")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Retry export 상세 열기" }));
    expect(onOpenTarget).toHaveBeenCalledWith({ screen: "jobs", job_id: "job-1" });
  });

  it("keeps usage visible when operations fails and retries only operations", async () => {
    fetch
      .mockResolvedValueOnce(await jsonResponse(completeReport))
      .mockRejectedValueOnce(new Error("operations offline"))
      .mockResolvedValueOnce(await jsonResponse(operationsPayload));

    render(<DashboardView />);

    expect(await screen.findByRole("heading", { name: "Codex" })).toBeInTheDocument();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("운영 현황을 불러오지 못했습니다.");

    await userEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
    expect(await screen.findByRole("heading", { name: "Release dashboard" })).toBeInTheDocument();
  });

  it("shows explicit empty states when operations has no current data", async () => {
    fetch
      .mockResolvedValueOnce(await jsonResponse(completeReport))
      .mockResolvedValueOnce(await jsonResponse({
        intake_open: true,
        diagnostics: { workspace_writable: true },
        health: [],
        items: []
      }));

    render(<DashboardView />);

    expect(await screen.findByText("현재 진행 중인 작업이 없습니다.")).toBeInTheDocument();
    expect(screen.getByText("시스템 상태 정보가 없습니다.")).toBeInTheDocument();
    expect(screen.getByText("조치가 필요한 항목이 없습니다.")).toBeInTheDocument();
  });
});
