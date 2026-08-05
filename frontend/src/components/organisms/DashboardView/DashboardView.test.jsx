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

function readySessions(sessions = []) {
  return { sessions, lmg: { status: "ready", message: null } };
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
      rate_limits: [
        { window_minutes: 300, used_percent: 60, resets_at: "2026-07-27T00:00:00Z" }
      ],
      usage_status: "ok",
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
  beforeEach(async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(await jsonResponse(readySessions()));
  });

  it("calls the dashboard usage API and renders provider usage as a card and gauge", async () => {
    fetch
      .mockResolvedValueOnce(await jsonResponse(completeReport))
      .mockResolvedValueOnce(await jsonResponse(operationsPayload));

    render(<DashboardView />);

    expect(screen.getByText("계정 한도를 불러오는 중입니다.")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Codex" })).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith("/api/dashboard/usage");
    expect(fetch).toHaveBeenCalledWith("/api/operations");
    expect(screen.getByText("계정 전체 한도")).toBeInTheDocument();
    expect(screen.getByText("5시간 · 60%", { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Codex 5시간 한도" })).toHaveAttribute(
      "aria-valuenow",
      "60"
    );
  });

  it("renders each collected account-limit window without calling it local run usage", async () => {
    fetch
      .mockResolvedValueOnce(await jsonResponse({
        detected_at: "2026-07-27T00:00:00Z",
        providers: [
          {
            provider: "codex",
            label: "Codex",
            available: true,
            availability_error: null,
            version: "1.2.3",
            model: "gpt-5",
            usage_status: "ok",
            note: null,
            rate_limits: [
              { window_minutes: 300, used_percent: 25, resets_at: "2026-07-27T04:00:00Z" },
              { window_minutes: 10080, used_percent: 41, resets_at: "2026-08-02T09:00:00Z" },
              { window_minutes: 60, used_percent: 10, resets_at: "2026-07-27T01:00:00Z" }
            ]
          }
        ]
      }))
      .mockResolvedValueOnce(await jsonResponse(operationsPayload));

    render(<DashboardView />);

    const fiveHourLimit = await screen.findByRole("progressbar", { name: "Codex 5시간 한도" });
    expect(fiveHourLimit).toHaveAttribute(
      "aria-valuenow",
      "25"
    );
    expect(fiveHourLimit).toHaveAttribute("aria-valuemin", "0");
    expect(fiveHourLimit).toHaveAttribute("aria-valuemax", "100");
    expect(screen.getByRole("progressbar", { name: "Codex 7일 한도" })).toHaveAttribute(
      "aria-valuenow",
      "41"
    );
    expect(screen.getByRole("progressbar", { name: "Codex 60분 한도" })).toHaveAttribute(
      "aria-valuenow",
      "10"
    );
    expect(screen.getByText("계정 전체 한도")).toBeInTheDocument();
    expect(screen.queryByText("로컬 에이전트의 주간 사용량을 한눈에 확인합니다.")).not.toBeInTheDocument();
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
          rate_limits: [],
          usage_status: "unconfirmed",
          note: "확정된 계정 한도 소스가 없습니다."
        },
        {
          provider: "claude",
          label: "Claude",
          available: false,
          availability_error: "not found",
          version: "",
          model: "",
          rate_limits: [],
          usage_status: "unavailable",
          note: "not found"
        }
      ]
    })).mockResolvedValueOnce(await jsonResponse(operationsPayload));

    render(<DashboardView />);

    expect(await screen.findByText("계정 한도를 수집하지 못했습니다.")).toBeInTheDocument();
    expect(screen.getByText("확정된 계정 한도 소스가 없습니다.")).toBeInTheDocument();
    expect(screen.getByText("이 에이전트는 현재 실행할 수 없습니다.")).toBeInTheDocument();
    expect(screen.getByText("not found")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("shows an error and retries the API request", async () => {
    fetch
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(await jsonResponse(operationsPayload))
      .mockResolvedValueOnce(await jsonResponse(readySessions()))
      .mockResolvedValueOnce(await jsonResponse(completeReport));

    render(<DashboardView />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("계정 한도를 불러오지 못했습니다.");
    expect(alert).toHaveTextContent("Network request failed");

    await userEvent.click(screen.getByRole("button", { name: "다시 시도" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(4));
    expect(await screen.findByRole("heading", { name: "Codex" })).toBeInTheDocument();
  });

  it("shows a clear empty state when no account-limit providers are returned", async () => {
    fetch
      .mockResolvedValueOnce(await jsonResponse({ detected_at: "2026-07-22T00:00:00Z", providers: [] }))
      .mockResolvedValueOnce(await jsonResponse(operationsPayload));

    render(<DashboardView />);

    expect(await screen.findByText("표시할 계정 한도 제공자가 없습니다.")).toBeInTheDocument();
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
      .mockResolvedValueOnce(await jsonResponse(readySessions()))
      .mockResolvedValueOnce(await jsonResponse(operationsPayload));

    render(<DashboardView />);

    expect(await screen.findByRole("heading", { name: "Codex" })).toBeInTheDocument();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("운영 현황을 불러오지 못했습니다.");

    await userEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(4));
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

  it("renders local sessions from the dashboard sessions API", async () => {
    fetch
      .mockResolvedValueOnce(await jsonResponse(completeReport))
      .mockResolvedValueOnce(await jsonResponse(operationsPayload))
      .mockResolvedValueOnce(await jsonResponse(readySessions([
          {
            upstream_id: "sess-1",
            provider: "codex",
            model: "gpt-5",
            size_bytes: 2048,
            created_at: "2026-07-20T00:00:00Z",
            last_run_at: "2026-07-22T00:00:00Z",
            workspace_root: "/workspace/project",
            storage_path: "/data/sessions/sess-1"
          }
        ])));

    render(<DashboardView />);

    expect(await screen.findByText("2.0 KB")).toBeInTheDocument();
    expect(screen.getByText("gpt-5")).toBeInTheDocument();
    expect(screen.getByText("/workspace/project")).toBeInTheDocument();
    expect(screen.getByText("/data/sessions/sess-1")).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith("/api/dashboard/sessions");
  });

  it("shows the five newest sessions until the user asks for more", async () => {
    const sessions = Array.from({ length: 7 }, (_, index) => ({
      upstream_id: `sess-${index + 1}`,
      provider: "codex",
      model: `model-${index + 1}`,
      size_bytes: 1024,
      created_at: `2026-07-${String(26 - index).padStart(2, "0")}T00:00:00Z`,
      last_run_at: `2026-07-${String(26 - index).padStart(2, "0")}T00:00:00Z`,
      workspace_root: `/workspace/project-${index + 1}`,
      storage_path: `/data/sessions/sess-${index + 1}`
    }));
    fetch
      .mockResolvedValueOnce(await jsonResponse(completeReport))
      .mockResolvedValueOnce(await jsonResponse(operationsPayload))
      .mockResolvedValueOnce(await jsonResponse(readySessions(sessions)));

    render(<DashboardView />);

    expect(await screen.findByText("model-1")).toBeInTheDocument();
    expect(screen.getByText("model-5")).toBeInTheDocument();
    expect(screen.queryByText("model-6")).not.toBeInTheDocument();
    expect(screen.queryByText("model-7")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "더보기 (2)" }));

    expect(screen.getByText("model-6")).toBeInTheDocument();
    expect(screen.getByText("model-7")).toBeInTheDocument();
  });

  it("shows an empty state when there are no local sessions", async () => {
    fetch
      .mockResolvedValueOnce(await jsonResponse(completeReport))
      .mockResolvedValueOnce(await jsonResponse(operationsPayload))
      .mockResolvedValueOnce(await jsonResponse(readySessions()));

    render(<DashboardView />);

    expect(await screen.findByText("로컬 세션 없음")).toBeInTheDocument();
  });

  it.each([
    ["unreachable", "로컬 모델 게이트웨이에 연결할 수 없습니다."],
    ["unauthorized", "로컬 모델 게이트웨이 인증에 실패했습니다."],
    ["not_ready", "로컬 모델 게이트웨이가 준비되지 않았습니다."],
    ["protocol_error", "로컬 모델 게이트웨이 응답 형식이 올바르지 않습니다."]
  ])("shows the %s LMG state instead of an empty session list", async (status, message) => {
    fetch
      .mockResolvedValueOnce(await jsonResponse(completeReport))
      .mockResolvedValueOnce(await jsonResponse(operationsPayload))
      .mockResolvedValueOnce(await jsonResponse({
        sessions: [],
        lmg: { status, message }
      }));

    render(<DashboardView />);

    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.queryByText("로컬 세션 없음")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "다시 시도" })).toBeInTheDocument();
  });

  it("distinguishes a model-scoped window from the account-wide window of the same length", async () => {
    // LMG reports a per-model scoped window alongside the account-wide one.
    // Both are 7-day windows resetting at the same instant, so without the
    // scope they render as two identical rows — and previously collided on the
    // React key as well.
    fetch
      .mockResolvedValueOnce(await jsonResponse({
        detected_at: "2026-08-05T00:00:00Z",
        providers: [
          {
            provider: "claude",
            label: "Claude",
            available: true,
            availability_error: null,
            version: "2.1.0",
            model: "claude-opus",
            usage_status: "ok",
            note: null,
            rate_limits: [
              { window_minutes: 10080, used_percent: 4, resets_at: "2026-08-12T09:00:00Z", scope: "" },
              { window_minutes: 10080, used_percent: 100, resets_at: "2026-08-12T09:00:00Z", scope: "Opus" }
            ]
          }
        ]
      }))
      .mockResolvedValueOnce(await jsonResponse(readySessions()))
      .mockResolvedValueOnce(await jsonResponse(operationsPayload));

    render(<DashboardView />);

    const accountWide = await screen.findByRole("progressbar", { name: "Claude 7일 한도" });
    expect(accountWide).toHaveAttribute("aria-valuenow", "4");

    const scoped = screen.getByRole("progressbar", { name: "Claude 7일 · Opus 한도" });
    expect(scoped).toHaveAttribute("aria-valuenow", "100");
  });

});
