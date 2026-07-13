import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { vi } from "vitest";
import { TeamRunDetail } from "./index.jsx";

describe("TeamRunDetail", () => {
  it("renders header, agent lanes, task board, and activity", () => {
    render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "Design", status: "running", run_mode: "plan_and_execute" },
          agents: [{ id: "a1", name: "Tech Lead", role: "Planning", status: "running", current_task_id: null }],
          tasks: [{ id: "t1", title: "Define schema", description: "tables", status: "in_progress" }],
          messages: [{ id: "m1", kind: "note", content: "Planning started", created_at: "2026-07-08T00:00:00Z" }]
        }}
      />
    );

    expect(screen.getByText("Design")).toBeInTheDocument();
    expect(screen.getByText("Tech Lead")).toBeInTheDocument();
    expect(screen.getByText("Define schema")).toBeInTheDocument();
    expect(screen.getByText("Planning started")).toBeInTheDocument();
  });

  it("renders a placeholder when no run is selected", () => {
    render(<TeamRunDetail detail={null} />);
    expect(screen.getByText("No team run selected.")).toBeInTheDocument();
  });

  it("submits additional work through onAddWork", async () => {
    const onAddWork = vi.fn();
    render(
      <TeamRunDetail
        onAddWork={onAddWork}
        detail={{
          run: { id: "r1", goal: "Design", status: "running", run_mode: "plan_and_execute" },
          agents: [],
          tasks: [],
          messages: []
        }}
      />
    );

    await userEvent.type(screen.getByLabelText("Additional work"), "also write docs");
    await userEvent.click(screen.getByRole("button", { name: "추가 업무 요청" }));

    expect(onAddWork).toHaveBeenCalledWith("also write docs");
  });

  it("labels the add-work button for reopening a finished run", () => {
    render(
      <TeamRunDetail
        onAddWork={vi.fn()}
        detail={{
          run: { id: "r1", goal: "Design", status: "completed", run_mode: "plan_and_execute" },
          agents: [],
          tasks: [],
          messages: []
        }}
      />
    );
    expect(screen.getByRole("button", { name: "재개하며 요청" })).toBeInTheDocument();
  });

  it("marks the current phase in the stepper", () => {
    render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "Design", status: "summarizing", run_mode: "plan_and_execute" },
          agents: [],
          tasks: [],
          messages: []
        }}
      />
    );
    expect(screen.getByText("Summarizing").closest(".team-phase")).toHaveAttribute("aria-current", "step");
    expect(screen.getByText("Planning").closest(".team-phase")).not.toHaveAttribute("aria-current");
  });
});
