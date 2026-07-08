import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PersonaLibrary } from "./index.jsx";

describe("PersonaLibrary", () => {
  it("renders personas and triggers seeding of defaults", async () => {
    const onSeedDefaults = vi.fn();
    render(
      <PersonaLibrary
        personas={[{ id: "p1", name: "Tech Lead", role: "Planning", responsibilities: ["Plan"] }]}
        onCreate={vi.fn()}
        onSeedDefaults={onSeedDefaults}
      />
    );

    expect(screen.getByText("Tech Lead")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /seed defaults/i }));
    expect(onSeedDefaults).toHaveBeenCalled();
  });

  it("does not render a seed defaults button when onSeedDefaults is omitted", () => {
    render(<PersonaLibrary personas={[]} onCreate={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /seed defaults/i })).not.toBeInTheDocument();
  });

  it("submits an assembled persona payload from the create form", async () => {
    const onCreate = vi.fn();
    render(<PersonaLibrary personas={[]} onCreate={onCreate} />);

    await userEvent.type(screen.getByLabelText("Name"), "Growth Hacker");
    await userEvent.type(screen.getByLabelText("Role"), "Growth");
    await userEvent.type(screen.getByLabelText("Responsibilities"), "Find channels\nRun experiments");
    await userEvent.click(screen.getByRole("button", { name: /save persona/i }));

    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({
      name: "Growth Hacker",
      role: "Growth",
      responsibilities: ["Find channels", "Run experiments"],
      constraints: []
    }));
  });
});
