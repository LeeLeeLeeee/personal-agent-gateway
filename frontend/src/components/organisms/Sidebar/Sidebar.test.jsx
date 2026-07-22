import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Sidebar } from "./index.jsx";

describe("Sidebar", () => {
  it("renders Team Runs, Teams, Personas, Rules nav items", () => {
    render(<Sidebar screen="chat" onScreenChange={vi.fn()} />);
    expect(screen.getByText("Team Runs")).toBeInTheDocument();
    expect(screen.getByText("Teams")).toBeInTheDocument();
    expect(screen.getByText("Personas")).toBeInTheDocument();
    expect(screen.getByText("Rules")).toBeInTheDocument();
    expect(screen.getByText("Operations")).toBeInTheDocument();
  });

  it("renders a Dashboard nav item before Chat and marks it active", () => {
    render(<Sidebar screen="dashboard" onScreenChange={vi.fn()} />);
    const dashboardButton = screen.getByRole("button", { name: "Dashboard" });
    expect(dashboardButton).toBeInTheDocument();
    expect(dashboardButton).toHaveAttribute("aria-current", "page");
    const navButtons = screen.getAllByRole("button").map((button) => button.textContent);
    expect(navButtons.indexOf("Dashboard")).toBeLessThan(navButtons.findIndex((text) => text.startsWith("Chat")));
  });

  it("renders a Hooks nav item with a badge when hooksBadge > 0", () => {
    render(<Sidebar screen="chat" hooksBadge={3} onScreenChange={vi.fn()} />);
    expect(screen.getByText("Hooks")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });
});
