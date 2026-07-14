import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TeamsView } from "./index.jsx";

const personas = [
  { id: "p1", name: "Tech Lead", role: "lead", avatar: "a01" },
  { id: "p2", name: "QA", role: "qa", avatar: "a08" }
];

describe("TeamsView", () => {
  it("creates a team with a leader and members", async () => {
    const onCreate = vi.fn(async () => ({ id: "t1" }));
    render(<TeamsView teams={[]} personas={personas} onCreate={onCreate} onUpdate={vi.fn()} onDelete={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /new team/i }));
    await userEvent.type(screen.getByLabelText(/team name/i), "Release Crew");
    await userEvent.click(screen.getByRole("button", { name: /save team/i }));
    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({ name: "Release Crew" }));
  });

  it("lists existing teams", () => {
    render(<TeamsView teams={[{ id: "t1", name: "Release Crew", leader: { name: "Tech Lead" }, members: [] }]}
      personas={personas} onCreate={vi.fn()} onUpdate={vi.fn()} onDelete={vi.fn()} />);
    expect(screen.getByText("Release Crew")).toBeInTheDocument();
  });
});
