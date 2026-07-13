import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TeamRunForm } from "./index.jsx";

const personas = [
  { id: "p1", name: "Tech Lead", role: "Planning" },
  { id: "p2", name: "QA Tester", role: "Verification" }
];

describe("TeamRunForm", () => {
  it("submits an assembled team-run payload", async () => {
    const onSubmit = vi.fn();
    render(<TeamRunForm personas={personas} onSubmit={onSubmit} />);

    await userEvent.type(screen.getByLabelText("Goal"), "Design Agent Teams");
    await userEvent.click(screen.getByRole("button", { name: /select tech lead as leader/i }));
    await userEvent.click(screen.getByRole("button", { name: /start team run/i }));

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
      goal: "Design Agent Teams",
      leader_persona_id: "p1",
      run_mode: "planning_only"
    }));
  });

  it("includes toggled member personas and run settings in the payload", async () => {
    const onSubmit = vi.fn();
    render(<TeamRunForm personas={personas} onSubmit={onSubmit} />);

    await userEvent.type(screen.getByLabelText("Goal"), "Ship it");
    await userEvent.click(screen.getByRole("button", { name: /select tech lead as leader/i }));
    await userEvent.click(screen.getByRole("button", { name: /toggle qa tester as member/i }));
    await userEvent.click(screen.getByRole("button", { name: /plan \+ execute/i }));

    // default max workers is 3; bump to 5
    await userEvent.click(screen.getByRole("button", { name: /increase workers/i }));
    await userEvent.click(screen.getByRole("button", { name: /increase workers/i }));

    await userEvent.click(screen.getByRole("button", { name: /start team run/i }));

    expect(onSubmit).toHaveBeenCalledWith({
      goal: "Ship it",
      leader_persona_id: "p1",
      member_persona_ids: ["p2"],
      run_mode: "plan_and_execute",
      max_workers: 5
    });
  });

  it("previews the selected leader, member count and workers", async () => {
    render(<TeamRunForm personas={personas} onSubmit={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: /toggle qa tester as member/i }));

    expect(screen.getByText("1 agents")).toBeInTheDocument();
    expect(screen.getByLabelText("Max workers")).toHaveTextContent("3");
  });

  it("disables the current leader in the member list", async () => {
    render(<TeamRunForm personas={personas} onSubmit={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /select tech lead as leader/i }));

    const leaderAsMember = screen.getByRole("button", { name: /tech lead is the leader/i });
    expect(leaderAsMember).toBeDisabled();
  });

  it("deselects a member when it becomes the leader", async () => {
    const onSubmit = vi.fn();
    render(<TeamRunForm personas={personas} onSubmit={onSubmit} />);

    await userEvent.click(screen.getByRole("button", { name: /toggle qa tester as member/i }));
    // Promote QA Tester to leader; it must drop out of the member set.
    await userEvent.click(screen.getByRole("button", { name: /select qa tester as leader/i }));
    await userEvent.click(screen.getByRole("button", { name: /start team run/i }));

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
      leader_persona_id: "p2",
      member_persona_ids: []
    }));
  });
});
