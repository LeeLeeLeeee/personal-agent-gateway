import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SchedulesView } from "./index.jsx";

const schedules = [
  { id: "s1", name: "Nightly digest", cron_expression: "0 9 * * *", enabled: true, next_run_at: "2026-07-10T09:00:00Z", last_run_at: null, input_template: { prompt: "Summarize my workspace" } }
];

describe("SchedulesView", () => {
  it("lists schedules with cron and enabled state", () => {
    render(<SchedulesView schedules={schedules} onCreate={vi.fn()} onPause={vi.fn()} onResume={vi.fn()} onDelete={vi.fn()} onRunNow={vi.fn()} />);
    expect(screen.getByText("Nightly digest")).toBeInTheDocument();
    expect(screen.getByText("0 9 * * *")).toBeInTheDocument();
    expect(screen.getByText("ENABLED")).toBeInTheDocument();
  });

  it("builds an agent-instruction schedule from the form", async () => {
    const onCreate = vi.fn();
    render(<SchedulesView schedules={[]} onCreate={onCreate} onPause={vi.fn()} onResume={vi.fn()} onDelete={vi.fn()} onRunNow={vi.fn()} />);
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
});
