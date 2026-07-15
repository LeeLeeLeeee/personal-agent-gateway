import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TeamRunCard } from "./index.jsx";

const run = {
  id: "TR-204", goal: "Ship export-to-PDF", status: "running", run_mode: "plan_and_execute",
  leader_name: "Tech Lead",
  leader: { name: "Tech Lead", avatar: "a01", initials: "TL" },
  members: [{ name: "Frontend Dev", avatar: "a05", initials: "FD" }],
  task_counts: { completed: 2, in_progress: 1, pending: 3 },
  task_done: 2, task_total: 6, elapsed_seconds: 251, team_id: "t1"
};

describe("TeamRunCard", () => {
  it("shows id, goal, leader, members and task progress", () => {
    const { container } = render(<TeamRunCard run={run} onOpen={vi.fn()} />);
    expect(screen.getByText("TR-204")).toBeInTheDocument();
    expect(screen.getByText(/Ship export-to-PDF/i)).toBeInTheDocument();
    expect(screen.getByText("Tech Lead")).toBeInTheDocument();
    expect(container.querySelector('img[src="/static/avatars/a01.png"]')).toBeInTheDocument();
    expect(screen.getByText("Frontend Dev")).toBeInTheDocument();
    expect(container.querySelector('img[src="/static/avatars/a05.png"]')).toBeInTheDocument();
    expect(screen.getByText("2 / 6 DONE")).toBeInTheDocument();
  });

  it("calls onOpen when clicked", async () => {
    const onOpen = vi.fn();
    render(<TeamRunCard run={run} onOpen={onOpen} />);
    await userEvent.click(screen.getByRole("button", { name: /open team run/i }));
    expect(onOpen).toHaveBeenCalledWith("TR-204");
  });

  it("marks legacy runs without a team", () => {
    render(<TeamRunCard run={{ ...run, team_id: null }} onOpen={vi.fn()} />);
    expect(screen.getByText("LEGACY")).toBeInTheDocument();
  });

  it("falls back to initials while keeping roster names visible", () => {
    render(<TeamRunCard run={{
      ...run,
      leader: { name: "Tech Lead", avatar: "", initials: "TL" },
      members: [{ name: "QA Tester", avatar: "", initials: "QT" }]
    }} onOpen={vi.fn()} />);

    expect(screen.getByText("TL")).toBeInTheDocument();
    expect(screen.getByText("Tech Lead")).toBeInTheDocument();
    expect(screen.getByText("QT")).toBeInTheDocument();
    expect(screen.getByText("QA Tester")).toBeInTheDocument();
  });
});
