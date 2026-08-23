import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Sidebar } from "./index.jsx";

describe("Sidebar", () => {
  it("keeps the top-level navigation focused on work, knowledge, and system", () => {
    render(<Sidebar screen="chat" onScreenChange={vi.fn()} />);
    expect(screen.getByText("Team Runs")).toBeInTheDocument();
    expect(screen.getByText("Configuration")).toBeInTheDocument();
    expect(screen.getByText("Operations")).toBeInTheDocument();
    expect(screen.queryByText("Jobs")).not.toBeInTheDocument();
    expect(screen.queryByText("Schedules")).not.toBeInTheDocument();
    expect(screen.queryByText("Hooks")).not.toBeInTheDocument();
  });

  it("renders Home before Chat and marks it active", () => {
    render(<Sidebar screen="dashboard" onScreenChange={vi.fn()} />);
    const homeButton = screen.getByRole("button", { name: "Home" });
    expect(homeButton).toHaveAttribute("aria-current", "page");
    const navButtons = screen.getAllByRole("button").map((button) => button.textContent);
    expect(navButtons.indexOf("Home")).toBeLessThan(navButtons.findIndex((text) => text.startsWith("Chat")));
  });

  it("renders independent Library and Outputs navigation items", () => {
    render(<Sidebar screen="library" onScreenChange={vi.fn()} />);

    expect(screen.getByRole("button", { name: "Library" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "Outputs" })).toBeInTheDocument();
  });

  it("moves hook notifications to the Configuration badge", () => {
    render(<Sidebar screen="chat" hooksBadge={3} onScreenChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Configuration" })).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });
});
