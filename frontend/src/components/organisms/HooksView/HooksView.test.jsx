import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { HooksView } from "./index.jsx";

const agents = [
  { id: "codex", label: "Codex", available: true, default_model: "gpt-5", defaults: {}, options_schema: [], model_options: [{ id: "gpt-5", label: "gpt-5", efforts: [] }] }
];

const personas = [{
  id: "p1",
  name: "Mail Manager",
  role: "Inbox triage",
  description: "Classifies incoming mail.",
  default_backend: "codex",
  default_model: "gpt-5",
  default_options: {}
}];

const hooks = [
  {
    id: "h1", name: "Inbox watcher", enabled: true,
    filter: { from_contains: "boss", subject_contains: "", folder: "INBOX" },
    target_backend: "codex", target_model: "gpt-5", target_options: {},
    prompt_template: "요약: {{subject}}", poll_interval_seconds: 300,
    last_polled_at: "2026-07-15T10:32:00Z", last_error: null
  }
];

const teamRuns = [
  { id: "standard-1", goal: "One-off", lifecycle_mode: "standard", run_mode: "plan_and_execute", execution_policy: "triggered" },
  { id: "auto-1", goal: "AUTO inbox", lifecycle_mode: "continuous", run_mode: "plan_and_execute", execution_policy: "auto" },
  { id: "planning-1", goal: "Planning only", lifecycle_mode: "continuous", run_mode: "planning_only", execution_policy: "triggered" },
  { id: "mail-1", goal: "Mail inbox", lifecycle_mode: "continuous", run_mode: "plan_and_execute", execution_policy: "triggered" }
];

function noop() {}

