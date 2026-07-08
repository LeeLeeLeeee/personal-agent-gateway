import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { AgentPicker } from "./index.jsx";

const agents = [
  {
    id: "codex",
    label: "Codex CLI",
    available: true,
    models: ["default"],
    default_model: "default",
    allow_custom_model: true,
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
    allow_custom_model: true,
    defaults: { effort: "medium" },
    options_schema: [{ name: "effort", kind: "select", choices: ["medium", "high"] }]
  }
];

function StatefulAgentPicker({ initialConfig, onChange }) {
  const [config, setConfig] = useState(initialConfig);

  function handleChange(nextConfig) {
    setConfig(nextConfig);
    onChange(nextConfig);
  }

  return <AgentPicker agents={agents} config={config} onChange={handleChange} />;
}

describe("AgentPicker", () => {
  it("shows editable available agents and disables unavailable agents", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();

    render(
      <StatefulAgentPicker
        initialConfig={{ agent_id: "codex", model: "default", options: {}, editable: true }}
        onChange={onChange}
      />,
    );

    expect(screen.getByText("Codex CLI")).toBeInTheDocument();
    expect(screen.getByText("Claude Code")).toBeInTheDocument();
    expect(screen.getByText("not found on PATH")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Claude Code/i })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: /Claude Code/i }));
    expect(onChange).not.toHaveBeenCalled();

    await user.clear(screen.getByLabelText("Model"));
    await user.type(screen.getByLabelText("Model"), "gpt-5.4");

    expect(onChange).toHaveBeenLastCalledWith({
      agent_id: "codex",
      model: "gpt-5.4",
      options: {},
      editable: true
    });
  });

  it("keeps editable agent choices visible when the current config is unavailable", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();

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
    expect(screen.getByRole("button", { name: /Claude Code/i })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: /Codex CLI/i }));

    expect(onChange).toHaveBeenCalledWith({
      agent_id: "codex",
      model: "default",
      options: { sandbox: "workspace-write", approval_policy: "never" },
      editable: true
    });
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
