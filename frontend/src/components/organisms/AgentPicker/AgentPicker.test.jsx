import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AgentPicker } from "./index.jsx";

const agents = [
  {
    id: "codex",
    label: "Codex CLI",
    available: true,
    models: ["default", "gpt-5.5", "gpt-5.4"],
    model_options: [
      { id: "default", label: "Default", efforts: ["low", "medium", "high", "xhigh"], default_effort: "high" },
      { id: "gpt-5.5", label: "GPT 5.5", efforts: ["low", "medium", "high", "xhigh", "max"], default_effort: "xhigh" },
      { id: "gpt-5.4", label: "GPT 5.4", efforts: ["low", "medium", "high"], default_effort: "medium" }
    ],
    default_model: "default",
    defaults: { effort: "high", sandbox: "workspace-write", approval_policy: "never" },
    options_schema: [
      { name: "effort", kind: "select", choices: ["low", "medium", "high", "xhigh"] },
      { name: "sandbox", kind: "select", choices: ["read-only", "workspace-write"] },
      { name: "approval_policy", kind: "select", choices: ["never", "on-request"] }
    ]
  },
  {
    id: "claude",
    label: "Claude Code",
    available: false,
    availability_error: "not found on PATH",
    models: ["sonnet"],
    default_model: "sonnet",
    defaults: { effort: "medium" },
    options_schema: [{ name: "effort", kind: "select", choices: ["low", "medium", "high"] }]
  }
];

function editable(overrides = {}) {
  return { agent_id: "codex", model: "default", options: {}, editable: true, ...overrides };
}

describe("AgentPicker (session config bar)", () => {
  it("selects a curated model from the model menu (no free-form input)", async () => {
    const onChange = vi.fn();
    render(<AgentPicker agents={agents} config={editable()} onChange={onChange} />);

    // model is a curated menu, never a text input
    expect(screen.queryByRole("textbox", { name: "Model" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Model" }));
    await userEvent.click(screen.getByRole("button", { name: "GPT 5.5" }));

    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ agent_id: "codex", model: "gpt-5.5" }));
  });

  it("sets effort through the segmented control", async () => {
    const onChange = vi.fn();
    render(<AgentPicker agents={agents} config={editable()} onChange={onChange} />);

    await userEvent.click(screen.getByRole("button", { name: "effort xhigh" }));

    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ options: { effort: "xhigh" } }));
  });

  it("uses model-specific effort choices and normalizes effort when the model changes", async () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <AgentPicker agents={agents} config={editable({ model: "gpt-5.5", options: { effort: "max" } })} onChange={onChange} />
    );

    expect(screen.getByRole("button", { name: "effort max" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Model" }));
    await userEvent.click(screen.getByRole("button", { name: "GPT 5.4" }));

    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({
      model: "gpt-5.4",
      options: { effort: "medium" }
    }));

    rerender(<AgentPicker agents={agents} config={editable({ model: "gpt-5.4", options: { effort: "medium" } })} onChange={onChange} />);
    expect(screen.queryByRole("button", { name: "effort max" })).not.toBeInTheDocument();
  });

  it("shows unavailable agents disabled with a reason and switches to an available one", async () => {
    const onChange = vi.fn();
    render(<AgentPicker agents={agents} config={editable({ agent_id: "claude", model: "sonnet" })} onChange={onChange} />);

    // current agent (claude) is unavailable -> no model control
    expect(screen.queryByRole("button", { name: "Model" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Agent" }));
    expect(screen.getByText("not found on PATH")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Claude Code/i })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: /Codex CLI/i }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      agent_id: "codex",
      model: "default",
      options: { effort: "high", sandbox: "workspace-write", approval_policy: "never" }
    }));
  });

  it("renders a locked config as a read-only summary", () => {
    render(
      <AgentPicker
        agents={agents}
        config={{ agent_id: "codex", model: "default", options: { effort: "high", sandbox: "workspace-write" }, editable: false }}
        onChange={vi.fn()}
      />
    );

    expect(screen.getByText(/LOCKED/)).toBeInTheDocument();
    expect(screen.getByText("workspace-write")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Model" })).not.toBeInTheDocument();
  });

  it("shows a config error with a working retry", async () => {
    const onRetry = vi.fn();
    render(<AgentPicker agents={agents} config={editable()} error="agent daemon busy" onChange={vi.fn()} onRetry={onRetry} />);

    expect(screen.getByText("agent daemon busy")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "RETRY" }));
    expect(onRetry).toHaveBeenCalled();
  });
});
