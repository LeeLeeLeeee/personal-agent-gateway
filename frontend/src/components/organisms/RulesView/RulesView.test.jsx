import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { RulesView } from "./index.jsx";

const rules = {
  global: { personality: "global voice", rules: [{ level: "REQUIRED", text: "no destructive writes" }] },
  persona_baseline: { personality: "persona voice", rules: [{ level: "GUIDELINE", text: "be terse" }] },
  teams: [{ team_id: "t1", personality: "team voice", rules: [] }]
};
const teams = [{ id: "t1", name: "Release Crew" }];

describe("RulesView", () => {
  it("shows global rules by default and saves edits", async () => {
    const onSaveGlobal = vi.fn(async () => ({}));
    render(<RulesView rules={rules} teams={teams} onSaveGlobal={onSaveGlobal}
      onSavePersonaBaseline={vi.fn()} onSaveTeam={vi.fn()} />);
    expect(screen.getByDisplayValue("no destructive writes")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /save/i }));
    expect(onSaveGlobal).toHaveBeenCalledWith(expect.objectContaining({
      personality: "global voice",
      rules: expect.arrayContaining([{ level: "REQUIRED", text: "no destructive writes" }])
    }));
  });

  it("switches to persona baseline scope", async () => {
    render(<RulesView rules={rules} teams={teams} onSaveGlobal={vi.fn()}
      onSavePersonaBaseline={vi.fn()} onSaveTeam={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /persona baseline/i }));
    expect(screen.getByDisplayValue("be terse")).toBeInTheDocument();
  });

  it("adds a rule", async () => {
    render(<RulesView rules={rules} teams={teams} onSaveGlobal={vi.fn()}
      onSavePersonaBaseline={vi.fn()} onSaveTeam={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /add rule/i }));
    const inputs = screen.getAllByPlaceholderText(/rule text/i);
    expect(inputs.length).toBeGreaterThan(1);
  });

  it("saves persona baseline via onSavePersonaBaseline", async () => {
    const onSaveGlobal = vi.fn(async () => ({}));
    const onSavePersonaBaseline = vi.fn(async () => ({}));
    const onSaveTeam = vi.fn(async () => ({}));
    render(<RulesView rules={rules} teams={teams} onSaveGlobal={onSaveGlobal}
      onSavePersonaBaseline={onSavePersonaBaseline} onSaveTeam={onSaveTeam} />);
    await userEvent.click(screen.getByRole("button", { name: /persona baseline/i }));
    expect(screen.getByDisplayValue("be terse")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /save/i }));
    expect(onSavePersonaBaseline).toHaveBeenCalledWith({
      personality: "persona voice",
      rules: [{ level: "GUIDELINE", text: "be terse" }]
    });
    expect(onSaveGlobal).not.toHaveBeenCalled();
    expect(onSaveTeam).not.toHaveBeenCalled();
  });

  it("saves an individual team via onSaveTeam", async () => {
    const onSaveGlobal = vi.fn(async () => ({}));
    const onSavePersonaBaseline = vi.fn(async () => ({}));
    const onSaveTeam = vi.fn(async () => ({}));
    render(<RulesView rules={rules} teams={teams} onSaveGlobal={onSaveGlobal}
      onSavePersonaBaseline={onSavePersonaBaseline} onSaveTeam={onSaveTeam} />);
    await userEvent.click(screen.getByRole("button", { name: "Release Crew" }));
    expect(screen.getByDisplayValue("team voice")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /save/i }));
    expect(onSaveTeam).toHaveBeenCalledWith("t1", { personality: "team voice", rules: [] });
    expect(onSaveGlobal).not.toHaveBeenCalled();
  });
});
