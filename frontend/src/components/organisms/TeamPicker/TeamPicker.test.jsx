import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TeamPicker } from "./index.jsx";

const teams = [{
  id: "t1", name: "Release Crew",
  leader: { name: "Tech Lead", avatar: "a01" },
  members: [{ name: "QA", avatar: "a08" }]
}];

describe("TeamPicker", () => {
  it("shows the selected team roster read-only and starts a run", async () => {
    const onStart = vi.fn();
    render(<TeamPicker teams={teams} onStart={onStart} />);
    expect(screen.getByText("Tech Lead")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/goal/i), "ship it");
    await userEvent.click(screen.getByRole("button", { name: /start team run/i }));
    expect(onStart).toHaveBeenCalledWith({
      team_id: "t1",
      goal: "ship it",
      run_mode: "planning_only",
      lifecycle_mode: "standard",
      max_workers: 1
    });
  });

  it("forces plan-and-execute for a continuous run payload", async () => {
    const onStart = vi.fn();
    render(<TeamPicker teams={teams} onStart={onStart} />);

    await userEvent.click(screen.getByRole("button", { name: "CONTINUOUS" }));
    expect(screen.getByRole("button", { name: "PLANNING ONLY" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "PLAN + EXECUTE" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.type(screen.getByLabelText(/goal/i), "watch inbox");
    await userEvent.click(screen.getByRole("button", { name: /create continuous run/i }));

    expect(onStart).toHaveBeenCalledWith({
      team_id: "t1",
      goal: "watch inbox",
      run_mode: "plan_and_execute",
      lifecycle_mode: "continuous",
      max_workers: 1
    });
  });

  it("shows only implemented run modes and sequential execution", () => {
    render(<TeamPicker teams={teams} onStart={vi.fn()} runtime={{
      team_review_supported: false,
      team_execution_mode: "sequential"
    }} />);

    expect(screen.queryByRole("button", { name: /review only/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /increase workers/i })).not.toBeInTheDocument();
    expect(screen.getAllByText(/sequential/i).length).toBeGreaterThan(0);
  });

  it("prompts to create a team when none exist", () => {
    render(<TeamPicker teams={[]} onStart={vi.fn()} />);
    expect(screen.getByText(/먼저 팀을 만드세요/i)).toBeInTheDocument();
  });
});
