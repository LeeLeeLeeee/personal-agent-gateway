import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { vi } from "vitest";
import { api } from "../../../api/client.js";
import { TeamRunDetail } from "./index.jsx";

const APPROVED_PLAN_REVISION = {
  revision: 1,
  status: "approved",
  required_approver_agent_ids: [],
  reviews: {},
  objections: {}
};

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

    await userEvent.click(screen.getByRole("tab", { name: "LOG" }));
    expect(screen.getByText("Planning started")).toBeInTheDocument();
  });

  it("keeps waiting, skipped, and canceled tasks visible with dependencies", async () => {
    render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "Collect", status: "running", run_mode: "plan_and_execute" },
          agents: [],
          tasks: [
            { id: "source", title: "Collect sources", status: "failed" },
            {
              id: "rewrite",
              title: "Rewrite summary",
              status: "skipped",
              error_message: "skipped_by_dependency",
              depends_on_task_ids: ["source"]
            },
            { id: "provider", title: "Fetch transcript", status: "waiting_for_provider" },
            { id: "review", title: "Review selection", status: "waiting_for_user" },
            { id: "obsolete", title: "Obsolete work", status: "canceled" }
          ],
          messages: []
        }}
      />
    );

    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));

    expect(screen.getByRole("button", { name: "Open task Fetch transcript" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open task Review selection" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open task Rewrite summary" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open task Obsolete work" })).toBeInTheDocument();
    expect(screen.getByText("선행 작업 · Collect sources")).toBeInTheDocument();
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

  it("promotes the current instruction while keeping technical identity in run details", () => {
    const runId = "1bceb1ef9d54459fb1174f5bec686dbc";
    const instruction = "Collect the current popular Reddit posts and summarize the top 10.";
    const workspace = "/Users/example/works/personal-agent-gateway/team-runs/reddit-popular";

    render(
      <TeamRunDetail
        detail={{
          run: {
            id: runId,
            goal: "Track Reddit trends",
            status: "running",
            run_mode: "plan_and_execute",
            lifecycle_mode: "continuous",
            current_objective: instruction,
            workspace_root: workspace
          },
          agents: [],
          tasks: [],
          messages: [],
          cycles: [{
            id: "cycle-1",
            sequence: 1,
            status: "running",
            effective_instruction: `${instruction}\n\nPREVIOUS CYCLE CONTEXT\nInternal context`
          }]
        }}
      />
    );

    expect(screen.getByText(runId)).toHaveClass("team-run-meta-copy");
    expect(screen.getByText(instruction)).toHaveClass("team-run-current-request");
    expect(screen.getByText(instruction)).toHaveAttribute("title", instruction);
    expect(screen.getByText(/BASE OBJECTIVE · Track Reddit trends/)).toHaveClass("team-run-base-objective");
    expect(screen.getByText(workspace)).toHaveClass("team-run-meta-path");
  });

  it("uses the base objective as the visible request before the first Cycle exists", () => {
    render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "Prepare release notes", status: "draft", run_mode: "plan_and_execute" },
          agents: [],
          tasks: [],
          messages: [],
          cycles: []
        }}
      />
    );

    expect(screen.getByText("Prepare release notes")).toHaveClass("team-run-current-request");
    expect(screen.queryByText(/BASE OBJECTIVE/)).not.toBeInTheDocument();
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
            {
              id: "m3",
              kind: "agent_output",
              sender_agent_id: "a2",
              content: "API built",
              metadata: { task_id: "t1", files_created: ["src/api.py"] },
              created_at: "2026-07-13T00:02:00Z"
            }
          ]
        }}
      />
    );

    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));
    expect(screen.getByText("FILES 1")).toBeInTheDocument();
    expect(screen.getByText("REPORTS 1")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "SUMMARY" }));
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

  it("surfaces a failed run cause and recovery actions above the detail tabs", async () => {
    const onRetryTask = vi.fn();
    const onOpenSettings = vi.fn();
    render(
      <TeamRunDetail
        onRetryTask={onRetryTask}
        onOpenSettings={onOpenSettings}
        detail={{
          run: { id: "r1", goal: "Design", status: "failed", run_mode: "plan_and_execute" },
          agents: [],
          tasks: [{ id: "t1", title: "Run QA", status: "failed", error_message: "capabilities_unavailable" }],
          messages: []
        }}
      />
    );

    expect(screen.getByRole("alert")).toHaveTextContent("Run QA: capabilities_unavailable");
    expect(screen.queryByRole("button", { name: "Retry Run QA" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Change runtime" }));
    expect(onOpenSettings).toHaveBeenCalledTimes(1);
    expect(onRetryTask).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Open diagnostics" }));
    expect(screen.getByRole("tab", { name: /TASKS/ })).toHaveAttribute("aria-selected", "true");
  });

  it("resumes an interrupted cycle instead of retrying the same failed task twice", async () => {
    const onResume = vi.fn(() => new Promise(() => {}));
    const onRetryTask = vi.fn();
    render(
      <TeamRunDetail
        onResume={onResume}
        onRetryTask={onRetryTask}
        documents={[{ path: "report.md" }]}
        detail={{
          run: { id: "r1", goal: "Design", status: "interrupted", run_mode: "plan_and_execute" },
          agents: [{ id: "a1", name: "QA Agent", status: "idle" }],
          cycles: [{ id: "c1", sequence: 2, status: "interrupted" }],
          tasks: [
            { id: "t1", cycle_id: "c1", title: "Run QA", status: "failed", owner_agent_id: "a1", error_message: "timed out" },
            { id: "t2", cycle_id: "c1", title: "Run QA (retry)", status: "pending", retry_of_task_id: "t1" },
            { id: "t3", cycle_id: "c1", title: "Build", status: "completed" }
          ],
          messages: []
        }}
      />
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("CYCLE #2");
    expect(alert).toHaveTextContent("AGENT · QA Agent");
    expect(alert).toHaveTextContent("완료된 Task 1개와 Files 1개는 유지됩니다.");
    expect(screen.queryByRole("button", { name: "Retry Run QA" })).not.toBeInTheDocument();

    const resume = screen.getByRole("button", { name: "Resume cycle" });
    await userEvent.click(screen.getByRole("button", { name: "Review retry task" }));
    expect(screen.getByRole("tab", { name: /TASKS/ })).toHaveAttribute("aria-selected", "true");
    await userEvent.click(resume);
    expect(onResume).toHaveBeenCalledTimes(1);
    expect(onRetryTask).not.toHaveBeenCalled();
    expect(resume).toBeDisabled();
  });

  it("retries the latest failed retry task instead of diagnosing its original failure", async () => {
    const onRetryTask = vi.fn();
    const onOpenSettings = vi.fn();
    render(
      <TeamRunDetail
        onRetryTask={onRetryTask}
        onOpenSettings={onOpenSettings}
        detail={{
          run: { id: "r1", goal: "Design", status: "failed", run_mode: "plan_and_execute" },
          agents: [],
          tasks: [
            {
              id: "t1",
              title: "Run QA",
              status: "failed",
              error_message: "capabilities_unavailable",
              created_at: "2026-08-23T01:00:00Z"
            },
            {
              id: "t2",
              title: "Run QA",
              status: "failed",
              retry_of_task_id: "t1",
              error_message: "invalid_structured_output",
              created_at: "2026-08-23T02:00:00Z"
            }
          ],
          messages: []
        }}
      />
    );

    expect(screen.getByRole("alert")).toHaveTextContent("Run QA: invalid_structured_output");
    expect(screen.queryByText(/재시도 Task가 준비되었습니다/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Change runtime" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry Run QA" }));
    expect(onRetryTask).toHaveBeenCalledWith("t2");
    expect(onOpenSettings).not.toHaveBeenCalled();
  });

  it("links Team Run files to their grouped Outputs", async () => {
    const onViewOutputs = vi.fn();
    render(
      <TeamRunDetail
        onViewOutputs={onViewOutputs}
        detail={{
          run: { id: "run-1", goal: "Design", status: "completed", run_mode: "plan_and_execute" },
          agents: [], tasks: [], messages: []
        }}
      />
    );

    await userEvent.click(screen.getByRole("tab", { name: "OUTPUTS" }));
    await userEvent.click(screen.getByRole("button", { name: "Outputs에서 모두 보기" }));
    expect(onViewOutputs).toHaveBeenCalledWith("run-1");
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
    await userEvent.click(screen.getByRole("tab", { name: /OUTPUTS/ }));
    await userEvent.click(screen.getByText("notes.md"));
    expect(screen.getByText("docs")).toBeInTheDocument();
    expect(onLoadDocument).toHaveBeenCalledWith("docs/notes.md");
    expect(await screen.findByRole("heading", { name: "hi" })).toBeInTheDocument();
  });

  it("shows completed-run reports expanded by default and keeps raw activity separate", async () => {
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
    expect(screen.getByText("Feature built")).toBeVisible();

    await userEvent.click(screen.getByRole("tab", { name: "LOG" }));
    expect(screen.getByText("Planning started")).toBeInTheDocument();
  });

  it("renders summaries and task details as structured markdown", async () => {
    const { container } = render(
      <TeamRunDetail
        detail={{
          run: {
            id: "r1",
            goal: "Design",
            status: "completed",
            run_mode: "plan_and_execute",
            summary: "## 완료 내용\n\n- API 구현\n- QA 통과"
          },
          agents: [{ id: "a1", name: "Worker", role: "member", status: "completed" }],
          tasks: [{
            id: "t1",
            title: "Build API",
            description: "## 수행 내용\n\n`/api/items`를 구현합니다.",
            result: "## 결과\n\n- 성공",
            status: "completed"
          }],
          messages: [{
            id: "m1",
            kind: "agent_output",
            sender_agent_id: "a1",
            content: "### 보고서\n\n| 항목 | 상태 |\n| --- | --- |\n| API | 완료 |",
            metadata: { task_id: "t1" },
            created_at: "2026-07-08T00:01:00Z"
          }],
          cycles: [{
            id: "c1",
            sequence: 1,
            source_type: "manual",
            status: "completed",
            rounds_used: 1,
            rounds_budget: 8,
            summary: "## Cycle 결과\n\n1. 구현\n2. 검증"
          }]
        }}
      />
    );

    const latestSummary = container.querySelector(".team-final-summary-body");
    expect(within(latestSummary).getByRole("heading", { name: "완료 내용" })).toBeInTheDocument();
    expect(within(latestSummary).getByRole("list")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: /CYCLES/ }));
    const cycleSummary = container.querySelector(".team-cycle-summary");
    expect(within(cycleSummary).getByRole("heading", { name: "Cycle 결과" })).toBeInTheDocument();
    expect(within(cycleSummary).getByRole("list")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));
    await userEvent.click(screen.getByRole("button", { name: "Open task Build API" }));
    const taskDialog = screen.getByRole("dialog", { name: "Task details: Build API" });
    expect(within(taskDialog).getByRole("heading", { name: "수행 내용" })).toBeInTheDocument();
    expect(within(taskDialog).getByRole("heading", { name: "결과" })).toBeInTheDocument();
    expect(within(taskDialog).getByRole("heading", { name: "보고서" })).toBeInTheDocument();
    expect(within(taskDialog).getByRole("table")).toBeInTheDocument();
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

    await userEvent.click(screen.getByRole("tab", { name: /CYCLES/ }));
    expect(screen.getByText("continuous")).toBeInTheDocument();
    expect(screen.getByText("hook · hook-run-2")).toBeInTheDocument();
    expect(screen.getByText("First mail done")).toBeInTheDocument();
    expect([...container.querySelectorAll(".team-cycle-sequence")].map((node) => node.textContent))
      .toEqual(["CYCLE #2", "CYCLE #1"]);
  });

  it("distinguishes a leader that reported no gaps from one that did not report", async () => {
    const { rerender } = render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "G", status: "completed", run_mode: "plan_and_execute" },
          agents: [], messages: [], tasks: [],
          cycles: [{ id: "c1", sequence: 1, status: "completed", coverage_gaps: [] }]
        }}
      />
    );
    await userEvent.click(screen.getByRole("tab", { name: /CYCLES/ }));
    expect(screen.getByText(/누락 없다고 보고함/)).toBeInTheDocument();

    rerender(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "G", status: "completed", run_mode: "plan_and_execute" },
          agents: [], messages: [], tasks: [],
          cycles: [{ id: "c1", sequence: 1, status: "completed" }]
        }}
      />
    );
    expect(screen.getByText(/커버리지를 보고하지 않음/)).toBeInTheDocument();
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

    await userEvent.click(screen.getByRole("tab", { name: "LOG" }));
    expect([...container.querySelectorAll(".tl-detail")].map((node) => node.textContent)).toEqual([
      "new report", "new answer", "new question", "old report", "old answer", "old question"
    ]);

    await userEvent.click(screen.getByRole("tab", { name: "SUMMARY" }));
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

  it("shows blocked acceptance requirements, evidence, and reason in task details", async () => {
    render(<TeamRunDetail detail={{
      run: {
        id: "r1",
        goal: "Publish guide",
        status: "blocked",
        run_mode: "plan_and_execute"
      },
      agents: [],
      messages: [],
      tasks: [{
        id: "t1",
        title: "Verify guide",
        status: "blocked",
        required: true,
        error_message: "The link checker could not run.",
        acceptance: {
          required_outputs: ["outputs/guide.md"],
          required_verifications: [
            { name: "link-check", check: null },
            {
              name: "schema-check",
              check: { type: "file_nonempty", path: "outputs/schema.json" }
            },
            "source-check"
          ]
        },
        outcome: {
          status: "completed",
          summary: "Guide written.",
          reason_code: null,
          deliverables: [{ path: "outputs/guide.md", kind: "text" }],
          verifications: [
            {
              name: "link-check",
              status: "failed",
              evidence: "Executable unavailable"
            },
            {
              name: "schema-check",
              status: "passed",
              evidence: "file_nonempty: outputs/schema.json has content"
            },
            {
              name: "source-check",
              status: "passed",
              evidence: "attested"
            }
          ]
        },
        acceptance_result: {
          accepted: false,
          status: "failed",
          reason_code: "required_verification_failed",
          evidence: {}
        }
      }]
    }} />);

    expect(screen.getByText("차단됨")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));
    await userEvent.click(screen.getByRole("button", { name: "Open task Verify guide" }));

    const dialog = screen.getByRole("dialog", { name: "Task details: Verify guide" });
    expect(within(dialog).getByText("REQUIRED TASK")).toBeInTheDocument();
    expect(within(dialog).getByText("outputs/guide.md")).toBeInTheDocument();
    expect(within(dialog).getByText("link-check")).toBeInTheDocument();
    expect(within(dialog).getByText("FAILED")).toBeInTheDocument();
    expect(within(dialog).getByText(/Executable unavailable/)).toBeInTheDocument();
    expect(within(dialog).getByText("required_verification_failed")).toBeInTheDocument();
    expect(within(dialog).getByText("The link checker could not run.")).toBeInTheDocument();
    expect(within(dialog).getByText("schema-check")).toBeInTheDocument();
    expect(within(dialog).getByText("source-check")).toBeInTheDocument();
    expect(within(dialog).getAllByText("PASSED").length).toBe(2);
  });

  it("shows what a server-run check verified", async () => {
    render(<TeamRunDetail detail={{
      run: { id: "r1", goal: "Draft library", status: "completed", run_mode: "plan_and_execute" },
      agents: [],
      messages: [],
      tasks: [{
        id: "t1",
        title: "Draft library",
        status: "completed",
        acceptance: {
          required_outputs: ["notes.md"],
          required_verifications: [
            { name: "marker", check: { type: "file_contains", path: "draft.md", value: "<library_draft>" } }
          ]
        },
        outcome: {
          status: "completed",
          verifications: [{ name: "marker", status: "passed", evidence: "matched" }]
        },
        acceptance_result: {
          accepted: true,
          status: "accepted",
          evidence: {
            verifications: { marker: { mode: "verified" } },
            attested_only: false
          }
        }
      }]
    }} />);

    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));
    await userEvent.click(screen.getByRole("button", { name: "Open task Draft library" }));
    const dialog = screen.getByRole("dialog", { name: "Task details: Draft library" });

    expect(within(dialog).getByText(/file_contains/)).toBeInTheDocument();
    expect(within(dialog).getByText(/draft\.md/)).toBeInTheDocument();
    expect(within(dialog).getByText("VERIFIED")).toBeInTheDocument();
  });

  it("shows the server's verified status and evidence when the worker omits its self-report", async () => {
    render(<TeamRunDetail detail={{
      run: { id: "r1", goal: "Draft library", status: "completed", run_mode: "plan_and_execute" },
      agents: [],
      messages: [],
      tasks: [{
        id: "t1",
        title: "Draft library",
        status: "completed",
        acceptance: {
          required_outputs: ["draft.md"],
          required_verifications: [
            { name: "marker", check: { type: "file_contains", path: "draft.md", value: "<library_draft>" } }
          ]
        },
        outcome: {
          status: "completed",
          verifications: []
        },
        acceptance_result: {
          accepted: true,
          status: "accepted",
          evidence: {
            verifications: {
              marker: {
                mode: "verified",
                status: "passed",
                evidence: "file_contains: draft.md contains the value"
              }
            },
            attested_only: false
          }
        }
      }]
    }} />);

    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));
    await userEvent.click(screen.getByRole("button", { name: "Open task Draft library" }));
    const dialog = screen.getByRole("dialog", { name: "Task details: Draft library" });

    expect(within(dialog).getByText("PASSED")).toBeInTheDocument();
    expect(within(dialog).queryByText("MISSING")).not.toBeInTheDocument();
    expect(
      within(dialog).getByText(/file_contains: draft\.md contains the value/)
    ).toBeInTheDocument();
  });

  it("marks a task nothing machine-checked as attested", async () => {
    render(<TeamRunDetail detail={{
      run: { id: "r1", goal: "Draft library", status: "completed", run_mode: "plan_and_execute" },
      agents: [],
      messages: [],
      tasks: [{
        id: "t1",
        title: "Draft library",
        status: "completed",
        acceptance: {
          required_outputs: ["draft.md"],
          required_verifications: [{ name: "marker", check: null }]
        },
        outcome: {
          status: "completed",
          verifications: [{ name: "marker", status: "passed", evidence: "attested" }]
        },
        acceptance_result: {
          accepted: true,
          status: "accepted",
          evidence: {
            verifications: { marker: { mode: "attested" } },
            attested_only: true
          }
        }
      }]
    }} />);

    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));
    await userEvent.click(screen.getByRole("button", { name: "Open task Draft library" }));
    const dialog = screen.getByRole("dialog", { name: "Task details: Draft library" });

    expect(within(dialog).getByText("NO GATE CHECK")).toBeInTheDocument();
  });

  it("does not mark an attested badge on a task with a server-run check", async () => {
    render(<TeamRunDetail detail={{
      run: { id: "r1", goal: "Draft library", status: "completed", run_mode: "plan_and_execute" },
      agents: [],
      messages: [],
      tasks: [{
        id: "t1",
        title: "Draft library",
        status: "completed",
        acceptance: {
          required_outputs: ["draft.md"],
          required_verifications: [
            { name: "marker", check: { type: "file_contains", path: "draft.md", value: "<library_draft>" } }
          ]
        },
        outcome: {
          status: "completed",
          verifications: [
            { name: "marker", status: "passed", evidence: "file_contains: draft.md has <library_draft>" }
          ]
        },
        acceptance_result: {
          accepted: true,
          status: "accepted",
          evidence: {
            verifications: { marker: { mode: "verified" } },
            attested_only: false
          }
        }
      }]
    }} />);

    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));
    await userEvent.click(screen.getByRole("button", { name: "Open task Draft library" }));
    const dialog = screen.getByRole("dialog", { name: "Task details: Draft library" });

    expect(within(dialog).queryByText("NO GATE CHECK")).not.toBeInTheDocument();
  });

  it("shows acceptance reviews only in the selected task details", async () => {
    render(<TeamRunDetail detail={{
      run: { id: "r1", goal: "Publish guide", status: "running", run_mode: "plan_and_execute" },
      agents: [],
      tasks: [{ id: "t1", title: "Verify guide", status: "in_progress" }],
      messages: [
        {
          id: "review-1",
          kind: "acceptance_review",
          sender_agent_id: "lead",
          recipient_agent_id: "worker",
          content: "Resubmit without the undeclared file.",
          metadata: {
            task_id: "t1",
            attempt: 1,
            reason_code: "undeclared_deliverable",
            action: "retry_worker",
            reason: "The contract declares no output.",
            instruction: "Resubmit without the undeclared file.",
            acceptance_before: {
              required_outputs: [],
              required_verifications: [{ name: "source-check", check: null }]
            },
            acceptance_after: null
          },
          created_at: "2026-07-30T00:00:00Z"
        },
        {
          id: "review-2",
          kind: "acceptance_review",
          content: "This must remain private to another task.",
          metadata: { task_id: "t2", reason_code: "other_task" },
          created_at: "2026-07-30T00:01:00Z"
        }
      ]
    }} />);

    expect(screen.queryByText("undeclared_deliverable")).not.toBeInTheDocument();
    expect(screen.queryByText("INTERNAL REVIEW")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));
    await userEvent.click(screen.getByRole("button", { name: "Open task Verify guide" }));

    const dialog = screen.getByRole("dialog", { name: "Task details: Verify guide" });
    expect(within(dialog).getByText("INTERNAL REVIEW · 1")).toBeInTheDocument();
    expect(within(dialog).getByText("undeclared_deliverable")).toBeInTheDocument();
    expect(within(dialog).getByText("RETRY WORKER")).toBeInTheDocument();
    expect(within(dialog).getByText("Resubmit without the undeclared file.")).toBeInTheDocument();
    expect(within(dialog).queryByText("This must remain private to another task.")).not.toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole("button", { name: "Close task details" }));
    await userEvent.click(screen.getByRole("tab", { name: "LOG" }));
    expect(screen.queryByText("Resubmit without the undeclared file.")).not.toBeInTheDocument();
    expect(screen.queryByText("undeclared_deliverable")).not.toBeInTheDocument();
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

  it("hides the answer form when the linked Cycle is not waiting", () => {
    render(<TeamRunDetail detail={{
      run: { id: "r1", goal: "Ship", status: "waiting_for_user", run_mode: "plan_and_execute" },
      agents: [],
      tasks: [],
      messages: [],
      cycles: [{ id: "c1", sequence: 1, status: "running" }],
      decisionRequest: {
        id: "d1",
        cycle_id: "c1",
        revision: 1,
        status: "awaiting_user",
        items: []
      }
    }} />);

    expect(screen.queryByRole("region", { name: "Input needed" })).not.toBeInTheDocument();
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

  it("shows the running agent's task title with elapsed time that ticks", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-06T04:10:24.000Z"));
    try {
      const { container } = render(
        <TeamRunDetail
          detail={{
            run: { id: "r1", goal: "D3 규약", status: "running", run_mode: "plan_and_execute" },
            agents: [
              {
                id: "a1",
                name: "Tech Lead",
                role: "member",
                status: "running",
                current_task_id: "t1"
              }
            ],
            tasks: [
              {
                id: "t1",
                title: "잔여 P3 7건 수정",
                description: "fix",
                status: "in_progress",
                started_at: "2026-08-06T04:07:12.000Z"
              }
            ],
            messages: []
          }}
        />
      );

      const lane = container.querySelector(".team-lane-task");
      expect(lane).not.toBeNull();
      // The title and the elapsed time are separate spans; assert on the
      // combined text so a missing separator cannot slip through.
      expect(lane.textContent).toBe("잔여 P3 7건 수정 03:12 경과");
      expect(screen.getByText(/03:12 경과/)).toBeInTheDocument();

      await act(async () => {
        vi.advanceTimersByTime(1000);
      });

      expect(container.querySelector(".team-lane-task").textContent).toBe(
        "잔여 P3 7건 수정 03:13 경과"
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not show stale running child states after the run failed", async () => {
    const { container } = render(
      <TeamRunDetail
        detail={{
          run: {
            id: "r1",
            goal: "Recover operation",
            status: "failed",
            run_mode: "plan_and_execute",
            error_message: "Operation key is already bound to another request"
          },
          agents: [
            {
              id: "a1",
              name: "Worker",
              role: "member",
              status: "running",
              current_task_id: "t1"
            }
          ],
          tasks: [
            {
              id: "t1",
              title: "Finalize review",
              description: "review",
              status: "in_progress",
              owner_agent_id: "a1",
              started_at: "2026-08-06T04:07:12.000Z"
            }
          ],
          messages: []
        }}
      />
    );

    expect(screen.queryByText("LIVE")).not.toBeInTheDocument();
    expect(container.querySelector(".team-lane-status-row")).toHaveTextContent("FAILED");

    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));
    expect(container.querySelector(".team-task-status")).toHaveTextContent("FAILED");
  });

  it("groups every task state into four board columns", async () => {
    const { container } = render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "Board", status: "running", run_mode: "plan_and_execute" },
          agents: [],
          messages: [],
          tasks: [
            { id: "t1", title: "Queued work", status: "pending" },
            { id: "t2", title: "Active work", status: "in_progress" },
            { id: "t3", title: "Awaiting answer", status: "waiting_for_user" },
            { id: "t4", title: "Awaiting provider", status: "waiting_for_provider" },
            { id: "t5", title: "Finished work", status: "completed" },
            { id: "t6", title: "Bypassed work", status: "skipped" },
            { id: "t7", title: "Stuck work", status: "blocked" },
            { id: "t8", title: "Broken work", status: "failed" },
            { id: "t9", title: "Dropped work", status: "canceled" }
          ]
        }}
      />
    );

    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));

    const columns = [...container.querySelectorAll(".team-task-column")];
    expect(columns).toHaveLength(4);

    const heads = columns.map((column) => column.querySelector(".team-task-column-head"));
    expect(heads.map((head) => head.firstElementChild.textContent)).toEqual([
      "PENDING",
      "IN PROGRESS",
      "COMPLETED",
      "UNRESOLVED"
    ]);
    expect(heads.map((head) => head.lastElementChild.textContent)).toEqual(["1", "3", "2", "3"]);

    const titlesByColumn = columns.map((column) =>
      [...column.querySelectorAll(".team-task-title")].map((node) => node.textContent)
    );
    expect(titlesByColumn[0]).toEqual(["Queued work"]);
    expect(titlesByColumn[1]).toEqual(["Active work", "Awaiting answer", "Awaiting provider"]);
    expect(titlesByColumn[2]).toEqual(["Finished work", "Bypassed work"]);
    expect(titlesByColumn[3]).toEqual(["Stuck work", "Broken work", "Dropped work"]);
  });

  it("keeps a task with an unmapped state visible instead of dropping it", async () => {
    const { container } = render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "Board", status: "running", run_mode: "plan_and_execute" },
          agents: [],
          messages: [],
          tasks: [{ id: "t1", title: "Unknown state work", status: "invented_state" }]
        }}
      />
    );

    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));

    const unresolved = [...container.querySelectorAll(".team-task-column")][3];
    expect(unresolved.querySelector(".team-task-title").textContent).toBe("Unknown state work");
    expect(screen.getByRole("button", { name: "Open task Unknown state work" })).toBeInTheDocument();
  });
  it("shows how a leader response failed to parse", async () => {
    render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "G", status: "waiting_for_user", run_mode: "plan_and_execute" },
          agents: [],
          messages: [],
          tasks: [{
            id: "t1",
            title: "Verify guide",
            status: "in_progress",
            failure_shape: {
              length: 812,
              fenced: true,
              parsed_json: false,
              missing_expected_keys: ["resolution"],
              unexpected_key_count: 0
            }
          }]
        }}
      />
    );

    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));
    await userEvent.click(screen.getByRole("button", { name: "Open task Verify guide" }));
    const dialog = screen.getByRole("dialog", { name: "Task details: Verify guide" });

    expect(within(dialog).getByText(/812/)).toBeInTheDocument();
    expect(within(dialog).getByText(/resolution/)).toBeInTheDocument();
  });

  it("shows what a task promised against what it built", async () => {
    render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "G", status: "completed", run_mode: "plan_and_execute" },
          agents: [],
          messages: [],
          buildEvidenceSummary: {
            task_count: 3,
            worker_asserted_only_count: 2,
            missing_file_count: 1,
          },
          tasks: [{
            id: "t1",
            title: "Write the guide",
            status: "completed",
            // Render-only fixture, not a model of a real state: live, a task can't
            // land here with status "completed" and a non-empty extra_declarations
            // -- team_acceptance.py rejects a task whose declared deliverables
            // exceed required_outputs before acceptance ever succeeds.
            build_evidence: {
              promised: ["kept.md", "forgotten.md"],
              declared: ["kept.md", "ghost.md", "lost.md"],
              undeclared_promises: ["forgotten.md"],
              extra_declarations: ["ghost.md"],
              missing_files: ["lost.md"],
              verifications: [
                { name: "ran", mode: "verified", status: "passed" },
                { name: "claimed", mode: "attested", status: "passed" }
              ],
              worker_asserted_only: false
            }
          }]
        }}
      />
    );

    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));
    // "통과" would read as "acceptance was rigorous". Every check kind is a file
    // read, so the label has to say that and name its own scope.
    expect(screen.getByText(/태스크 3개 중 게이트가 파일 확인 1/)).toBeInTheDocument();
    expect(screen.getByText(/게이트 미검사 2/)).toBeInTheDocument();
    expect(screen.getByText(/검사는 모두 파일 읽기/)).toBeInTheDocument();
    expect(screen.queryByText(/워커 신고만으로 통과/)).not.toBeInTheDocument();
    expect(screen.getByText(/없는 파일 1/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Open task Write the guide" }));
    const dialog = screen.getByRole("dialog", { name: "Task details: Write the guide" });

    expect(within(dialog).getByText(/forgotten\.md/)).toBeInTheDocument();
    expect(within(dialog).getByText(/ghost\.md/)).toBeInTheDocument();
    expect(within(dialog).getByText(/lost\.md/)).toBeInTheDocument();
    expect(within(dialog).getByText(/파일 내용 확인/)).toBeInTheDocument();
    expect(within(dialog).getByText(/워커 신고/)).toBeInTheDocument();
    expect(within(dialog).queryByText("검증됨")).not.toBeInTheDocument();
  });

  it("renders nothing for a task with no build evidence", async () => {
    render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "G", status: "running", run_mode: "plan_and_execute" },
          agents: [], messages: [],
          tasks: [{ id: "t1", title: "Fresh task", status: "pending" }]
        }}
      />
    );

    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));
    await userEvent.click(screen.getByRole("button", { name: "Open task Fresh task" }));
    const dialog = screen.getByRole("dialog", { name: "Task details: Fresh task" });

    expect(within(dialog).queryByText(/약속한 파일/)).not.toBeInTheDocument();
  });

  it("lets the operator contest the plan and shows how it was ruled on", async () => {
    const onContest = vi.fn().mockResolvedValue({ ok: true });
    render(
      <TeamRunDetail
        onContestPlan={onContest}
        detail={{
          run: { id: "r1", goal: "G", status: "running", run_mode: "plan_and_execute" },
          agents: [], messages: [], tasks: [],
          planRevisions: [APPROVED_PLAN_REVISION],
          contests: [{
            objection: "T-04 has no owner",
            kind: "reject",
            reason: "task 7 covers it",
            supersedes: [],
            created_at: "2026-08-12T00:00:00Z"
          }]
        }}
      />
    );

    expect(screen.getByText(/task 7 covers it/)).toBeInTheDocument();

    await userEvent.click(screen.getByText("계획 검토"));
    await userEvent.type(
      screen.getByRole("textbox", { name: /계획에 이의/ }),
      "T-15 also has no owner"
    );
    await userEvent.click(screen.getByRole("button", { name: /이의 보내기/ }));

    expect(onContest).toHaveBeenCalledWith("r1", "T-15 also has no owner");
  });

  // "running" rather than "canceled": a canceled run no longer shows the form,
  // so the rejection this covers has to be one the client cannot rule out on
  // its own -- intake being closed, for instance.
  it("shows the server's rejection detail when a contest submission is rejected", async () => {
    const onContest = vi.fn().mockResolvedValue({ ok: false, status: 409, detail: "intake is closed" });
    render(
      <TeamRunDetail
        onContestPlan={onContest}
        detail={{
          run: { id: "r1", goal: "G", status: "running", run_mode: "plan_and_execute" },
          agents: [], messages: [], tasks: [],
          planRevisions: [APPROVED_PLAN_REVISION], contests: []
        }}
      />
    );

    await userEvent.click(screen.getByText("계획 검토"));
    await userEvent.type(screen.getByRole("textbox", { name: /계획에 이의/ }), "T-15 has no owner");
    await userEvent.click(screen.getByRole("button", { name: /이의 보내기/ }));

    expect(onContest).toHaveBeenCalledWith("r1", "T-15 has no owner");
    expect(screen.getByText(/intake is closed/)).toBeInTheDocument();
  });

  it("disables submit and does not call onContestPlan for a whitespace-only objection", async () => {
    const onContest = vi.fn();
    render(
      <TeamRunDetail
        onContestPlan={onContest}
        detail={{
          run: { id: "r1", goal: "G", status: "running", run_mode: "plan_and_execute" },
          agents: [], messages: [], tasks: [],
          planRevisions: [APPROVED_PLAN_REVISION], contests: []
        }}
      />
    );

    await userEvent.click(screen.getByText("계획 검토"));
    await userEvent.type(screen.getByRole("textbox", { name: /계획에 이의/ }), "   ");
    expect(screen.getByRole("button", { name: /이의 보내기/ })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: /이의 보내기/ }));
    expect(onContest).not.toHaveBeenCalled();
  });

  it("labels reject and ask_back verdicts and renders each supersedes entry by field", async () => {
    render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "G", status: "running", run_mode: "plan_and_execute" },
          agents: [], messages: [], tasks: [],
          contests: [
            {
              objection: "T-04 has no owner",
              kind: "reject",
              reason: "task 7 covers it",
              supersedes: [],
              created_at: "2026-08-12T00:00:00Z"
            },
            {
              objection: "T-09 conflicts with T-10",
              kind: "ask_back",
              reason: "need clarification from operator",
              supersedes: [{ document_path: "docs/plan.md", decision: "revise" }],
              created_at: "2026-08-12T01:00:00Z"
            }
          ]
        }}
      />
    );

    expect(screen.getByText(/기각 · task 7 covers it/)).toBeInTheDocument();
    expect(screen.getByText(/재질문 · need clarification from operator/)).toBeInTheDocument();
    expect(screen.getByText(/docs\/plan\.md/)).toBeInTheDocument();
    expect(screen.getByText(/revise/)).toBeInTheDocument();
  });
  it("tells a dead contest apart from one still awaiting a ruling", async () => {
    render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "G", status: "running", run_mode: "plan_and_execute" },
          agents: [], messages: [], tasks: [],
          contests: [
            {
              // The live run produced exactly this: the cycle refused the
              // objection outright, so there is no verdict and never will be.
              // Refiling the same objection is idempotent, so a contest shown
              // as "판정 대기" here waits forever and cannot be retried.
              objection: "nothing owns T-04",
              kind: null,
              reason: null,
              status: "settled",
              error_message: "Team run status 'draft' cannot be contested",
              supersedes: [],
              created_at: "2026-08-12T00:00:00Z"
            },
            {
              objection: "T-15 has no owner",
              kind: null,
              reason: null,
              status: "queued",
              error_message: null,
              supersedes: [],
              created_at: "2026-08-12T01:00:00Z"
            }
          ]
        }}
      />
    );

    expect(
      screen.getByText(/실패 · Team run status 'draft' cannot be contested/)
    ).toBeInTheDocument();
    expect(screen.getByText("판정 대기")).toBeInTheDocument();
  });

  it("carries build evidence and contests from a /detail response into the view", async () => {
    // The seam the three shipped defects hid behind: api.teamRunDetail rebuilds
    // the response field by field, so a top-level field it does not name is
    // invisible to the UI no matter what the endpoint returns.
    const detailBody = {
      team_run: { id: "r1", goal: "G", status: "completed", run_mode: "plan_and_execute" },
      agents: [],
      messages: [],
      tasks: [{ id: "t1", title: "Write the guide", status: "completed" }],
      cycles: [],
      build_evidence_summary: {
        task_count: 13,
        worker_asserted_only_count: 13,
        missing_file_count: 2
      },
      contests: [{
        objection: "nothing owns T-04",
        kind: "reject",
        reason: "task 7 covers it",
        status: "settled",
        error_message: null,
        supersedes: [],
        created_at: "2026-08-12T00:00:00Z"
      }]
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(detailBody)
    });
    vi.stubGlobal("fetch", fetchMock);
    let detail;
    try {
      detail = await api.teamRunDetail("r1");
    } finally {
      vi.unstubAllGlobals();
    }

    render(<TeamRunDetail detail={detail} />);

    expect(screen.getByText(/기각 · task 7 covers it/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));
    expect(screen.getByText(/태스크 13개 중 게이트가 파일 확인 0/)).toBeInTheDocument();
    expect(screen.getByText(/없는 파일 2/)).toBeInTheDocument();
  });

  it("labels a verification nobody confirmed", async () => {
    render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "G", status: "completed", run_mode: "plan_and_execute" },
          agents: [], messages: [],
          buildEvidenceSummary: {
            task_count: 2, worker_asserted_only_count: 0,
            missing_file_count: 0, unverified_task_count: 1
          },
          tasks: [{
            id: "t1", title: "Build the screens", status: "completed",
            build_evidence: {
              promised: [], declared: [], undeclared_promises: [],
              extra_declarations: [], missing_files: [],
              verifications: [
                { name: "ran", mode: "verified", status: "passed" },
                { name: "typecheck", mode: "unverified", status: "unknown" }
              ],
              unverified: ["typecheck"], worker_asserted_only: false
            }
          }]
        }}
      />
    );

    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));
    expect(screen.getByText(/미확인 1/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Open task Build the screens" }));
    const dialog = screen.getByRole("dialog", { name: "Task details: Build the screens" });
    expect(within(dialog).getByText(/미확인/)).toBeInTheDocument();
    expect(within(dialog).getByText(/파일 내용 확인/)).toBeInTheDocument();
    expect(within(dialog).queryByText("검증됨")).not.toBeInTheDocument();
  });

  it("does not call an unchecked verification MISSING in the acceptance list", async () => {
    // The same dialog renders verifications twice: BuildEvidence from the gate's
    // recorded evidence, and this list from the contract. The second one fell back
    // to `status || "missing"`, so an unchecked verification -- whose reported
    // status is null by design -- printed MISSING, which until now meant the one
    // thing it is not: a verification the worker never reported at all. The task-4
    // test above cannot catch this, because its fixture task carries no
    // `acceptance` key and this list renders nothing without one.
    render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "G", status: "completed", run_mode: "plan_and_execute" },
          agents: [], messages: [],
          tasks: [{
            id: "t1", title: "Build the screens", status: "completed",
            acceptance: {
              required_outputs: ["a.tsx"],
              required_verifications: [{ name: "typecheck", check: null }]
            },
            outcome: {
              verifications: [{
                name: "typecheck", checked: false, status: null,
                evidence: "npx --no-install tsc: typescript-unavailable"
              }]
            },
            acceptance_result: {
              evidence: {
                unverified: ["typecheck"],
                verifications: {
                  typecheck: {
                    mode: "unverified", status: "unknown",
                    evidence: "npx --no-install tsc: typescript-unavailable"
                  }
                }
              }
            }
          }]
        }}
      />
    );

    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));
    await userEvent.click(screen.getByRole("button", { name: "Open task Build the screens" }));
    const dialog = screen.getByRole("dialog", { name: "Task details: Build the screens" });

    expect(within(dialog).queryByText("MISSING")).not.toBeInTheDocument();
    expect(within(dialog).getByText("UNKNOWN")).toBeInTheDocument();
    expect(
      within(dialog).getByText(/npx --no-install tsc: typescript-unavailable/)
    ).toBeInTheDocument();
  });

  it("explains that a plan was never approved", async () => {
    render(
      <TeamRunDetail
        detail={{
          run: { id: "r1", goal: "G", status: "completed_with_failures", run_mode: "plan_and_execute" },
          agents: [{ id: "w1", name: "Worker One", role: "member", status: "pending", current_task_id: null }],
          messages: [], tasks: [],
          planRevisions: [{
            revision: 1, status: "abandoned",
            required_approver_agent_ids: ["w1"],
            reviews: { w1: "object" },
            objections: { w1: [{ kind: "gap", task_ref: "T-01", detail: "마이그레이션 담당 없음" }] }
          }]
        }}
      />
    );

    expect(screen.getByText(/합의 실패/)).toBeInTheDocument();
    expect(screen.getByText(/마이그레이션 담당 없음/)).toBeInTheDocument();
  });
});

