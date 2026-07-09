import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PersonaLibrary } from "./index.jsx";

const personas = [
  { id: "p1", name: "Tech Lead", role: "Planning", description: "Owns the plan", responsibilities: ["Plan"], constraints: [] },
  { id: "p2", name: "QA Tester", role: "Verification", responsibilities: [], constraints: ["No prod data"] }
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
    render(<PersonaLibrary personas={personas} onCreate={onCreate} onSave={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: /new persona/i }));
    expect(screen.getByLabelText("Name")).toHaveValue("");

    await userEvent.type(screen.getByLabelText("Name"), "Growth Hacker");
    await userEvent.type(screen.getByLabelText("Role"), "Growth");
    await userEvent.type(screen.getByLabelText("Responsibilities"), "Find channels\nRun experiments");
    await userEvent.click(screen.getByRole("button", { name: /create persona/i }));

    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({
      name: "Growth Hacker",
      role: "Growth",
      responsibilities: ["Find channels", "Run experiments"],
      constraints: []
    }));
  });

  it("saves edits to an existing persona", async () => {
    const onSave = vi.fn();
    render(<PersonaLibrary personas={personas} onCreate={vi.fn()} onSave={onSave} />);

    const name = screen.getByLabelText("Name");
    await userEvent.clear(name);
    await userEvent.type(name, "Lead Architect");
    await userEvent.click(screen.getByRole("button", { name: /save persona/i }));

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
