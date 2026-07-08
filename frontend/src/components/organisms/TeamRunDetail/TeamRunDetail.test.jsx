import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
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
});
