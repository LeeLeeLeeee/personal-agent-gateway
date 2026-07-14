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
    expect(onStart).toHaveBeenCalledWith(expect.objectContaining({
      team_id: "t1", goal: "ship it", run_mode: "planning_only"
    }));
  });

  it("prompts to create a team when none exist", () => {
    render(<TeamPicker teams={[]} onStart={vi.fn()} />);
    expect(screen.getByText(/먼저 팀을 만드세요/i)).toBeInTheDocument();
  });
});
