import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SpacesView } from "./index.jsx";

const globalPolicy = {
  scope: "global", scope_id: "", read_mode: "home", read_path: "C:\\Users\\me",
  write_mode: "isolated", workspace_path: null
};

const policies = {
  precedence: ["team", "persona", "global"],
  global: globalPolicy,
  personas: [],
  teams: [{ ...globalPolicy, scope: "team", scope_id: "t1" }]
};

function props(overrides = {}) {
  return {
    policies,
    teams: [{ id: "t1", name: "Gateway Team" }],
    personas: [{ id: "p1", name: "Developer" }],
    onSaveGlobal: vi.fn(),
    onSavePersona: vi.fn(),
    onDeletePersona: vi.fn(),
    onSaveTeam: vi.fn(),
    ...overrides
  };
}

describe("SpacesView", () => {
  it("shows the precedence and saves the required global policy", async () => {
    const onSaveGlobal = vi.fn();
    render(<SpacesView {...props({ onSaveGlobal })} />);

    expect(screen.getByLabelText("Space precedence")).toHaveTextContent("TEAM›PERSONA›GLOBAL");
    await userEvent.click(screen.getByRole("button", { name: "Save space" }));

    expect(onSaveGlobal).toHaveBeenCalledWith({
      read_mode: "home",
      read_path: "C:\\Users\\me",
      write_mode: "isolated",
      workspace_path: null
    });
  });

  it("offers persona inheritance and creates an override only on request", async () => {
    const onSavePersona = vi.fn();
    render(<SpacesView {...props({ onSavePersona })} />);

    await userEvent.click(screen.getByRole("tab", { name: "PERSONA" }));
    expect(await screen.findByText("INHERITS GLOBAL")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Create persona space" }));

    expect(onSavePersona).toHaveBeenCalledWith("p1", expect.objectContaining({
      read_mode: "home", write_mode: "isolated"
    }));
  });

  it("exposes git worktree only for teams", async () => {
    render(<SpacesView {...props()} />);

    expect(screen.queryByRole("option", { name: "Git worktree" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "TEAM" }));
    expect(await screen.findByRole("option", { name: "Git worktree" })).toBeInTheDocument();
  });
});
