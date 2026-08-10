import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TeamTaskCard } from "./index.jsx";

const task = { id: "t1", title: "Build API", status: "in_progress" };

describe("TeamTaskCard", () => {
  it("shows the assigned Persona avatar and visible name", () => {
    const { container } = render(<TeamTaskCard
      task={task}
      owner={{ name: "Kim Developer", persona_snapshot: { avatar: "a03" } }}
      onOpen={vi.fn()}
    />);

    expect(screen.getByText("Kim Developer")).toBeInTheDocument();
    expect(container.querySelector('img[src="/static/avatars/a03.png"]')).toBeInTheDocument();
  });

  it("shows UNASSIGNED when no Persona owns the task", () => {
    render(<TeamTaskCard task={task} owner={null} onOpen={vi.fn()} />);
    expect(screen.getByText("UNASSIGNED")).toBeInTheDocument();
  });

  it("shows completed work, changed files, and report counts on the card", () => {
    render(
      <TeamTaskCard
        task={{ ...task, status: "completed", result: "Implemented the API and added tests." }}
        owner={null}
        fileCount={2}
        reportCount={1}
        onOpen={vi.fn()}
      />
    );

    expect(screen.getByText("COMPLETED")).toBeInTheDocument();
    expect(screen.getByText("Implemented the API and added tests.")).toBeInTheDocument();
    expect(screen.getByText("FILES 2")).toBeInTheDocument();
    expect(screen.getByText("REPORTS 1")).toBeInTheDocument();
  });

  it("shows blocked as blocked with a stable reason and safe diagnostic", () => {
    render(
      <TeamTaskCard
        task={{
          ...task,
          status: "blocked",
          error_message: "Input snapshot changed; retry with fresh inputs.",
          outcome: { reason_code: "input_snapshot_modified" },
          acceptance_result: {
            accepted: false,
            status: "blocked",
            reason_code: "input_snapshot_modified"
          }
        }}
        owner={null}
        onOpen={vi.fn()}
      />
    );

    expect(screen.getByText("차단됨")).toBeInTheDocument();
    expect(screen.getByText("input_snapshot_modified")).toBeInTheDocument();
    expect(screen.getByText(/Input snapshot changed/)).toBeInTheDocument();
    expect(screen.queryByText("COMPLETED")).not.toBeInTheDocument();
  });

  it("keeps a long failure diagnostic outside compact metadata", () => {
    const diagnostic = "Required task failed: " + "a-very-long-path/".repeat(20);
    const { container } = render(
      <TeamTaskCard
        task={{ ...task, status: "failed", error_message: diagnostic }}
        owner={{ name: "QA Reviewer", persona_snapshot: {} }}
        reportCount={1}
        onOpen={vi.fn()}
      />
    );

    const diagnosticNode = screen.getByText(diagnostic);
    expect(diagnosticNode).toHaveClass("team-task-diagnostic");
    expect(diagnosticNode.closest(".team-task-meta")).toBeNull();
    expect(container.querySelector(".team-task-meta")).toHaveTextContent("FILES 0");
    expect(container.querySelector(".team-task-meta")).toHaveTextContent("REPORTS 1");
  });

  it("shows why a dependency-skipped task did not run", () => {
    render(
      <TeamTaskCard
        task={{
          ...task,
          status: "skipped",
          error_message: "skipped_by_dependency"
        }}
        owner={null}
        prerequisiteTitles={["Collect sources"]}
        onOpen={vi.fn()}
      />
    );

    expect(screen.getByText("건너뜀")).toBeInTheDocument();
    expect(screen.getByText("skipped_by_dependency")).toBeInTheDocument();
    expect(screen.getByText("선행 작업 · Collect sources")).toBeInTheDocument();
  });
});
