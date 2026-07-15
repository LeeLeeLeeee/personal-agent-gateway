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
});
