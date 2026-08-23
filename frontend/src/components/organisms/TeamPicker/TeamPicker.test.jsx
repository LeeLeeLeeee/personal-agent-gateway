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
  it("starts the single fixed Team Run flow with its first request", async () => {
    const onStart = vi.fn();
    render(<TeamPicker teams={teams} onStart={onStart} />);

    expect(screen.getByText("TRIGGERED · CONTINUOUS")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "TRIGGERED" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "AUTO" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "STANDARD" })).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Run mode" })).not.toBeInTheDocument();

    expect(screen.queryByLabelText(/base objective/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start team run" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("First request"), "  collect Reddit trends  ");
    await userEvent.click(screen.getByRole("button", { name: "Start team run" }));

    expect(onStart).toHaveBeenCalledWith({
      team_id: "t1",
      execution_policy: "triggered",
      max_workers: 1,
      initial_instruction: "collect Reddit trends"
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
    await userEvent.type(screen.getByLabelText("First request"), "Draft release notes");
    await userEvent.click(screen.getByRole("button", { name: "Start team run" }));

    expect(onStart).toHaveBeenCalledWith(expect.objectContaining({ team_id: "t2" }));
  });

  it("can inherit a completed Team Run workspace", async () => {
    const onStart = vi.fn();
    const teamRuns = [
      { id: "source-run", team_name: "SNS Studio", display_status: "completed" },
      { id: "active-run", team_name: "Busy", display_status: "running" }
    ];
    render(<TeamPicker teams={teams} teamRuns={teamRuns} onStart={onStart} />);

    await userEvent.selectOptions(screen.getByLabelText("Inherit workspace"), "source-run");
    expect(screen.queryByRole("option", { name: /Busy/ })).not.toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("First request"), "Continue the release");
    await userEvent.click(screen.getByRole("button", { name: "Start team run" }));

    expect(onStart).toHaveBeenCalledWith({
      team_id: "t1",
      execution_policy: "triggered",
      max_workers: 1,
      parent_team_run_id: "source-run",
      initial_instruction: "Continue the release"
    });
  });

  // This used to assert that no worker control existed at all, which was right
  // while execution was sequential whatever the run asked for. Now that
  // assignments can overlap, withholding the control is what would be wrong --
  // so what is pinned instead is that the choice is bounded by the executor's
  // ceiling, and that a gateway with overlap off says so rather than offering
  // a number it will not honour.
  it("offers a bounded parallel choice and keeps the runtime execution summary", () => {
    render(<TeamPicker teams={teams} onStart={vi.fn()} runtime={{
      team_execution_mode: "sequential"
    }} />);

    const choices = screen.getByLabelText("Parallel assignments");
    expect([...choices.options].map((option) => option.value)).toEqual(["1", "2", "3"]);
    expect(screen.getByText(/Overlap is off on this gateway/i)).toBeInTheDocument();
    expect(screen.getAllByText(/1 · sequential/i).length).toBeGreaterThan(0);
  });

  it("says overlap applies only to assignments that do not collide", () => {
    render(<TeamPicker teams={teams} onStart={vi.fn()} runtime={{
      team_execution_mode: "parallel"
    }} />);

    expect(screen.getByText(/do not collide/i)).toBeInTheDocument();
  });

  it("prompts to create a team when none exist", () => {
    render(<TeamPicker teams={[]} onStart={vi.fn()} />);
    expect(screen.getByText(/먼저 팀을 만드세요/i)).toBeInTheDocument();
  });

  it("explains the selected Team workspace and blocks a known invalid setup", () => {
    render(<TeamPicker
      teams={teams}
      onStart={vi.fn()}
      workspacePolicies={{
        teams: [{
          scope_id: "t1",
          capability: {
            ready: false,
            read_summary: "Select a bounded source directory",
            write_summary: "Original files are not changed",
            changes_originals: false,
            issues: ["Select a bounded source directory for isolated execution"]
          }
        }]
      }}
    />);

    expect(screen.getByText("Select a bounded source directory")).toBeInTheDocument();
    expect(screen.getByText("Original files are not changed")).toBeInTheDocument();
    expect(screen.getByText("Workspace needs attention")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start team run" })).toBeDisabled();
  });

  it("blocks starting while the workspace policy is loading", async () => {
    render(<TeamPicker teams={teams} onStart={vi.fn()} workspacePolicies={null} />);

    await userEvent.type(screen.getByLabelText("First request"), "Build the site");

    expect(screen.getByText("Checking workspace...")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start team run" })).toBeDisabled();
  });
});
