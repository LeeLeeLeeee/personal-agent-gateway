import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ArtifactsView } from "./index.jsx";
import { api } from "../../../api/client.js";
import { UiProvider } from "../../providers/UiProvider/index.jsx";

const artifacts = [
  { id: "a1", type: "image", title: "snap.png", relative_path: "captures/snap.png", mime_type: "image/png", size_bytes: 1400000, created_at: "2026-07-08T00:00:00Z", source_job_id: "j1", source_session_id: "s1", retention_class: "durable" },
  { id: "a2", type: "log", title: "run.log", relative_path: "logs/run.log", mime_type: "text/plain", size_bytes: 3000, created_at: "2026-07-08T00:00:00Z", source_job_id: "j2", source_session_id: "s2", retention_class: "temporary", metadata: { team_run_id: "run-1", task_id: "task-1" } }
];

function renderView() {
  return render(<UiProvider><ArtifactsView artifacts={artifacts} /></UiProvider>);
}

describe("ArtifactsView", () => {
  it("defaults to saved artifacts and exposes recent team outputs", async () => {
    renderView();
    expect(screen.getByText("snap.png")).toBeInTheDocument();
    expect(screen.queryByText("run.log")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /최근 산출물/i }));
    expect(screen.getByText("run.log")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /TEAM RUN run-1/i })).toBeInTheDocument();
    expect(screen.getByText("TASK task-1")).toBeInTheDocument();
  });

  it("loads cleanup candidates unchecked for review", async () => {
    vi.spyOn(api, "artifactCleanupPreview").mockResolvedValue({
      artifacts: [{ ...artifacts[1], expires_at: "2026-07-01T00:00:00Z" }],
      total_size_bytes: 3000
    });
    renderView();
    await userEvent.click(screen.getByRole("button", { name: /정리 후보/i }));
    expect(await screen.findByLabelText(/run.log 선택/i)).not.toBeChecked();
    expect(screen.getByRole("heading", { name: "Cleanup candidates" })).toBeInTheDocument();
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

  it("shows documents under the Documents filter", () => {
    render(<ArtifactsView artifacts={[
      { id: "d1", type: "document", title: "spec.pdf", relative_path: "files/x/spec.pdf", mime_type: "application/pdf", size_bytes: 2048, created_at: "2026-07-09T00:00:00Z" }
    ]} />);
    fireEvent.click(screen.getByRole("button", { name: "Documents" }));
    expect(screen.getByRole("button", { name: "Open spec.pdf" })).toBeInTheDocument();
  });

  it("renders an image type glyph in the compact result row", () => {
    render(<ArtifactsView artifacts={[
      { id: "i1", type: "image", title: "cat.png", relative_path: "files/x/cat.png", mime_type: "image/png", size_bytes: 2048, created_at: "2026-07-10T00:00:00Z" }
    ]} />);
    expect(screen.getByRole("button", { name: "Open cat.png" })).toHaveTextContent("▦");
  });

  it("opens a centered modal (dialog) when a card is clicked", () => {
    render(<ArtifactsView artifacts={[
      { id: "i1", type: "image", title: "cat.png", relative_path: "files/x/cat.png", mime_type: "image/png", size_bytes: 2048, created_at: "2026-07-10T00:00:00Z" }
    ]} />);
    fireEvent.click(screen.getByRole("button", { name: "Open cat.png" }));
    expect(screen.getByRole("dialog", { name: "cat.png" })).toBeInTheDocument();
  });

  it("uses grouped browser results for search and exposes selection deletion", async () => {
    const browser = vi.spyOn(api, "artifactBrowser").mockResolvedValue({
      items: [{
        artifact: artifacts[1],
        source_kind: "team_task_output",
        role: { code: "deliverable", label: "Deliverable" },
        breadcrumbs: [
          { kind: "team", id: "team-1", label: "Documentation team" },
          { kind: "team_run", id: "run-1", label: "Design system review" },
          { kind: "team_cycle", id: "cycle-3", label: "Cycle 3" },
          { kind: "team_task", id: "task-1", label: "Write verification checklist" }
        ],
        deletion: { allowed: true }
      }],
      counts: { saved: 1, recent: 1, cleanup: 0 }
    });
    const remove = vi.spyOn(api, "deleteArtifacts").mockResolvedValue({ deleted_ids: ["a2"], blocked: [], missing_ids: [] });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    renderView();
    await userEvent.click(screen.getByRole("button", { name: /최근 산출물/i }));
    expect(await screen.findByRole("heading", { name: "Documentation team" })).toBeInTheDocument();
    expect(screen.getByText("Design system review · Cycle 3 · Write verification checklist")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /선택 삭제/i }));
    await userEvent.click(screen.getByLabelText(/run.log 선택/i));
    await userEvent.click(screen.getByRole("button", { name: /선택 1개 삭제/i }));
    await userEvent.click(screen.getByRole("button", { name: "삭제" }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith(["a2"]));
    expect(browser).toHaveBeenCalledWith(expect.objectContaining({ segment: "recent" }));

    browser.mockRestore();
    remove.mockRestore();
    confirmSpy.mockRestore();
  });
});
