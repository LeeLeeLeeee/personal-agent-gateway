import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TeamPicker } from "./index.jsx";

const teams = [{
  id: "t1", name: "Release Crew",
  leader: { name: "Tech Lead", avatar: "a01" },
  members: [{ name: "QA", avatar: "a08" }]
}, {
  id: "t2", name: "Docs Crew",
  leader: { name: "Docs Lead", avatar: "a02" },
  members: [{ name: "Writer", avatar: "a03" }]
}];

describe("TeamPicker", () => {
  it("shows fixed CONTINUOUS creation and submits a TRIGGERED policy without AUTO fields", async () => {
    const onStart = vi.fn();
    render(<TeamPicker teams={teams} onStart={onStart} />);

    expect(screen.getByText("CONTINUOUS · FIXED")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "TRIGGERED" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("button", { name: "STANDARD" })).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Run mode" })).not.toBeInTheDocument();

    await userEvent.type(screen.getByLabelText(/goal/i), "  ship it  ");
    await userEvent.click(screen.getByRole("button", { name: "Create team run" }));

    expect(onStart).toHaveBeenCalledWith({
      team_id: "t1",
      goal: "ship it",
      execution_policy: "triggered"
    });
  });

  it("creates an AUTO continuous run with numeric repeat and interval settings", async () => {
    const onStart = vi.fn();
    render(<TeamPicker teams={teams} onStart={onStart} />);

    await userEvent.click(screen.getByRole("button", { name: "AUTO" }));
    expect(screen.getByLabelText("Repeat count")).toHaveValue(3);
    expect(screen.getByLabelText("Interval minutes")).toHaveValue(5);
    await userEvent.clear(screen.getByLabelText("Repeat count"));
    await userEvent.type(screen.getByLabelText("Repeat count"), "5");
    await userEvent.clear(screen.getByLabelText("Interval minutes"));
    await userEvent.type(screen.getByLabelText("Interval minutes"), "30");
    await userEvent.click(screen.getByRole("button", { name: "Create team run" }));

    expect(onStart).toHaveBeenCalledWith({
      team_id: "t1",
      goal: "",
      execution_policy: "auto",
      auto_repeat_count: 5,
      auto_interval_minutes: 30
    });
  });

  it("updates the locked roster and payload when another team is selected", async () => {
    const onStart = vi.fn();
    render(<TeamPicker teams={teams} onStart={onStart} />);

    expect(screen.getByText("Tech Lead")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Docs Crew" }));
    expect(screen.getByText("Docs Lead")).toBeInTheDocument();
    expect(screen.getByText("Writer")).toBeInTheDocument();
    expect(screen.queryByText("Tech Lead")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Create team run" }));

    expect(onStart).toHaveBeenCalledWith(expect.objectContaining({ team_id: "t2" }));
  });

  it("keeps the runtime execution summary without exposing worker controls", () => {
    render(<TeamPicker teams={teams} onStart={vi.fn()} runtime={{
      team_execution_mode: "sequential"
    }} />);

    expect(screen.queryByRole("button", { name: /increase workers/i })).not.toBeInTheDocument();
    expect(screen.getAllByText(/1 · sequential/i).length).toBeGreaterThan(0);
  });

  it("prompts to create a team when none exist", () => {
    render(<TeamPicker teams={[]} onStart={vi.fn()} />);
    expect(screen.getByText(/먼저 팀을 만드세요/i)).toBeInTheDocument();
  });
});
