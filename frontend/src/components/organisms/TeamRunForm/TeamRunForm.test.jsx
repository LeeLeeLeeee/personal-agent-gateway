import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TeamRunForm } from "./index.jsx";

const personas = [
  { id: "p1", name: "Tech Lead" },
  { id: "p2", name: "QA Tester" }
];

describe("TeamRunForm", () => {
  it("submits an assembled team-run payload", async () => {
    const onSubmit = vi.fn();
    render(<TeamRunForm personas={personas} onSubmit={onSubmit} />);

    await userEvent.type(screen.getByLabelText(/goal/i), "Design Agent Teams");
    await userEvent.selectOptions(screen.getByLabelText(/leader/i), "p1");
    await userEvent.click(screen.getByRole("button", { name: /create team run/i }));

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
      goal: "Design Agent Teams",
      leader_persona_id: "p1",
      run_mode: "planning_only"
    }));
  });

  it("includes checked member personas and run settings in the payload", async () => {
    const onSubmit = vi.fn();
    render(<TeamRunForm personas={personas} onSubmit={onSubmit} />);

    await userEvent.type(screen.getByLabelText(/goal/i), "Ship it");
    await userEvent.selectOptions(screen.getByLabelText(/leader/i), "p1");
    await userEvent.click(screen.getByRole("checkbox", { name: /qa tester/i }));
    await userEvent.selectOptions(screen.getByLabelText(/run mode/i), "plan_and_execute");

    const maxWorkers = screen.getByLabelText(/max workers/i);
    await userEvent.clear(maxWorkers);
    await userEvent.type(maxWorkers, "5");

    await userEvent.click(screen.getByRole("button", { name: /create team run/i }));

    expect(onSubmit).toHaveBeenCalledWith({
      goal: "Ship it",
      leader_persona_id: "p1",
      member_persona_ids: ["p2"],
      run_mode: "plan_and_execute",
      max_workers: 5
    });
  });
});
