import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { HooksView } from "./index.jsx";

const agents = [
  { id: "codex", label: "Codex", available: true, default_model: "gpt-5", defaults: {}, options_schema: [], model_options: [{ id: "gpt-5", label: "gpt-5", efforts: [] }] }
];

const hooks = [
  {
    id: "h1", name: "Inbox watcher", enabled: true,
    filter: { from_contains: "boss", subject_contains: "", folder: "INBOX" },
    target_backend: "codex", target_model: "gpt-5", target_options: {},
    prompt_template: "요약: {{subject}}", poll_interval_seconds: 300,
    last_polled_at: "2026-07-15T10:32:00Z", last_error: null
  }
];

function noop() {}

describe("HooksView", () => {
  it("lists hooks with enabled state and target summary", () => {
    render(<HooksView hooks={hooks} agents={agents} onCreate={noop} onToggle={noop} onRunNow={noop} onDelete={noop} onOpenRuns={noop} onCloseRuns={noop} onTestConnection={noop} />);
    expect(screen.getByText("Inbox watcher")).toBeInTheDocument();
    expect(screen.getByText("ENABLED")).toBeInTheDocument();
    expect(screen.getByText(/codex\/gpt-5/)).toBeInTheDocument();
  });

  it("builds an email hook from the form (minutes -> seconds, filter, target)", async () => {
    const onCreate = vi.fn();
    render(<HooksView hooks={[]} agents={agents} onCreate={onCreate} onToggle={noop} onRunNow={noop} onDelete={noop} onOpenRuns={noop} onCloseRuns={noop} onTestConnection={noop} />);
    await userEvent.type(screen.getByLabelText("Name"), "Inbox watcher");
    await userEvent.type(screen.getByLabelText("Host"), "imap.gmail.com");
    await userEvent.type(screen.getByLabelText("Username"), "me@gmail.com");
    await userEvent.type(screen.getByLabelText("App password"), "app-pw");
    await userEvent.type(screen.getByLabelText("From contains"), "boss");
    await userEvent.clear(screen.getByLabelText("Poll minutes"));
    await userEvent.type(screen.getByLabelText("Poll minutes"), "5");
    await userEvent.type(screen.getByLabelText("Prompt template"), "요약: {{{{subject}}");
    await userEvent.click(screen.getByRole("button", { name: /create hook/i }));
    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({
      name: "Inbox watcher",
      source_type: "email",
      connection: { host: "imap.gmail.com", port: 993, username: "me@gmail.com" },
      secret: "app-pw",
      target_backend: "codex",
      target_model: "gpt-5",
      prompt_template: "요약: {{subject}}",
      poll_interval_seconds: 300
    }));
    expect(onCreate.mock.calls[0][0].filter.from_contains).toBe("boss");
  });

  it("shows the result of a connection test", async () => {
    const onTestConnection = vi.fn().mockResolvedValue({ ok: false, error: "auth failed" });
    render(<HooksView hooks={[]} agents={agents} onCreate={noop} onToggle={noop} onRunNow={noop} onDelete={noop} onOpenRuns={noop} onCloseRuns={noop} onTestConnection={onTestConnection} />);
    await userEvent.type(screen.getByLabelText("Host"), "imap.gmail.com");
    await userEvent.type(screen.getByLabelText("Username"), "me@gmail.com");
    await userEvent.type(screen.getByLabelText("App password"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: /test connection/i }));
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
