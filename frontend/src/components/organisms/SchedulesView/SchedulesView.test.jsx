import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SchedulesView } from "./index.jsx";

const schedules = [
  { id: "s1", name: "Nightly digest", cron_expression: "0 9 * * *", enabled: true, next_run_at: "2026-07-10T09:00:00Z", last_run_at: null, input_template: { prompt: "Summarize my workspace" } }
];

describe("SchedulesView", () => {
  it("lists schedules with cron and enabled state", () => {
    render(<SchedulesView schedules={schedules} automationReady onCreate={vi.fn()} onPause={vi.fn()} onResume={vi.fn()} onDelete={vi.fn()} onRunNow={vi.fn()} />);
    expect(screen.getByText("Nightly digest")).toBeInTheDocument();
    expect(screen.getByText("0 9 * * *")).toBeInTheDocument();
    expect(screen.getByText("ENABLED")).toBeInTheDocument();
  });

  it("builds an agent-instruction schedule from the form", async () => {
    const onCreate = vi.fn();
    render(<SchedulesView schedules={[]} automationReady onCreate={onCreate} onPause={vi.fn()} onResume={vi.fn()} onDelete={vi.fn()} onRunNow={vi.fn()} />);
    await userEvent.type(screen.getByLabelText("Name"), "Morning brief");
    await userEvent.type(screen.getByLabelText("Instruction"), "Give me a status brief");
    await userEvent.click(screen.getByRole("button", { name: /create schedule/i }));
    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({
      name: "Morning brief",
      capability_id: "agent.instruct",
      cron_expression: "0 9 * * *",
      input_template: { prompt: "Give me a status brief" }
    }));
  });

  it("disables create and run-now actions when automation is unhealthy", () => {
    render(
      <SchedulesView
        schedules={schedules}
        automationReady={false}
        automationUnavailableReason="Worker is not running"
        onCreate={vi.fn()}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onDelete={vi.fn()}
        onRunNow={vi.fn()}
      />
    );

    expect(screen.getByRole("status")).toHaveTextContent("Worker is not running");
    expect(screen.getByRole("button", { name: /create schedule/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /run now/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /pause/i })).toBeEnabled();
  });

  it("loads schedule history, failure stats, and the next three runs", async () => {
    const onLoadDetail = vi.fn().mockResolvedValue({
      schedule: { ...schedules[0], timezone: "Asia/Seoul" },
      jobs: [
        { id: "j2", title: "Nightly digest", status: "failed", error_message: "agent failed" },
        { id: "j1", title: "Nightly digest", status: "succeeded" }
      ],
      stats: { total: 2, succeeded: 1, failed: 1, canceled: 0, success_rate: 0.5 },
      last_failure: { id: "j2", error_message: "agent failed" },
      next_runs: [
        "2026-07-16T00:00:00Z",
        "2026-07-17T00:00:00Z",
        "2026-07-18T00:00:00Z"
      ]
    });
    render(
      <SchedulesView
        schedules={schedules}
        automationReady
        onCreate={vi.fn()}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onDelete={vi.fn()}
        onRunNow={vi.fn()}
        onLoadDetail={onLoadDetail}
      />
    );

    await userEvent.click(screen.getByRole("button", { name: /history for Nightly digest/i }));

    expect(onLoadDetail).toHaveBeenCalledWith("s1");
    expect(await screen.findByText("50%")).toBeInTheDocument();
    expect(screen.getByText("agent failed")).toBeInTheDocument();
    expect(screen.getByText("Asia/Seoul")).toBeInTheDocument();
    expect(within(screen.getByLabelText("Next run preview")).getAllByText(/일/)).toHaveLength(3);
  });
});
