import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ArtifactsView } from "./index.jsx";
import { UiProvider } from "../../providers/UiProvider/index.jsx";

const artifacts = [
  { id: "a1", type: "image", title: "snap.png", relative_path: "captures/snap.png", mime_type: "image/png", size_bytes: 1400000, created_at: "2026-07-08T00:00:00Z", source_job_id: "j1", source_session_id: "s1" },
  { id: "a2", type: "log", title: "run.log", relative_path: "logs/run.log", mime_type: "text/plain", size_bytes: 3000, created_at: "2026-07-08T00:00:00Z", source_job_id: "j2", source_session_id: "s2" }
];

function renderView() {
  return render(<UiProvider><ArtifactsView artifacts={artifacts} /></UiProvider>);
}

describe("ArtifactsView", () => {
  it("shows a card grid and filters by type", async () => {
    renderView();
    expect(screen.getByText("snap.png")).toBeInTheDocument();
    expect(screen.getByText("run.log")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^Images$/i }));
    expect(screen.getByText("snap.png")).toBeInTheDocument();
    expect(screen.queryByText("run.log")).not.toBeInTheDocument();
  });

  it("opens a viewer drawer with provenance and copy path", async () => {
    Object.defineProperty(navigator, "clipboard", { value: { writeText: vi.fn() }, configurable: true });
    vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
    renderView();
    await userEvent.click(screen.getByRole("button", { name: /open snap.png/i }));
    expect(screen.getByText("captures/snap.png")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /copy path/i }));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith("captures/snap.png");
  });
});
