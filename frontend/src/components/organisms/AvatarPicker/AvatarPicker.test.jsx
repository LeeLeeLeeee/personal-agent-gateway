import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AvatarPicker } from "./index.jsx";

const avatars = [
  { slug: "dev-glasses", label: "Developer (glasses)", category: "person" },
  { slug: "robot", label: "Robot", category: "tech" },
  { slug: "wolf", label: "Wolf", category: "animal" },
  { slug: "ghost", label: "Ghost", category: "creature" }
];

describe("AvatarPicker", () => {
  it("renders the provided avatars grouped by category", () => {
    render(<AvatarPicker avatars={avatars} value="" onSelect={vi.fn()} />);

    expect(screen.getByAltText("Developer (glasses)")).toBeInTheDocument();
    expect(screen.getByAltText("Robot")).toBeInTheDocument();
    expect(screen.getByAltText("Wolf")).toBeInTheDocument();
    expect(screen.getByAltText("Ghost")).toBeInTheDocument();
  });

  it("fires onSelect with the tile's slug when clicked", async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();
    render(<AvatarPicker avatars={avatars} value="" onSelect={onSelect} />);

    await user.click(screen.getByRole("button", { name: "Robot" }));

    expect(onSelect).toHaveBeenCalledWith("robot");
  });

  it("marks the tile matching value as selected", () => {
    render(<AvatarPicker avatars={avatars} value="wolf" onSelect={vi.fn()} />);

    expect(screen.getByRole("button", { name: "Wolf" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Robot" })).toHaveAttribute("aria-pressed", "false");
  });
});
