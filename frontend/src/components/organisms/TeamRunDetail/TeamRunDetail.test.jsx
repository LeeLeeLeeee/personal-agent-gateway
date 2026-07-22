import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { vi } from "vitest";
import { TeamRunDetail } from "./index.jsx";

describe("TeamRunDetail", () => {
  it("renders the overview first and moves tasks and activity into tabs", async () => {
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
    expect(screen.queryByText("Define schema")).not.toBeInTheDocument();
    expect(screen.queryByText("Planning started")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));
    expect(screen.getByText("Define schema")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "ACTIVITY" }));
    expect(screen.getByText("Planning started")).toBeInTheDocument();
  });

  it("renders a placeholder when no run is selected", () => {
    render(<TeamRunDetail detail={null} />);
    expect(screen.getByText("No team run selected.")).toBeInTheDocument();
  });

  it("shows loading and load-error states without flashing the empty placeholder", () => {
    const { rerender } = render(<TeamRunDetail detail={null} loading />);

    expect(screen.getByRole("status")).toHaveTextContent("LOADING TEAM RUN...");
    expect(screen.queryByText("No team run selected.")).not.toBeInTheDocument();

    rerender(<TeamRunDetail detail={null} loadError />);
    expect(screen.getByText("Team run could not be loaded. Use Retry request above.")).toBeInTheDocument();
    expect(screen.queryByText("No team run selected.")).not.toBeInTheDocument();
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

    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));
    expect(screen.getByText("DOCS 1")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "OVERVIEW" }));
    await userEvent.click(screen.getByText(/SHARED \/ HANDOFFS/));
    const handoffsSection = container.querySelector(".team-handoffs");
    expect(within(handoffsSection).getByText("which schema?")).toBeInTheDocument();
    expect(within(handoffsSection).getByText("use schema X")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));
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

    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));
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
    const onLoadDocument = vi.fn(async () => ({ path: "docs/notes.md", kind: "md", previewable: true, content: "# hi" }));
    render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "Design", status: "running", run_mode: "plan_and_execute" },
          agents: [],
          tasks: [],
          messages: []
        }}
        documents={[{ path: "docs/notes.md", kind: "md", previewable: true, size: 10 }]}
        onLoadDocument={onLoadDocument}
      />
    );
    await userEvent.click(screen.getByRole("tab", { name: /FILES/ }));
    await userEvent.click(screen.getByText("notes.md"));
    expect(screen.getByText("docs")).toBeInTheDocument();
    expect(onLoadDocument).toHaveBeenCalledWith("docs/notes.md");
    expect(await screen.findByRole("heading", { name: "hi" })).toBeInTheDocument();
  });

  it("shows overview by default and keeps reports collapsed away from raw activity", async () => {
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

    expect(screen.getByText("All shipped.")).toBeInTheDocument();
    expect(screen.queryByText("Planning started")).not.toBeInTheDocument();
    expect(screen.getByText("Feature built")).not.toBeVisible();

    await userEvent.click(screen.getByText(/AGENT REPORTS/));
    expect(screen.getByText("Feature built")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "ACTIVITY" }));
    expect(screen.getByText("Planning started")).toBeInTheDocument();
  });

  it("renders continuous lifecycle Cycle history newest first", async () => {
    const { container } = render(
      <TeamRunDetail detail={{
        run: {
          id: "mail-run",
          goal: "Mail inbox",
          status: "running",
          run_mode: "plan_and_execute",
          lifecycle_mode: "continuous"
        },
        agents: [],
        tasks: [],
        messages: [],
        cycles: [
          {
            id: "c1", sequence: 1, source_type: "hook", source_id: "hook-run-1",
            status: "completed", rounds_used: 1, rounds_budget: 8, summary: "First mail done"
          },
          {
            id: "c2", sequence: 2, source_type: "hook", source_id: "hook-run-2",
            status: "queued", rounds_used: 0, rounds_budget: 8
          }
        ]
      }} />
    );

    await userEvent.click(screen.getByRole("tab", { name: /HISTORY/ }));
    expect(screen.getByText("continuous")).toBeInTheDocument();
    expect(screen.getByText("hook · hook-run-2")).toBeInTheDocument();
    expect(screen.getByText("First mail done")).toBeInTheDocument();
    expect([...container.querySelectorAll(".team-cycle-sequence")].map((node) => node.textContent))
      .toEqual(["CYCLE #2", "CYCLE #1"]);
  });

  it("triggers from the latest settled Cycle and clears instructions only when accepted", async () => {
    const onTriggerCycle = vi.fn()
      .mockResolvedValueOnce(true)
      .mockResolvedValueOnce(false);
    render(<TeamRunDetail
      detail={{
        run: {
          id: "r1",
          goal: "Maintain",
          status: "running",
          run_mode: "plan_and_execute",
          lifecycle_mode: "continuous",
          execution_policy: "triggered"
        },
        policyStatus: "running",
        queueCount: 2,
        activeRequest: { id: "request-9", status: "dispatching" },
        agents: [], tasks: [], messages: [],
        cycles: [
          { id: "c9", sequence: 9, status: "queued", summary: "not settled" },
          { id: "c7", sequence: 7, status: "completed", summary: "older result" },
          { id: "c8", sequence: 8, status: "completed_with_failures", summary: "latest result" }
        ]
      }}
      onTriggerCycle={onTriggerCycle}
    />);

    const policyPanel = screen.getByRole("region", { name: "Cycle policy" });
    expect(within(policyPanel).getByText("latest result")).toBeInTheDocument();
    expect(within(policyPanel).queryByText("older result")).not.toBeInTheDocument();
    expect(within(policyPanel).getByText(/QUEUE · 2/)).toBeInTheDocument();
    expect(within(policyPanel).getByText(/ACTIVE REQUEST · request-9/)).toBeInTheDocument();

    const instruction = screen.getByLabelText("Cycle instruction");
    await userEvent.type(instruction, "  next work  ");
    await userEvent.click(screen.getByRole("button", { name: "Trigger cycle" }));
    expect(onTriggerCycle).toHaveBeenLastCalledWith({
      instruction: "next work",
      previous_cycle_id: "c8"
    });
    expect(instruction).toHaveValue("");

    await userEvent.type(instruction, "keep this draft");
    await userEvent.click(screen.getByRole("button", { name: "Trigger cycle" }));
    expect(instruction).toHaveValue("keep this draft");
  });

  it("shows AUTO progress and locks paused-failure actions while one is pending", async () => {
    const onContinueAuto = vi.fn(() => new Promise(() => {}));
    const onRetryAuto = vi.fn();
    render(<TeamRunDetail
      detail={{
        run: {
          id: "r1", goal: "Maintain", status: "failed",
          run_mode: "plan_and_execute", lifecycle_mode: "continuous",
          execution_policy: "auto"
        },
        policyStatus: "paused_failure",
        queueCount: 1,
        activeAutoSeries: {
          id: "s1", target_slots: 5, settled_slots: 2,
          status: "paused_failure", next_run_at: "2026-07-20T06:00:00Z"
        },
        cycles: [], tasks: [], agents: [], messages: []
      }}
      onContinueAuto={onContinueAuto}
      onRetryAuto={onRetryAuto}
      onAddWork={vi.fn()}
    />);

    expect(screen.getByText("2 / 5 SETTLED")).toBeInTheDocument();
    expect(screen.getByText(/QUEUE · 1/)).toBeInTheDocument();
    expect(screen.getByText(/NEXT ·/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add work" })).not.toBeInTheDocument();

    const continueButton = screen.getByRole("button", { name: "Continue" });
    const retryButton = screen.getByRole("button", { name: "Retry" });
    await userEvent.click(continueButton);
    expect(onContinueAuto).toHaveBeenCalledWith("s1");
    expect(continueButton).toBeDisabled();
    expect(retryButton).toBeDisabled();
  });

  it("counts down to the next AUTO Cycle, clamps at zero, and cleans up its timer", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-20T05:59:57Z"));
    const setIntervalSpy = vi.spyOn(window, "setInterval");
    const clearIntervalSpy = vi.spyOn(window, "clearInterval");
    const detail = {
      run: {
        id: "r1", goal: "Maintain", status: "completed",
        run_mode: "plan_and_execute", lifecycle_mode: "continuous",
        execution_policy: "auto"
      },
      policyStatus: "waiting_interval",
      queueCount: 0,
      activeAutoSeries: {
        id: "s1", target_slots: 5, settled_slots: 2,
        status: "waiting_interval", next_run_at: "2026-07-20T06:00:00Z"
      },
      cycles: [], tasks: [], agents: [], messages: []
    };

    try {
      const { rerender, unmount } = render(<TeamRunDetail detail={detail} />);

      expect(screen.getByText("NEXT · 3s")).toBeInTheDocument();
      expect(setIntervalSpy).toHaveBeenCalledTimes(1);

      act(() => vi.advanceTimersByTime(1000));
      expect(screen.getByText("NEXT · 2s")).toBeInTheDocument();

      act(() => vi.advanceTimersByTime(4000));
      expect(screen.getByText("NEXT · 0s")).toBeInTheDocument();
      expect(clearIntervalSpy).toHaveBeenCalled();

      rerender(<TeamRunDetail detail={{
        ...detail,
        activeAutoSeries: {
          ...detail.activeAutoSeries,
          next_run_at: "2026-07-20T06:00:10Z"
        }
      }} />);
      expect(setIntervalSpy).toHaveBeenCalledTimes(2);
      unmount();
      expect(clearIntervalSpy).toHaveBeenCalledTimes(2);

      const withoutNextRun = render(<TeamRunDetail detail={{
        ...detail,
        activeAutoSeries: { ...detail.activeAutoSeries, next_run_at: null }
      }} />);
      expect(screen.queryByText(/NEXT ·/)).not.toBeInTheDocument();
      expect(setIntervalSpy).toHaveBeenCalledTimes(2);
      withoutNextRun.unmount();
    } finally {
      setIntervalSpy.mockRestore();
      clearIntervalSpy.mockRestore();
      vi.useRealTimers();
    }
  });

  it("shows only the AUTO action group valid for completed and interrupted policies", async () => {
    const onRestartAuto = vi.fn(() => new Promise(() => {}));
    const onResume = vi.fn(() => new Promise(() => {}));
    const detail = {
      run: {
        id: "r1", goal: "Maintain", status: "completed",
        run_mode: "plan_and_execute", lifecycle_mode: "continuous",
        execution_policy: "auto"
      },
      policyStatus: "auto_completed",
      queueCount: 0,
      activeAutoSeries: null,
      cycles: [], tasks: [], agents: [], messages: []
    };
    const { rerender } = render(<TeamRunDetail
      detail={detail}
      onRestartAuto={onRestartAuto}
      onContinueAuto={vi.fn()}
      onRetryAuto={vi.fn()}
    />);

    expect(screen.queryByRole("button", { name: "Continue" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    const restart = screen.getByRole("button", { name: "Restart" });
    await userEvent.click(restart);
    expect(onRestartAuto).toHaveBeenCalledTimes(1);
    expect(restart).toBeDisabled();

    rerender(<TeamRunDetail
      detail={{
        ...detail,
        run: { ...detail.run, status: "interrupted" },
        policyStatus: "paused_interrupted",
        activeAutoSeries: {
          id: "s1", target_slots: 3, settled_slots: 2, status: "paused_interrupted"
        }
      }}
      onResume={onResume}
      onRestartAuto={vi.fn()}
      onContinueAuto={vi.fn()}
      onRetryAuto={vi.fn()}
    />);
    expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Restart" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Continue" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("orders results and activity newest first while keeping handoff pairs intact", async () => {
    const { container } = render(
      <TeamRunDetail detail={{
        run: { id: "r1", goal: "Sort", status: "completed", run_mode: "plan_and_execute" },
        agents: [
          { id: "lead", name: "Lead", role: "leader", status: "completed" },
          { id: "worker", name: "Worker", role: "member", status: "completed" }
        ],
        tasks: [],
        messages: [
          { id: "q1", kind: "query", sender_agent_id: "worker", content: "old question", created_at: "2026-07-15T01:00:00Z" },
          { id: "a1", kind: "answer", sender_agent_id: "lead", content: "old answer", created_at: "2026-07-15T01:01:00Z" },
          { id: "r1", kind: "agent_output", sender_agent_id: "worker", content: "old report", created_at: "2026-07-15T01:02:00Z" },
          { id: "q2", kind: "query", sender_agent_id: "worker", content: "new question", created_at: "2026-07-15T02:00:00Z" },
          { id: "a2", kind: "answer", sender_agent_id: "lead", content: "new answer", created_at: "2026-07-15T02:01:00Z" },
          { id: "r2", kind: "agent_output", sender_agent_id: "worker", content: "new report", created_at: "2026-07-15T02:02:00Z" }
        ]
      }} />
    );

    await userEvent.click(screen.getByRole("tab", { name: "ACTIVITY" }));
    expect([...container.querySelectorAll(".tl-detail")].map((node) => node.textContent)).toEqual([
      "new report", "new answer", "new question", "old report", "old answer", "old question"
    ]);

    await userEvent.click(screen.getByRole("tab", { name: "OVERVIEW" }));
    await userEvent.click(screen.getByText(/AGENT REPORTS/));
    expect([...container.querySelectorAll(".team-agent-report-body")].map((node) => node.textContent)).toEqual([
      "new report", "old report"
    ]);

    await userEvent.click(screen.getByText(/SHARED \/ HANDOFFS/));
    const handoffs = [...container.querySelectorAll(".team-handoff")];
    expect(handoffs[0]).toHaveTextContent("new question");
    expect(handoffs[0]).toHaveTextContent("new answer");
    expect(handoffs[1]).toHaveTextContent("old question");
    expect(handoffs[1]).toHaveTextContent("old answer");
  });

  it("pairs answers by query id even when answers arrive out of order", async () => {
    const { container } = render(
      <TeamRunDetail detail={{
        run: { id: "r1", goal: "Link", status: "waiting_for_user", run_mode: "plan_and_execute" },
        agents: [
          { id: "lead", name: "Lead", role: "leader", status: "waiting" },
          { id: "worker", name: "Worker", role: "member", status: "waiting" }
        ],
        tasks: [],
        messages: [
          { id: "q1", kind: "query", sender_agent_id: "worker", content: "first question", created_at: "2026-07-15T01:00:00Z" },
          { id: "q2", kind: "query", sender_agent_id: "worker", content: "second question", created_at: "2026-07-15T01:01:00Z" },
          { id: "q3", kind: "query", sender_agent_id: "worker", content: "still waiting", created_at: "2026-07-15T01:02:00Z" },
          { id: "a2", kind: "answer", sender_agent_id: "lead", content: "second answer", metadata: { query_id: "q2" }, created_at: "2026-07-15T02:00:00Z" },
          { id: "a1", kind: "answer", sender_agent_id: "lead", content: "first answer", metadata: { query_id: "q1" }, created_at: "2026-07-15T02:01:00Z" }
        ]
      }} />
    );

    await userEvent.click(screen.getByText(/SHARED \/ HANDOFFS/));
    const byQuestion = new Map(
      [...container.querySelectorAll(".team-handoff")].map((handoff) => [
        handoff.querySelector(".team-handoff-q .team-handoff-text").textContent,
        handoff.textContent
      ])
    );
    expect(byQuestion.get("first question")).toContain("first answer");
    expect(byQuestion.get("second question")).toContain("second answer");
    expect(byQuestion.get("still waiting")).toContain("awaiting answer");
  });

  it("shows assigned task names and a phase fallback for the leader", async () => {
    render(<TeamRunDetail detail={{
      run: { id: "r1", goal: "Work", status: "running", run_mode: "plan_and_execute" },
      agents: [
        { id: "lead", name: "Lead", role: "leader", status: "running", current_task_id: null },
        { id: "worker", name: "Worker", role: "member", status: "running", current_task_id: "t1" }
      ],
      tasks: [{ id: "t1", title: "Build API", status: "in_progress", owner_agent_id: "worker" }],
      messages: []
    }} />);

    expect(screen.getByText("Coordinating agents")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));
    expect(screen.getByText("Build API")).toBeInTheDocument();
    expect(screen.getByText("Worker", { selector: ".team-task-owner-name" })).toBeInTheDocument();
  });

  it("offers Stop run only for active runs and disables it while canceling", async () => {
    const onCancel = vi.fn(() => new Promise(() => {}));
    const detail = {
      run: { id: "r1", goal: "Work", status: "running", run_mode: "plan_and_execute" },
      agents: [], tasks: [], messages: []
    };
    const { rerender } = render(<TeamRunDetail detail={detail} onCancel={onCancel} />);

    const stop = screen.getByRole("button", { name: "Stop run" });
    await userEvent.click(stop);
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(stop).toBeDisabled();

    rerender(<TeamRunDetail detail={{ ...detail, run: { ...detail.run, status: "completed" } }} onCancel={onCancel} />);
    expect(screen.queryByRole("button", { name: "Stop run" })).not.toBeInTheDocument();
  });

  it("collects every pending user decision and submits one answer batch", async () => {
    const onAnswerDecision = vi.fn(() => new Promise(() => {}));
    render(<TeamRunDetail
      onAddWork={vi.fn()}
      onResume={vi.fn()}
      onCancel={vi.fn()}
      onAnswerDecision={onAnswerDecision}
      detail={{
        run: { id: "r1", goal: "Ship", status: "waiting_for_user", run_mode: "plan_and_execute" },
        agents: [],
        tasks: [{ id: "t1", title: "Deploy", status: "blocked" }],
        messages: [],
        decisionRequest: {
          id: "d1",
          revision: 3,
          status: "awaiting_user",
          items: [
            {
              id: "Q-001",
              topic: "Target",
              question: "Where should this deploy?",
              why_needed: "Configuration depends on the target.",
              options: [
                { id: "staging", label: "Staging", impact: "Safer validation." },
                { id: "production", label: "Production", impact: "Immediate release." }
              ],
              recommended_option_id: "staging"
            },
            {
              id: "Q-002",
              topic: "Audience",
              question: "Who should be notified?",
              why_needed: "Recipients are not defined.",
              options: []
            }
          ]
        }
      }}
    />);

    expect(screen.getByRole("region", { name: "Input needed" })).toBeInTheDocument();
    expect(screen.getByText(/Independent work is complete/)).toBeInTheDocument();
    expect(screen.getByText("Recommended: Staging")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add work" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop run" })).toBeInTheDocument();
    const submit = screen.getByRole("button", { name: "ANSWER & RESUME" });
    expect(submit).toBeDisabled();

    await userEvent.click(screen.getByRole("radio", { name: /Staging/ }));
    await userEvent.type(screen.getByLabelText("Answer for Q-002"), "Release team");
    await userEvent.click(submit);

    expect(onAnswerDecision).toHaveBeenCalledWith({
      "Q-001": "staging",
      "Q-002": "Release team"
    });
    expect(submit).toBeDisabled();
  });

  it.each([
    ["planning", "Planning is paused for your decision. Answer every open question to start the work."],
    ["synthesis", "Work is complete. Answer every open question to finalize the response."]
  ])("shows the correct %s decision stage guidance", (stage, guidance) => {
    render(<TeamRunDetail
      detail={{
        run: { id: "r1", goal: "Ship", status: "waiting_for_user", run_mode: "plan_and_execute" },
        agents: [],
        tasks: [],
        messages: [],
        decisionRequest: {
          id: "d1",
          revision: 1,
          status: "awaiting_user",
          items: [{
            id: "Q-001",
            stage,
            topic: "Scope",
            question: "Which scope?",
            options: []
          }]
        }
      }}
    />);

    expect(screen.getByText(guidance)).toBeInTheDocument();
  });

  it("shows a recoverable message when a waiting run has no active request", () => {
    render(<TeamRunDetail detail={{
      run: { id: "r1", goal: "Ship", status: "waiting_for_user", run_mode: "plan_and_execute" },
      agents: [], tasks: [], messages: [], decisionRequest: null
    }} />);

    expect(screen.getByText("Decision request is unavailable. Refresh this run.")).toBeInTheDocument();
  });

  it("reviews, commits, and applies worktree delivery through injected callbacks", async () => {
    const onCommitDelivery = vi.fn().mockResolvedValue(true);
    const onApplyDelivery = vi.fn().mockResolvedValue(true);
    const detail = {
      run: { id: "run-12345678", goal: "Ship", status: "completed", run_mode: "plan_and_execute" },
      agents: [], tasks: [], messages: []
    };
    const delivery = {
      available: true,
      source: { path: "C:/runs/project", branch: "team-run/run-12345678" },
      target: { path: "C:/repo", branch: "main", dirty_files: [] },
      uncommitted_files: [{ status: "M", path: "src/app.js" }],
      pending_commits: [],
      blocked_reasons: ["Commit Team Run changes before applying."],
      can_commit: true,
      can_apply: false
    };
    const { rerender } = render(<TeamRunDetail
      detail={detail}
      delivery={delivery}
      onCommitDelivery={onCommitDelivery}
      onApplyDelivery={onApplyDelivery}
      onRefreshDelivery={vi.fn()}
    />);

    expect(screen.getByRole("region", { name: "Repository delivery" })).toBeInTheDocument();
    expect(screen.getByText("src/app.js")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Apply to repository" })).toBeDisabled();
    const message = screen.getByLabelText("COMMIT MESSAGE");
    await userEvent.clear(message);
    await userEvent.type(message, "feat: apply dashboard");
    await userEvent.click(screen.getByRole("button", { name: "Commit changes" }));
    expect(onCommitDelivery).toHaveBeenCalledWith("feat: apply dashboard");

    rerender(<TeamRunDetail
      detail={detail}
      delivery={{
        ...delivery,
        uncommitted_files: [],
        pending_commits: [{ sha: "abcdef1234", short_sha: "abcdef12", subject: "feat: apply dashboard" }],
        blocked_reasons: [],
        can_commit: false,
        can_apply: true
      }}
      onCommitDelivery={onCommitDelivery}
      onApplyDelivery={onApplyDelivery}
      onRefreshDelivery={vi.fn()}
    />);
    await userEvent.click(screen.getByRole("button", { name: "Apply to repository" }));
    expect(onApplyDelivery).toHaveBeenCalledTimes(1);
  });

  it("collapses repository delivery and Cycle policy from their summaries", async () => {
    render(<TeamRunDetail
      detail={{
        run: {
          id: "run-1", goal: "", status: "completed", run_mode: "plan_and_execute",
          lifecycle_mode: "continuous", execution_policy: "triggered"
        },
        policyStatus: "ready",
        agents: [], tasks: [], messages: [], cycles: []
      }}
      delivery={{
        available: true,
        source: { path: "C:/run", branch: "team-run/run-1" },
        target: { path: "C:/repo", branch: "main", dirty_files: [] },
        uncommitted_files: [], pending_commits: [], blocked_reasons: []
      }}
    />);

    const deliveryPanel = screen.getByRole("region", { name: "Repository delivery" });
    const policyPanel = screen.getByRole("region", { name: "Cycle policy" });
    expect(deliveryPanel.tagName).toBe("DETAILS");
    expect(policyPanel.tagName).toBe("DETAILS");
    expect(deliveryPanel).toHaveAttribute("open");
    expect(policyPanel).toHaveAttribute("open");

    await userEvent.click(screen.getByText("Repository Delivery"));
    await userEvent.click(screen.getByText("TRIGGERED · READY"));
    expect(deliveryPanel).not.toHaveAttribute("open");
    expect(policyPanel).not.toHaveAttribute("open");
  });

  it("resolves repository conflicts through target, team, manual, continue, and cancel callbacks", async () => {
    const onResolveDeliveryConflict = vi.fn().mockResolvedValue(true);
    const onContinueDelivery = vi.fn().mockResolvedValue(true);
    const onCancelDeliveryConflicts = vi.fn().mockResolvedValue(true);
    const detail = {
      run: { id: "run-1", goal: "Ship", status: "completed", run_mode: "plan_and_execute" },
      agents: [], tasks: [], messages: []
    };
    const conflict = {
      id: "conflict-1",
      path: "docs/registry.json",
      resolved: false,
      resolution: null,
      target_content: "target content\n",
      team_content: "team content\n",
      working_content: "<<<<<<< target\n=======\n>>>>>>> team\n",
      target_deleted: false,
      team_deleted: false,
      manual_allowed: true
    };
    const delivery = {
      available: true,
      source: { path: "C:/run", branch: "team-run/run-1" },
      target: { path: "C:/repo", branch: "main", dirty_files: [] },
      uncommitted_files: [], pending_commits: [{ sha: "a1", short_sha: "a1", subject: "change" }],
      blocked_reasons: ["Resolve repository conflicts before applying."],
      conflict_session: {
        id: "session-1", files: [conflict], resolved_count: 0, total_count: 1,
        can_continue: false, target_changed: false
      }
    };
    const { rerender } = render(<TeamRunDetail
      detail={detail}
      delivery={delivery}
      onResolveDeliveryConflict={onResolveDeliveryConflict}
      onContinueDelivery={onContinueDelivery}
      onCancelDeliveryConflicts={onCancelDeliveryConflicts}
    />);

    expect(screen.getByRole("region", { name: "Repository conflicts" })).toBeInTheDocument();
    expect(screen.getByText("target content", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("team content", { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resolve & apply" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Keep target" }));
    expect(onResolveDeliveryConflict).toHaveBeenCalledWith("conflict-1", { mode: "target" });
    await userEvent.click(screen.getByRole("button", { name: "Use Team Run" }));
    expect(onResolveDeliveryConflict).toHaveBeenCalledWith("conflict-1", { mode: "team" });
    const manual = screen.getByLabelText("Merged result for docs/registry.json");
    await userEvent.clear(manual);
    await userEvent.type(manual, "merged content");
    await userEvent.click(screen.getByRole("button", { name: "Save manual merge" }));
    expect(onResolveDeliveryConflict).toHaveBeenLastCalledWith(
      "conflict-1",
      { mode: "manual", content: "merged content" }
    );

    rerender(<TeamRunDetail
      detail={detail}
      delivery={{
        ...delivery,
        conflict_session: {
          ...delivery.conflict_session,
          files: [{ ...conflict, resolved: true, resolution: "manual" }],
          resolved_count: 1,
          can_continue: true
        }
      }}
      onResolveDeliveryConflict={onResolveDeliveryConflict}
      onContinueDelivery={onContinueDelivery}
      onCancelDeliveryConflicts={onCancelDeliveryConflicts}
    />);
    await userEvent.click(screen.getByRole("button", { name: "Resolve & apply" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel resolution" }));
    expect(onContinueDelivery).toHaveBeenCalledTimes(1);
    expect(onCancelDeliveryConflicts).toHaveBeenCalledTimes(1);
  });
});
