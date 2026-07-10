import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusBadge } from "./index.jsx";

describe("StatusBadge", () => {
  it("renders a distinct label for team statuses instead of falling back to IDLE", () => {
    const { container } = render(<StatusBadge kind="planning" />);
    expect(screen.getByText("PLANNING")).toBeInTheDocument();
    expect(screen.queryByText("IDLE")).not.toBeInTheDocument();
    // active states show the pulsing dot
    expect(container.querySelector(".dot")).not.toBeNull();
    expect(container.querySelector(".badge-planning")).not.toBeNull();
  });

  it("renders a green success label for a completed terminal state", () => {
    const { container } = render(<StatusBadge kind="completed" />);
    expect(screen.getByText("COMPLETED")).toBeInTheDocument();
    expect(container.querySelector(".badge-completed")).not.toBeNull();
    expect(container.querySelector(".dot")).toBeNull();
  });

  it("preserves the existing running behavior", () => {
    const { container } = render(<StatusBadge kind="running" />);
    expect(screen.getByText("RUNNING")).toBeInTheDocument();
    expect(container.querySelector(".badge-running")).not.toBeNull();
    expect(container.querySelector(".dot")).not.toBeNull();
  });

  it("renders job statuses (succeeded/queued/waiting_approval) instead of IDLE", () => {
    const succeeded = render(<StatusBadge kind="succeeded" />);
    expect(screen.getByText("SUCCEEDED")).toBeInTheDocument();
    expect(succeeded.container.querySelector(".badge-succeeded")).not.toBeNull();
    expect(succeeded.container.querySelector(".dot")).toBeNull();
    succeeded.unmount();

    const queued = render(<StatusBadge kind="queued" />);
    expect(screen.getByText("QUEUED")).toBeInTheDocument();
    expect(queued.container.querySelector(".dot")).not.toBeNull();
    queued.unmount();

    render(<StatusBadge kind="waiting_approval" />);
    expect(screen.getByText("WAITING")).toBeInTheDocument();
  });

  it("falls back to IDLE for an unknown status", () => {
    render(<StatusBadge kind="totally-unknown" />);
    expect(screen.getByText("IDLE")).toBeInTheDocument();
  });

  it("renders completed_with_failures label", () => {
    render(<StatusBadge kind="completed_with_failures" />);
    expect(screen.getByText("COMPLETED*")).toBeInTheDocument();
  });
});
