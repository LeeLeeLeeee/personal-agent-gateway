import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { JobsView } from "./index.jsx";

const jobs = [
  { id: "j1", title: "Extract audio", capability_id: "ffmpeg.extract-audio", source: "chat", status: "succeeded", input: { source_file: "a.mov" }, command_preview: "ffmpeg -i a.mov", created_at: "2026-07-08T00:00:00Z" },
  { id: "j2", title: "Nightly compress", capability_id: "ffmpeg.thumbnail", source: "schedule", source_job_id: "j0", status: "failed", retryable: true, input: {}, command_preview: "ffmpeg ...", created_at: "2026-07-08T00:00:00Z" }
];

describe("JobsView", () => {
  it("filters by status independently of source", async () => {
    render(<JobsView jobs={jobs} onLoadEvents={vi.fn().mockResolvedValue([])} />);
    expect(screen.getByText("Extract audio")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^Failed$/i }));
    expect(screen.queryByText("Extract audio")).not.toBeInTheDocument();
    expect(screen.getByText("Nightly compress")).toBeInTheDocument();
  });

  it("opens a detail drawer with command and loads logs", async () => {
    const onLoadEvents = vi.fn().mockResolvedValue([{ id: "e1", kind: "log", payload: { line: "started" }, created_at: "2026-07-08T00:00:00Z" }]);
    render(<JobsView jobs={jobs} onLoadEvents={onLoadEvents} />);
    await userEvent.click(screen.getByRole("button", { name: /open Extract audio/i }));
    expect(screen.getByText("ffmpeg -i a.mov")).toBeInTheDocument();
    expect(onLoadEvents).toHaveBeenCalledWith("j1");
    expect(await screen.findByText(/started/)).toBeInTheDocument();
  });

  it("shows lineage and retries only retryable jobs", async () => {
    const onRetry = vi.fn().mockResolvedValue({ ...jobs[1], id: "j3", status: "queued" });
    render(
      <JobsView
        jobs={jobs}
        onLoadEvents={vi.fn().mockResolvedValue([])}
        onRetry={onRetry}
      />
    );

    await userEvent.click(screen.getByRole("button", { name: /open Nightly compress/i }));
    expect(screen.getByText("j0")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^Retry$/i }));
    expect(onRetry).toHaveBeenCalledWith("j2");

    await userEvent.click(screen.getByRole("button", { name: /open Extract audio/i }));
    expect(screen.queryByRole("button", { name: /^Retry$/i })).not.toBeInTheDocument();
  });
});
