import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TeamsView } from "./index.jsx";

const personas = [
  { id: "p1", name: "Tech Lead", role: "lead", avatar: "dev-glasses" },
  { id: "p2", name: "QA", role: "qa", avatar: "owl" },
  { id: "p3", name: "Mina Park", role: "designer", avatar: "" }
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

  it("strips a persona from members when it is promoted to leader", async () => {
    const onCreate = vi.fn(async () => ({ id: "t2" }));
    render(<TeamsView teams={[]} personas={personas} onCreate={onCreate} onUpdate={vi.fn()} onDelete={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /new team/i }));
    await userEvent.type(screen.getByLabelText(/team name/i), "QA Squad");

    // Leader defaults to Tech Lead (p1), so QA (p2) is selectable as a member.
    // [0] is the LEADER choice button, [1] is the MEMBERS choice button.
    await userEvent.click(screen.getAllByRole("button", { name: "QA" })[1]);
    expect(screen.getByText("1 SELECTED")).toBeInTheDocument();

    // Now promote QA (p2) to leader while it is still selected as a member.
    await userEvent.click(screen.getAllByRole("button", { name: "QA" })[0]);
    expect(screen.getByText("0 SELECTED")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /save team/i }));

    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({ leader_persona_id: "p2" }));
    const payload = onCreate.mock.calls[0][0];
    expect(payload.member_persona_ids).not.toContain("p2");
  });

  it("renders profile details and stable accessible names on persona cards", async () => {
    render(<TeamsView teams={[]} personas={personas} onCreate={vi.fn()} onUpdate={vi.fn()} onDelete={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /new team/i }));

    const techLeadCard = screen.getByRole("button", { name: "Tech Lead" });
    expect(techLeadCard.querySelector("img")).toHaveAttribute("src", "/static/avatars/dev-glasses.png");
    expect(screen.getAllByText("designer")).toHaveLength(2);
    expect(screen.getAllByText("MP")).toHaveLength(2);
    expect(techLeadCard).toHaveAttribute("aria-pressed", "true");
  });

  it("lists existing teams", () => {
    render(<TeamsView teams={[{ id: "t1", name: "Release Crew", leader: { name: "Tech Lead" }, members: [] }]}
      personas={personas} onCreate={vi.fn()} onUpdate={vi.fn()} onDelete={vi.fn()} />);
    expect(screen.getByText("Release Crew")).toBeInTheDocument();
  });
});
