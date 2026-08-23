import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SpacesView } from "./index.jsx";

const globalPolicy = {
  scope: "global", scope_id: "", read_mode: "home", read_path: "C:\\Users\\me",
  write_mode: "isolated", workspace_path: null,
  capability: {
    ready: false,
    read_summary: "Select a bounded source directory",
    write_summary: "Original files are not changed",
    changes_originals: false,
    issues: ["Select a bounded source directory for isolated execution"]
  }
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
  it("uses task-oriented presets and hides paths for an empty isolated workspace", async () => {
    const onSaveGlobal = vi.fn();
    const noSourcePolicies = {
      ...policies,
      global: { ...globalPolicy, read_mode: "none", read_path: null }
    };
    render(<SpacesView {...props({ policies: noSourcePolicies, onSaveGlobal })} />);

    expect(screen.getByRole("heading", { name: "Workspace access" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "New empty workspace" })).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("C:\\Users\\you")).not.toBeInTheDocument();
    expect(screen.getByText("Original files are not changed")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save workspace access" }));

    expect(onSaveGlobal).toHaveBeenCalledWith(expect.objectContaining({
      read_mode: "none",
      read_path: null
    }));
  });

  it("shows the precedence and saves the required global policy", async () => {
    const onSaveGlobal = vi.fn();
    render(<SpacesView {...props({ onSaveGlobal })} />);

    expect(screen.getByLabelText("Space precedence")).toHaveTextContent("TEAM›PERSONA›GLOBAL");
    await userEvent.click(screen.getByRole("button", { name: "Save workspace access" }));

    expect(onSaveGlobal).toHaveBeenCalledWith({
      read_mode: "selected",
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
      read_mode: "selected", write_mode: "isolated"
    }));
  });

  it("exposes git worktree only for teams", async () => {
    render(<SpacesView {...props()} />);

    expect(screen.queryByRole("option", { name: "Git branch workspace" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "TEAM" }));
    expect(await screen.findByRole("option", { name: "Git branch workspace" })).toBeInTheDocument();
  });

  it("shows the three workspace facts users need before saving", async () => {
    render(<SpacesView {...props()} />);

    expect(screen.getByText("CAN READ")).toBeInTheDocument();
    expect(screen.getByText("CHANGES ORIGINALS")).toBeInTheDocument();
    expect(screen.getByText("READY TO RUN")).toBeInTheDocument();
    expect(screen.getByText("Save to verify")).toBeInTheDocument();

    await userEvent.clear(screen.getByLabelText("Source directory"));

    expect(screen.getByText("Needs attention")).toBeInTheDocument();
    expect(screen.getByText(/bounded source directory for isolated execution/i)).toBeInTheDocument();
  });

  it("does not call an edited path verified before it is saved", async () => {
    render(<SpacesView {...props()} />);

    await userEvent.clear(screen.getByLabelText("Source directory"));
    await userEvent.type(screen.getByLabelText("Source directory"), "/tmp/reference");

    expect(screen.getByText("Save to verify")).toBeInTheDocument();
    expect(screen.getByText("No")).toBeInTheDocument();
  });
});