describe("TeamRunDetail contest availability", () => {
  it("hides the contest form on an interrupted run but keeps past contests", () => {
    render(
      <TeamRunDetail
        onContestPlan={vi.fn()}
        detail={{
          run: { id: "r1", goal: "G", status: "interrupted", run_mode: "plan_and_execute" },
          agents: [],
          messages: [],
          tasks: [],
          contests: [{ objection: "T-3 has no owner", status: "settled", created_at: null }]
        }}
      />
    );

    // Resume is the action that applies here; offering an objection next to it
    // reads as if objecting were the way forward.
    expect(screen.queryByRole("textbox", { name: /계획에 이의/ })).toBeNull();
    expect(screen.getByText(/T-3 has no owner/)).toBeInTheDocument();
  });

  it("hides the contest form when the run has no reviewable plan", () => {
    render(
      <TeamRunDetail
        onContestPlan={vi.fn()}
        detail={{
          run: { id: "r1", goal: "G", status: "running", run_mode: "plan_and_execute" },
          agents: [], messages: [], tasks: [], contests: []
        }}
      />
    );

    expect(screen.queryByText("계획 검토")).toBeNull();
    expect(screen.queryByRole("textbox", { name: /계획에 이의/ })).toBeNull();
  });

  it("offers a collapsed contest form while the run has a reviewable plan", async () => {
    render(
      <TeamRunDetail
        onContestPlan={vi.fn()}
        detail={{
          run: { id: "r1", goal: "G", status: "running", run_mode: "plan_and_execute" },
          agents: [], messages: [], tasks: [], contests: [],
          planRevisions: [APPROVED_PLAN_REVISION]
        }}
      />
    );

    const reviewTitle = screen.getByText("계획 검토");
    expect(reviewTitle.closest("details")).not.toHaveAttribute("open");

    await userEvent.click(reviewTitle);
    expect(screen.getByRole("textbox", { name: /계획에 이의/ })).toBeInTheDocument();
  });
});