describe("HooksView", () => {
  it("lists hooks with enabled state and target summary", () => {
    render(<HooksView hooks={hooks} agents={agents} onCreate={noop} onToggle={noop} onRunNow={noop} onDelete={noop} onOpenRuns={noop} onCloseRuns={noop} onTestConnection={noop} />);
    expect(screen.getByText("Inbox watcher")).toBeInTheDocument();
    expect(screen.getByText("ENABLED")).toBeInTheDocument();
    expect(screen.getByText(/codex\/gpt-5/)).toBeInTheDocument();
  });

  it("shows the create form only after CREATE NEW is selected", async () => {
    render(<HooksView hooks={hooks} agents={agents} onCreate={noop} onToggle={noop} onRunNow={noop} onDelete={noop} onOpenRuns={noop} onCloseRuns={noop} onTestConnection={noop} />);
    const createButton = screen.getByRole("button", { name: "CREATE NEW" });

    expect(createButton).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("form", { name: "New hook" })).not.toBeInTheDocument();

    await userEvent.click(createButton);
    expect(createButton).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("form", { name: "New hook" })).toBeInTheDocument();

    await userEvent.click(createButton);
    expect(screen.queryByRole("form", { name: "New hook" })).not.toBeInTheDocument();
  });

  it("builds a Persona email hook from the form (minutes -> seconds, filter, target)", async () => {
    const onCreate = vi.fn();
    const user = userEvent.setup();
    render(<HooksView hooks={[]} agents={agents} personas={personas} onCreate={onCreate} onToggle={noop} onRunNow={noop} onDelete={noop} onOpenRuns={noop} onCloseRuns={noop} onTestConnection={noop} />);
    await user.click(screen.getByRole("button", { name: "CREATE NEW" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Inbox watcher" } });
    fireEvent.change(screen.getByLabelText("Host"), { target: { value: "imap.gmail.com" } });
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "me@gmail.com" } });
    fireEvent.change(screen.getByLabelText("App password"), { target: { value: "app-pw" } });
    fireEvent.change(screen.getByLabelText("From contains"), { target: { value: "boss" } });
    fireEvent.change(screen.getByLabelText("Poll minutes"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("Prompt template"), { target: { value: "요약: {{subject}}" } });
    await user.click(screen.getByRole("button", { name: /create hook/i }));
    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({
      name: "Inbox watcher",
      source_type: "email",
      connection: { host: "imap.gmail.com", port: 993, username: "me@gmail.com" },
      secret: "app-pw",
      target_backend: "",
      target_model: "",
      target_kind: "persona",
      target_persona_id: "p1",
      target_team_run_id: null,
      prompt_template: "요약: {{subject}}",
      poll_interval_seconds: 300
    }));
    expect(onCreate.mock.calls[0][0].filter.from_contains).toBe("boss");
  });

  it("creates a hook targeting an existing continuous Team Run", async () => {
    const onCreate = vi.fn();
    const user = userEvent.setup();
    render(<HooksView hooks={[]} agents={agents} personas={personas} teamRuns={teamRuns} onCreate={onCreate} onToggle={noop} onRunNow={noop} onDelete={noop} onOpenRuns={noop} onCloseRuns={noop} onTestConnection={noop} />);
    await user.click(screen.getByRole("button", { name: "CREATE NEW" }));
    await user.click(screen.getByRole("button", { name: "TEAM RUN" }));
    expect(screen.getByRole("option", { name: "Mail inbox" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "One-off" })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "AUTO inbox" })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Planning only" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Mail team hook" } });
    fireEvent.change(screen.getByLabelText("Host"), { target: { value: "imap.example.com" } });
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "me@example.com" } });
    fireEvent.change(screen.getByLabelText("App password"), { target: { value: "app-pw" } });
    fireEvent.change(screen.getByLabelText("Prompt template"), { target: { value: "Process {{subject}}" } });
    await user.click(screen.getByRole("button", { name: /create hook/i }));

    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({
      target_kind: "team_run",
      target_team_run_id: "mail-1",
      target_backend: "",
      target_model: "",
      target_options: {}
    }));
  });

  it("disables Team Run targets when no exact TRIGGERED candidate exists", async () => {
    render(<HooksView
      hooks={[]}
      agents={agents}
      personas={personas}
      teamRuns={[{
        id: "auto-1",
        goal: "AUTO inbox",
        lifecycle_mode: "continuous",
        run_mode: "plan_and_execute",
        execution_policy: "auto"
      }]}
      onCreate={noop}
      onToggle={noop}
      onRunNow={noop}
      onDelete={noop}
      onOpenRuns={noop}
      onCloseRuns={noop}
      onTestConnection={noop}
    />);

    await userEvent.click(screen.getByRole("button", { name: "CREATE NEW" }));
    expect(screen.getByRole("button", { name: "TEAM RUN" })).toBeDisabled();
    expect(screen.getByText("Create a TRIGGERED Team Run to enable TEAM RUN target."))
      .toBeInTheDocument();
  });

  it("shows the result of a connection test", async () => {
    const onTestConnection = vi.fn().mockResolvedValue({ ok: false, error: "auth failed" });
    const user = userEvent.setup();
    render(<HooksView hooks={[]} agents={agents} onCreate={noop} onToggle={noop} onRunNow={noop} onDelete={noop} onOpenRuns={noop} onCloseRuns={noop} onTestConnection={onTestConnection} />);
    await user.click(screen.getByRole("button", { name: "CREATE NEW" }));
    fireEvent.change(screen.getByLabelText("Host"), { target: { value: "imap.gmail.com" } });
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "me@gmail.com" } });
    fireEvent.change(screen.getByLabelText("App password"), { target: { value: "wrong" } });
    await user.click(screen.getByRole("button", { name: /test connection/i }));
    expect(await screen.findByText(/auth failed/)).toBeInTheDocument();
    expect(onTestConnection).toHaveBeenCalled();
  });

  it("opens the runs drawer and renders run results", () => {
    const runs = [
      { id: "r1", trigger_summary: "메일: hi — boss", status: "succeeded", result_text: "정리했습니다", error_message: null, created_at: "2026-07-15T10:32:00Z" },
      { id: "r2", trigger_summary: "메일: err — x", status: "failed", result_text: null, error_message: "실행 실패", created_at: "2026-07-15T10:33:00Z" }
    ];
    render(<HooksView hooks={hooks} hookRuns={runs} agents={agents} openHookRunsId="h1" onCreate={noop} onToggle={noop} onRunNow={noop} onDelete={noop} onOpenRuns={noop} onCloseRuns={noop} onTestConnection={noop} />);
    const drawer = screen.getByLabelText("Hook runs");
    expect(within(drawer).getByText("정리했습니다")).toBeInTheDocument();
    expect(within(drawer).getByText("실행 실패")).toBeInTheDocument();
  });

  it("shows Hook Run cycle lineage and opens its target Team Run", async () => {
    const onOpenTeamRun = vi.fn();
    const teamHook = {
      ...hooks[0],
      target_kind: "team_run",
      target_team_run_id: "mail-1"
    };
    const runs = [{
      id: "r1",
      trigger_summary: "메일: hi",
      status: "queued",
      created_at: "2026-07-15T10:32:00Z",
      team_run_cycle_id: "cycle-7"
    }];
    render(<HooksView hooks={[teamHook]} hookRuns={runs} agents={agents} teamRuns={teamRuns} openHookRunsId="h1" onCreate={noop} onToggle={noop} onRunNow={noop} onDelete={noop} onOpenRuns={noop} onCloseRuns={noop} onTestConnection={noop} onOpenTeamRun={onOpenTeamRun} />);

    expect(screen.getByText("CYCLE · cycle-7")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Open Team Run" }));
    expect(onOpenTeamRun).toHaveBeenCalledWith("mail-1");
  });

  it("calls onRunNow and onToggle", async () => {
    const onRunNow = vi.fn();
    const onToggle = vi.fn();
    render(<HooksView hooks={hooks} agents={agents} onCreate={noop} onToggle={onToggle} onRunNow={onRunNow} onDelete={noop} onOpenRuns={noop} onCloseRuns={noop} onTestConnection={noop} />);
    await userEvent.click(screen.getByRole("button", { name: /run now/i }));
    await userEvent.click(screen.getByRole("button", { name: /pause/i }));
    expect(onRunNow).toHaveBeenCalledWith("h1");
    expect(onToggle).toHaveBeenCalledWith("h1", false);
  });
});
