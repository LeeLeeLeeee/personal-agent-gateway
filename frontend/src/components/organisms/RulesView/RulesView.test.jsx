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
});
