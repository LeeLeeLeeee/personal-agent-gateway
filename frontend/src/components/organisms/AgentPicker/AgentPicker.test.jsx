import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AgentPicker } from "./index.jsx";

const agents = [
  {
    id: "codex",
    label: "Codex CLI",
    available: true,
    models: ["default"],
    default_model: "default",
    defaults: { sandbox: "workspace-write", approval_policy: "never" },
    options_schema: [
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
    options_schema: [{ name: "effort", kind: "select", choices: ["medium", "high"] }]
  }
];

describe("AgentPicker", () => {
  it("shows editable available agents and disables unavailable agents", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();

    render(
      <AgentPicker
        agents={agents}
        config={{ agent_id: "codex", model: "default", options: {}, editable: true }}
        onChange={onChange}
      />
    );

    expect(screen.getByText("Codex CLI")).toBeInTheDocument();
    expect(screen.getByText("Claude Code")).toBeInTheDocument();
    expect(screen.getByText("not found on PATH")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Claude Code/i })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: /Claude Code/i }));
    expect(onChange).not.toHaveBeenCalled();

    await user.selectOptions(screen.getByLabelText("Model"), "default");

    expect(onChange).toHaveBeenCalled();
  });

  it("shows an unavailable current config as read-only summary", () => {
    const onChange = vi.fn();

    render(
      <AgentPicker
        agents={agents}
        config={{ agent_id: "claude", model: "sonnet", options: { effort: "medium" }, editable: true }}
        onChange={onChange}
      />
    );

    expect(screen.getByText("Claude Code")).toBeInTheDocument();
    expect(screen.getByText("not found on PATH")).toBeInTheDocument();
    expect(screen.getByText("UNAVAILABLE")).toBeInTheDocument();
    expect(screen.queryByLabelText("Model")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/effort/i)).not.toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("renders locked config as read-only summary", () => {
    render(
      <AgentPicker
        agents={agents}
        config={{ agent_id: "codex", model: "default", options: { sandbox: "workspace-write" }, editable: false }}
        onChange={vi.fn()}
      />
    );

    expect(screen.getByText(/Locked/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Model")).not.toBeInTheDocument();
  });
});
