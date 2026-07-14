import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { vi } from "vitest";
import { TeamRunDetail } from "./index.jsx";

describe("TeamRunDetail", () => {
  it("renders header, agent lanes, task board, and activity", () => {
    render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "Design", status: "running", run_mode: "plan_and_execute" },
          agents: [{ id: "a1", name: "Tech Lead", role: "Planning", status: "running", current_task_id: null }],
          tasks: [{ id: "t1", title: "Define schema", description: "tables", status: "in_progress" }],
          messages: [{ id: "m1", kind: "note", content: "Planning started", created_at: "2026-07-08T00:00:00Z" }]
        }}
      />
    );

    expect(screen.getByText("Design")).toBeInTheDocument();
    expect(screen.getByText("Tech Lead")).toBeInTheDocument();
    expect(screen.getByText("Define schema")).toBeInTheDocument();
    expect(screen.getByText("Planning started")).toBeInTheDocument();
  });

  it("renders a placeholder when no run is selected", () => {
    render(<TeamRunDetail detail={null} />);
    expect(screen.getByText("No team run selected.")).toBeInTheDocument();
  });

  it("submits additional work through onAddWork", async () => {
    const onAddWork = vi.fn();
    render(
      <TeamRunDetail
        onAddWork={onAddWork}
        detail={{
          run: { id: "r1", goal: "Design", status: "running", run_mode: "plan_and_execute" },
          agents: [],
          tasks: [],
          messages: []
        }}
      />
    );

    await userEvent.click(screen.getByRole("button", { name: "Add work" }));
    await userEvent.type(screen.getByLabelText("Additional work"), "also write docs");
    await userEvent.click(screen.getByRole("button", { name: "Request work" }));

    expect(onAddWork).toHaveBeenCalledWith("also write docs");
  });

  it("disables the add-work button while a submit is in flight", async () => {
    const onAddWork = vi.fn(() => new Promise(() => {}));
    render(
      <TeamRunDetail
        onAddWork={onAddWork}
        detail={{
          run: { id: "r1", goal: "Design", status: "running", run_mode: "plan_and_execute" },
          agents: [],
          tasks: [],
          messages: []
        }}
      />
    );

    await userEvent.click(screen.getByRole("button", { name: "Add work" }));
    await userEvent.type(screen.getByLabelText("Additional work"), "also write docs");
    await userEvent.click(screen.getByRole("button", { name: "Request work" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Request work" })).toBeDisabled();
    });
  });

  it("labels the add-work button for reopening a finished run", async () => {
    render(
      <TeamRunDetail
        onAddWork={vi.fn()}
        detail={{
          run: { id: "r1", goal: "Design", status: "completed", run_mode: "plan_and_execute" },
          agents: [],
          tasks: [],
          messages: []
        }}
      />
    );
    await userEvent.click(screen.getByRole("button", { name: "Add work" }));
    expect(screen.getByRole("button", { name: "Reopen & request" })).toBeInTheDocument();
  });

  it("marks the current phase in the stepper", () => {
    render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "Design", status: "summarizing", run_mode: "plan_and_execute" },
          agents: [],
          tasks: [],
          messages: []
        }}
      />
    );
    expect(screen.getByText("Summarizing").closest(".team-phase")).toHaveAttribute("aria-current", "step");
    expect(screen.getByText("Planning").closest(".team-phase")).not.toHaveAttribute("aria-current");
  });

  it("identifies task documents on the board and opens them from the task", async () => {
    const { container } = render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "Design", status: "completed", run_mode: "plan_and_execute" },
          agents: [
            { id: "a1", name: "Lead", role: "leader", status: "completed" },
            { id: "a2", name: "Worker", role: "member", status: "completed" }
          ],
          tasks: [{ id: "t1", title: "Build API", status: "completed" }],
          messages: [
            { id: "m1", kind: "query", sender_agent_id: "a2", content: "which schema?", created_at: "2026-07-13T00:00:00Z" },
            { id: "m2", kind: "answer", sender_agent_id: "a1", content: "use schema X", created_at: "2026-07-13T00:01:00Z" },
            { id: "m3", kind: "agent_output", sender_agent_id: "a2", content: "API built", metadata: { task_id: "t1" }, created_at: "2026-07-13T00:02:00Z" }
          ]
        }}
      />
    );

    expect(screen.getByText("1 documents")).toBeInTheDocument();
    expect(screen.getByText("DOCS 1")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: /SHARED \/ HANDOFFS/ }));
    const handoffsSection = container.querySelector(".team-handoffs");
    expect(within(handoffsSection).getByText("which schema?")).toBeInTheDocument();
    expect(within(handoffsSection).getByText("use schema X")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Open task Build API" }));
    const taskDialog = screen.getByRole("dialog", { name: "Task details: Build API" });
    expect(within(taskDialog).getByText("API built")).toBeInTheDocument();
    expect(within(taskDialog).getByText("SHARED DOCUMENTS · 1")).toBeInTheDocument();
  });

  it("only offers add work for started plan-and-execute runs", () => {
    const { rerender } = render(
      <TeamRunDetail
        onAddWork={vi.fn()}
        detail={{ run: { id: "r1", goal: "Design", status: "running", run_mode: "planning_only" }, agents: [], tasks: [], messages: [] }}
      />
    );
    expect(screen.queryByRole("button", { name: "Add work" })).not.toBeInTheDocument();

    rerender(
      <TeamRunDetail
        onAddWork={vi.fn()}
        detail={{ run: { id: "r1", goal: "Design", status: "draft", run_mode: "plan_and_execute" }, agents: [], tasks: [], messages: [] }}
      />
    );
    expect(screen.queryByRole("button", { name: "Add work" })).not.toBeInTheDocument();
  });

  it("offers manual resume for interrupted runs without marking a phase active", async () => {
    const onResume = vi.fn(() => new Promise(() => {}));
    const { container } = render(
      <TeamRunDetail
        onAddWork={vi.fn()}
        onResume={onResume}
        detail={{
          run: { id: "r1", goal: "Design", status: "interrupted", run_mode: "plan_and_execute" },
          agents: [],
          tasks: [{ id: "t1", title: "Continue UI", status: "pending" }],
          messages: []
        }}
      />
    );

    expect(screen.getByText("Run interrupted")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add work" })).not.toBeInTheDocument();
    expect(container.querySelector('[aria-current="step"]')).toBeNull();

    const resume = screen.getByRole("button", { name: "Resume" });
    await userEvent.click(resume);
    expect(onResume).toHaveBeenCalledTimes(1);
    expect(resume).toBeDisabled();
  });

  it("offers Retry only for a failed task in a failed terminal run", async () => {
    const onRetryTask = vi.fn(() => new Promise(() => {}));
    const { rerender } = render(
      <TeamRunDetail
        onRetryTask={onRetryTask}
        detail={{
          run: { id: "r1", goal: "Design", status: "completed_with_failures", run_mode: "plan_and_execute" },
          agents: [],
          tasks: [{ id: "t1", title: "Run QA", status: "failed", error_message: "timed out" }],
          messages: []
        }}
      />
    );

    await userEvent.click(screen.getByRole("button", { name: "Open task Run QA" }));
    const retry = screen.getByRole("button", { name: "Retry failed task" });
    await userEvent.click(retry);
    expect(onRetryTask).toHaveBeenCalledWith("t1");
    expect(retry).toBeDisabled();

    rerender(
      <TeamRunDetail
        onRetryTask={vi.fn()}
        detail={{
          run: { id: "r1", goal: "Design", status: "completed", run_mode: "plan_and_execute" },
          agents: [],
          tasks: [{ id: "t1", title: "Run QA", status: "failed" }],
          messages: []
        }}
      />
    );
    expect(screen.queryByRole("button", { name: "Retry failed task" })).not.toBeInTheDocument();
  });

  it("lists workspace documents and opens a preview", async () => {
    const onLoadDocument = vi.fn(async () => ({ path: "notes.md", kind: "md", previewable: true, content: "# hi" }));
    render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "Design", status: "running", run_mode: "plan_and_execute" },
          agents: [],
          tasks: [],
          messages: []
        }}
        documents={[{ path: "notes.md", kind: "md", previewable: true, size: 10 }]}
        onLoadDocument={onLoadDocument}
      />
    );
    await userEvent.click(screen.getByRole("tab", { name: /DOCUMENTS/ }));
    await userEvent.click(screen.getByText("notes.md"));
    expect(onLoadDocument).toHaveBeenCalledWith("notes.md");
    expect(await screen.findByRole("heading", { name: "hi" })).toBeInTheDocument();
  });

  it("switches between detail tabs (activity default, results, documents)", async () => {
    render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "Design", status: "completed", run_mode: "plan_and_execute", summary: "All shipped." },
          agents: [{ id: "a1", name: "Worker", role: "member", status: "completed" }],
          tasks: [],
          messages: [
            { id: "m1", kind: "note", content: "Planning started", created_at: "2026-07-08T00:00:00Z" },
            { id: "m2", kind: "agent_output", sender_agent_id: "a1", content: "Feature built", created_at: "2026-07-08T00:01:00Z" }
          ]
        }}
      />
    );

    // LIVE ACTIVITY is the default tab
    expect(screen.getByText("Planning started")).toBeInTheDocument();

    // RESULTS shows agent reports + final summary, and unmounts the activity timeline
    await userEvent.click(screen.getByRole("tab", { name: "RESULTS" }));
    expect(screen.getByText("Feature built")).toBeInTheDocument();
    expect(screen.getByText("All shipped.")).toBeInTheDocument();
    expect(screen.queryByText("Planning started")).not.toBeInTheDocument();
  });
});
