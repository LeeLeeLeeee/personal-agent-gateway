import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PersonaLibrary } from "./index.jsx";

const personas = [
  { id: "p1", name: "Tech Lead", role: "Planning", description: "Owns the plan", responsibilities: ["Plan"], constraints: [] },
  { id: "p2", name: "QA Tester", role: "Verification", responsibilities: [], constraints: ["No prod data"] }
];

const agents = [
  {
    id: "codex",
    label: "Codex CLI",
    available: true,
    version: "codex-cli 1.0",
    default_model: "default",
    models: ["default", "codex-max", "codex-fast"],
    model_options: [
      { id: "default", label: "Default", efforts: ["low", "high"], default_effort: "high" },
      { id: "codex-max", label: "Codex Max", efforts: ["high", "max"], default_effort: "max" },
      { id: "codex-fast", label: "Codex Fast", efforts: ["low", "medium"], default_effort: "medium" }
    ],
    defaults: { effort: "high", sandbox: "workspace-write", approval_policy: "never" },
    options_schema: [
      { name: "effort", kind: "select", choices: ["low", "medium", "high", "max"] },
      { name: "sandbox", kind: "select", choices: ["read-only", "workspace-write"] },
      { name: "approval_policy", kind: "select", choices: ["never", "on-request"] }
    ]
  },
  {
    id: "claude",
    label: "Claude Code",
    available: true,
    default_model: "sonnet",
    models: ["sonnet", "opus"],
    model_options: [
      { id: "sonnet", label: "Sonnet", efforts: ["low", "medium", "high"], default_effort: "medium" },
      { id: "opus", label: "Opus", efforts: ["high", "max"], default_effort: "high" }
    ],
    defaults: { effort: "medium", permission_mode: "manual" },
    options_schema: [
      { name: "effort", kind: "select", choices: ["low", "medium", "high", "max"] },
      { name: "permission_mode", kind: "select", choices: ["manual", "plan"] }
    ]
  }
];

