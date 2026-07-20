import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PersonaPicker } from "./index.jsx";

const personas = [{
  id: "p1",
  name: "Mail Manager",
  role: "Inbox triage",
  description: "Classifies incoming mail.",
  default_backend: "codex",
  default_model: "gpt-5"
}];

describe("PersonaPicker", () => {
  it("shows Persona identity and emits its id", async () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <PersonaPicker personas={personas} value="" onChange={onChange} />
    );

    expect(screen.getByRole("option", { name: "Mail Manager — Inbox triage" })).toBeInTheDocument();
    expect(screen.getByLabelText("Persona")).toHaveClass("persona-config-select");
    await userEvent.selectOptions(screen.getByLabelText("Persona"), "p1");

    expect(onChange).toHaveBeenCalledWith("p1");
    rerender(<PersonaPicker personas={personas} value="p1" onChange={onChange} />);
    expect(screen.getByText("codex / gpt-5")).toBeInTheDocument();
  });
});