describe("PersonaLibrary", () => {
  it("shows the selected persona's detail and switches when another row is clicked", async () => {
    render(<PersonaLibrary personas={personas} onCreate={vi.fn()} onSave={vi.fn()} />);

    // first persona is selected by default
    expect(screen.getByLabelText("Name")).toHaveValue("Tech Lead");
    expect(screen.getByLabelText("Description")).toHaveValue("Owns the plan");

    await userEvent.click(screen.getByRole("button", { name: "Select QA Tester" }));
    expect(screen.getByLabelText("Name")).toHaveValue("QA Tester");
    expect(screen.getByLabelText("Constraints")).toHaveValue("No prod data");
  });

  it("does not render a seed defaults button", () => {
    render(<PersonaLibrary personas={personas} onCreate={vi.fn()} onSave={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /seed defaults/i })).not.toBeInTheDocument();
  });

  it("creates a new persona from the New persona panel", async () => {
    const onCreate = vi.fn();
    const user = userEvent.setup();
    render(<PersonaLibrary personas={personas} onCreate={onCreate} onSave={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /new persona/i }));
    expect(screen.getByLabelText("Name")).toHaveValue("");

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Growth Hacker" } });
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "Growth" } });
    fireEvent.change(screen.getByLabelText("Responsibilities"), { target: { value: "Find channels\nRun experiments" } });
    await user.click(screen.getByRole("button", { name: /create persona/i }));

    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({
      name: "Growth Hacker",
      role: "Growth",
      responsibilities: ["Find channels", "Run experiments"],
      constraints: []
    }));
  });

  it("selects backend, model and model-specific options through the chat-style picker", async () => {
    const onCreate = vi.fn();
    const user = userEvent.setup();
    render(<PersonaLibrary personas={personas} agents={agents} onCreate={onCreate} onSave={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /new persona/i }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Local Agent" } });

    // backend via the agent dropdown (chat-style, not a native <select>)
    await user.click(screen.getByRole("button", { name: "Agent" }));
    await user.click(screen.getByRole("button", { name: /Claude Code/i }));

    // model -> opus
    await user.click(screen.getByRole("button", { name: "Model" }));
    await user.click(screen.getByRole("button", { name: "Opus" }));

    // opus only offers high/max, so "low" is no longer a choice
    expect(screen.queryByRole("button", { name: "effort low" })).not.toBeInTheDocument();

    // effort segmented control (opus offers high/max), then permission mode dropdown -> plan
    await user.click(screen.getByRole("button", { name: "effort max" }));
    await user.click(screen.getByRole("button", { name: "permission mode" }));
    await user.click(screen.getByRole("button", { name: /^plan/ }));

    await user.click(screen.getByRole("button", { name: /create persona/i }));

    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({
      default_backend: "claude",
      default_model: "opus",
      default_options: { effort: "max", permission_mode: "plan" }
    }));
  });

  it("explains runtime options with an info tooltip in the persona picker", () => {
    render(<PersonaLibrary personas={personas} agents={agents} onCreate={vi.fn()} onSave={vi.fn()} />);
    expect(screen.getByRole("button", { name: "sandbox 설명" })).toBeInTheDocument();
    expect(screen.getByText(/파일을 어디까지 쓸 수 있는지/)).toBeInTheDocument();
  });

  it("creates (not updates) the first persona when the library is empty", async () => {
    const onCreate = vi.fn();
    const onSave = vi.fn();
    const user = userEvent.setup();
    render(<PersonaLibrary personas={[]} onCreate={onCreate} onSave={onSave} />);

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "박PM" } });
    await user.click(screen.getByRole("button", { name: /create persona/i }));

    expect(onCreate).toHaveBeenCalledTimes(1);
    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({ name: "박PM" }));
    expect(onSave).not.toHaveBeenCalled();
  });

  it("saves edits to an existing persona", async () => {
    const onSave = vi.fn();
    const user = userEvent.setup();
    render(<PersonaLibrary personas={personas} onCreate={vi.fn()} onSave={onSave} />);

    const name = screen.getByLabelText("Name");
    fireEvent.change(name, { target: { value: "Lead Architect" } });
    await user.click(screen.getByRole("button", { name: /save persona/i }));

    expect(onSave).toHaveBeenCalledWith("p1", expect.objectContaining({ name: "Lead Architect" }));
  });

  it("deletes the selected persona after confirmation", async () => {
    const onDelete = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<PersonaLibrary personas={personas} onCreate={vi.fn()} onSave={vi.fn()} onDelete={onDelete} />);

    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    expect(onDelete).toHaveBeenCalledWith("p1");
    window.confirm.mockRestore();
  });

  it("does not delete when confirmation is dismissed", async () => {
    const onDelete = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<PersonaLibrary personas={personas} onCreate={vi.fn()} onSave={vi.fn()} onDelete={onDelete} />);

    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    expect(onDelete).not.toHaveBeenCalled();
    window.confirm.mockRestore();
  });

  it("hides the delete button while creating a new persona", async () => {
    render(<PersonaLibrary personas={personas} onCreate={vi.fn()} onSave={vi.fn()} onDelete={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /new persona/i }));
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
  });

  it("selects an avatar through the modal", async () => {
    const onSave = vi.fn();
    const avatars = [
      { slug: "person-01", label: "Person 1", category: "person" },
      { slug: "tech-02", label: "Tech 2", category: "tech" }
    ];
    render(<PersonaLibrary personas={personas} avatars={avatars} onCreate={vi.fn()} onSave={onSave} />);

    await userEvent.click(screen.getByRole("button", { name: /change avatar/i }));
    const dialog = screen.getByRole("dialog", { name: /choose avatar/i });
    await userEvent.click(within(dialog).getByRole("button", { name: "Tech 2" }));
    await userEvent.click(within(dialog).getByRole("button", { name: /use avatar/i }));
    await userEvent.click(screen.getByRole("button", { name: /save persona/i }));

    expect(onSave).toHaveBeenCalledWith("p1", expect.objectContaining({ avatar: "tech-02" }));
  });
});
