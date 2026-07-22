import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GatewayApp, applyTeamRunDelta } from "./index.jsx";
import { UiProvider } from "../../providers/UiProvider/index.jsx";

const teamRunDetailCapture = vi.hoisted(() => ({ props: null }));

vi.mock("../../organisms/TeamRunDetail/index.jsx", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    TeamRunDetail: function CapturedTeamRunDetail(props) {
      teamRunDetailCapture.props = props;
      const ActualTeamRunDetail = actual.TeamRunDetail;
      return <ActualTeamRunDetail {...props} />;
    }
  };
});

function response(body, ok = true) {
  return Promise.resolve({ ok, json: () => Promise.resolve(body) });
}

function deferredResponse() {
  let resolve;
  const promise = new Promise((settle) => {
    resolve = (body, ok = true) => settle({ ok, json: () => Promise.resolve(body) });
  });
  return { promise, resolve };
}

function installFetch(routes) {
  globalThis.fetch = vi.fn((url, init = {}) => {
    const method = init.method || "GET";
    const key = `${method} ${url}`;
    const match = routes[key] ?? routes[url];
    if (!match) return response({}, false);
    return typeof match === "function" ? match(url, init) : response(match);
  });
}

const status = {
  provider: "codex",
  model: "default",
  workspace_root: "C:/repo",
  session_id: "session-1",
  message_count: 0,
  pending_approval: false,
  session_status: "idle"
};

const sessions = [{
  id: "session-1",
  title: "Main chat",
  status: "idle",
  message_count: 0,
  is_active: true,
  created_at: "2026-07-08T01:00:00Z"
}];

describe("GatewayApp", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    teamRunDetailCapture.props = null;
  });

  afterEach(() => {
    localStorage.removeItem("pag.browser-notifications.v1");
    vi.unstubAllGlobals();
  });

  it("applies Team SSE entity deltas without requiring a full detail response", () => {
    const detail = {
      run: { id: "run-1", status: "running" },
      agents: [{ id: "agent-1", status: "pending", current_task_id: null }],
      tasks: [{ id: "task-1", status: "pending" }],
      messages: []
    };

    const updated = applyTeamRunDelta(detail, {
      run: { id: "run-1", status: "summarizing" },
      task: { id: "task-1", status: "completed" },
      agent: { id: "agent-1", status: "running", current_task_id: "task-1" }
    });

    expect(updated.run.status).toBe("summarizing");
    expect(updated.tasks[0].status).toBe("completed");
    expect(updated.agents[0]).toMatchObject({ status: "running", current_task_id: "task-1" });
  });

  it("boots authenticated users into the chat shell and preserves planned tabs", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/jobs": { jobs: [] },
      "GET /api/schedules": { schedules: [] }
    });

    render(<GatewayApp />);

    expect(await screen.findByLabelText("Agent Gateway")).toBeInTheDocument();
    expect(screen.getAllByText("Main chat").length).toBeGreaterThan(0);
    expect(screen.getByText("AGENT IDLE")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Jobs" }));
    expect(await screen.findByRole("heading", { name: "Jobs" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Schedules" }));
    expect(await screen.findByRole("heading", { name: "Schedules" })).toBeInTheDocument();
  });

  it("shows the configured environment title in the browser title and sidebar footer", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": { ...status, environment_title: "MPX PC" },
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null }
    });

    render(<GatewayApp />);

    expect(await screen.findByText("PC(MPX)")).toBeInTheDocument();
    await waitFor(() => expect(document.title).toBe("MPX PC · Agent Gateway"));
  });

  it("shows working sessions and moves session actions into a popover menu", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions: [{ ...sessions[0], status: "running" }] },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "DELETE /api/sessions/session-1": { deleted: true, active_session_id: null }
    });

    render(<GatewayApp />);

    expect(await screen.findByText("WORKING")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Rename" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Session actions Main chat" }));

    const menu = await screen.findByRole("menu", { name: "Session actions" });
    expect(menu).toBeInTheDocument();
    expect(screen.getByLabelText("Sessions")).not.toContainElement(menu);
    expect(screen.getByRole("menuitem", { name: "Rename" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("menuitem", { name: "Delete" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/sessions/session-1",
      expect.objectContaining({ method: "DELETE" })
    ));
    window.confirm.mockRestore();
  });

  it("shows the active session config in the statusbar even when /api/status is stale", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": { ...status, provider: "openai", model: "legacy-model" },
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: { agent_id: "claude", model: "sonnet", options: {}, editable: true, source: "explicit" } }
    });

    render(<GatewayApp />);

    expect(await screen.findByText("claude")).toBeInTheDocument();
    expect(screen.getAllByText("sonnet").length).toBeGreaterThan(0);
  });

  it("preserves legacy app status metadata when no explicit session config exists", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": {
        ...status,
        provider: "openai",
        model: "legacy-model",
        session_config: { agent_id: "codex", model: "default", options: {}, editable: true, source: "default" }
      },
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": {
        config: { agent_id: "codex", model: "default", options: {}, editable: true, source: "default" }
      }
    });

    render(<GatewayApp />);

    expect(await screen.findByText("openai")).toBeInTheDocument();
    expect(screen.getByText("legacy-model")).toBeInTheDocument();
  });

  it("supports OTP login before loading protected API data", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: false, totp_configured: true },
      "POST /api/auth/login": {},
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null }
    });

    render(<GatewayApp />);

    await userEvent.type(await screen.findByPlaceholderText("000000"), "123456");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/auth/login", expect.any(Object)));
    await waitFor(() => expect(screen.getAllByText("Main chat").length).toBeGreaterThan(0));
  });

  it("sends chat messages and renders non-streamed fallback agent responses", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "POST /api/sessions/session-1/chat": { messages: [{ content: "Fallback answer" }], pending_approval: null },
      "GET /api/artifacts": { artifacts: [] }
    });

    render(<GatewayApp />);

    const input = await screen.findByPlaceholderText("Message the agent, or describe a local action...");
    await userEvent.type(input, "hello");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("hello")).toBeInTheDocument();
    expect(await screen.findByText("Fallback answer")).toBeInTheDocument();
  });

  it("keeps a newly sent message below older history (createdAtMs stamped)", async () => {
    const oldEvents = [{ kind: "assistant", created_at: "2020-01-01T00:00:00Z", payload: { content: "old answer" } }];
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: oldEvents },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/artifacts": { artifacts: [] },
      "GET /api/sessions/session-1/history": { events: oldEvents },
      "GET /api/sessions/session-1/activity": { events: [] },
      "GET /api/sessions/session-1/status": { session_id: "session-1", status: "idle", pending_approval: false },
      "POST /api/sessions/session-1/chat": () => response({ messages: [], pending_approval: false, session_id: "session-1", request_id: "r1" })
    });

    render(<GatewayApp />);
    await screen.findByLabelText("Agent Gateway");

    const composer = screen.getByPlaceholderText("Message the agent, or describe a local action...");
    await userEvent.type(composer, "hello");
    await userEvent.keyboard("{Enter}");

    await waitFor(() => expect(screen.getByText("hello")).toBeInTheDocument());
    const texts = Array.from(document.querySelectorAll(".msg-user .bubble, .msg-agent .bubble")).map((node) => node.textContent);
    expect(texts.indexOf("old answer")).toBeLessThan(texts.indexOf("hello"));
  });

  it("uses the session approval endpoint for pending approvals", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "POST /api/sessions/session-1/chat": {
        messages: [],
        pending_approval: { id: "approval-1", command: "dir" }
      },
      "POST /api/sessions/session-1/approvals/approval-1/approve": {
        session_id: "session-1",
        request_id: "request-1",
        messages: [{ content: "Approved via session endpoint" }],
        pending_approval: null
      },
      "GET /api/artifacts": { artifacts: [] }
    });

    render(<GatewayApp />);

    const input = await screen.findByPlaceholderText("Message the agent, or describe a local action...");
    await userEvent.type(input, "run it");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByText("WAITING APPROVAL");
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/sessions/session-1/approvals/approval-1/approve",
      expect.objectContaining({ method: "POST" })
    ));
    expect(fetch).not.toHaveBeenCalledWith(
      "/api/approvals/approval-1/approve",
      expect.anything()
    );
    expect(await screen.findByText("Approved via session endpoint")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("WAITING APPROVAL")).not.toBeInTheDocument());
  });

  it("keeps pending approval visible when approval request is rejected", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "POST /api/sessions/session-1/chat": {
        messages: [],
        pending_approval: { id: "approval-1", command: "dir" }
      },
      "POST /api/sessions/session-1/approvals/approval-1/approve": () => response({ detail: "Session is already running" }, false),
      "GET /api/artifacts": { artifacts: [] }
    });

    render(<UiProvider><GatewayApp /></UiProvider>);

    const input = await screen.findByPlaceholderText("Message the agent, or describe a local action...");
    await userEvent.type(input, "run it");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByText("WAITING APPROVAL");
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));

    expect(await screen.findByText("Failed to resolve approval")).toBeInTheDocument();
    expect(screen.getByText("WAITING APPROVAL")).toBeInTheDocument();
  });

  it("searches and activates sessions from the session rail", async () => {
    let activeSessionId = "session-1";
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": () => response({ ...status, session_id: activeSessionId }),
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/sessions/search?q=old": { sessions: [{ ...sessions[0], id: "session-2", title: "Old chat", is_active: false }] },
      "POST /api/sessions/session-2/activate": {
        session_id: "session-2"
      },
      "GET /api/sessions/session-2/history": {
        events: [{ kind: "user", created_at: "2026-07-08T01:02:00Z", payload: { content: "previous" } }]
      },
      "GET /api/sessions/session-2/activity": { events: [] },
      "GET /api/sessions/session-2/status": () => {
        activeSessionId = "session-2";
        return response({ status: "idle", session_id: "session-2" });
      }
    });

    render(<GatewayApp />);

    const rail = await screen.findByLabelText("Sessions");
    await userEvent.type(within(rail).getByPlaceholderText("Search"), "old");
    await userEvent.click(await screen.findByText("Old chat"));

    expect(await screen.findByText("previous")).toBeInTheDocument();
  });

  it("clears active transcript state after deleting the active session", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    let deleted = false;
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": () => response(deleted ? { ...status, session_id: null, message_count: 0 } : status),
      "GET /api/sessions": () => response({ sessions: deleted ? [] : sessions }),
      "GET /api/history": {
        events: [{ kind: "user", created_at: "2026-07-08T01:02:00Z", payload: { content: "deleted session text" } }]
      },
      "GET /api/sessions/session-1/history": {
        events: [{ kind: "user", created_at: "2026-07-08T01:02:00Z", payload: { content: "deleted session text" } }]
      },
      "GET /api/sessions/session-1/activity": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "DELETE /api/sessions/session-1": () => {
        deleted = true;
        return response({ deleted: true, active_session_id: null });
      }
    });

    render(<GatewayApp />);

    expect(await screen.findByText("deleted session text")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Session actions Main chat" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Delete" }));

    await waitFor(() => expect(screen.queryByText("deleted session text")).not.toBeInTheDocument());
    expect(screen.getByText("AGENT IDLE")).toBeInTheDocument();
  });

  it("rebinds frontend active session to the reset session before the next send", async () => {
    let resetCount = 0;
    let activeSessionId = "session-1";
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": () => response({ ...status, session_id: activeSessionId }),
      "GET /api/sessions": () => response({
        sessions: activeSessionId === "session-2"
          ? [{ ...sessions[0], id: "session-2", title: "Fresh session", is_active: true }]
          : sessions
      }),
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "POST /api/reset": () => {
        resetCount += 1;
        activeSessionId = "session-2";
        return response({ session_id: "session-2" });
      },
      "POST /api/sessions/session-2/chat": { messages: [{ content: "After reset reply" }], pending_approval: null },
      "GET /api/artifacts": { artifacts: [] }
    });

    render(<GatewayApp />);

    await screen.findByLabelText("Agent Gateway");
    await userEvent.click(screen.getByRole("button", { name: "+" }));

    await waitFor(() => expect(screen.getAllByText("Fresh session").length).toBeGreaterThan(0));

    const input = screen.getByPlaceholderText("Message the agent, or describe a local action...");
    await userEvent.type(input, "hello after reset");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/sessions/session-2/chat",
      expect.objectContaining({ method: "POST" })
    ));
    expect(resetCount).toBe(1);
    expect(await screen.findByText("After reset reply")).toBeInTheDocument();
  });

  it("ignores live chat events from non-active sessions", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null }
    });

    render(<GatewayApp />);

    await screen.findByLabelText("Agent Gateway");
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0));
    const source = MockEventSource.instances[0];

    act(() => {
      source.emit({ session_id: "session-2", item: { type: "agent_message", text: "wrong session answer" } });
    });

    expect(screen.queryByText("wrong session answer")).not.toBeInTheDocument();

    act(() => {
      source.emit({ session_id: "session-1", item: { type: "agent_message", text: "active session answer" } });
    });

    expect(await screen.findByText("active session answer")).toBeInTheDocument();
  });

  it("keeps non-active session SSE entries in that session cache and shows them after activation", async () => {
    let activeSessionId = "session-1";
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": () => response({ ...status, session_id: activeSessionId }),
      "GET /api/sessions": { sessions: [
        sessions[0],
        { ...sessions[0], id: "session-2", title: "Background chat", is_active: false }
      ] },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/sessions/session-1/history": { events: [] },
      "GET /api/sessions/session-1/activity": { events: [] },
      "GET /api/sessions/session-1/status": { status: "idle", session_id: "session-1" },
      "GET /api/sessions/session-2/history": { events: [] },
      "GET /api/sessions/session-2/activity": { events: [] },
      "GET /api/sessions/session-2/status": () => {
        activeSessionId = "session-2";
        return response({ status: "idle", session_id: "session-2" });
      },
      "POST /api/sessions/session-2/activate": { session_id: "session-2" }
    });

    render(<GatewayApp />);

    await screen.findByLabelText("Agent Gateway");
    await waitFor(() => expect(MockEventSource.instances.length).toBe(1));
    const source = MockEventSource.instances[0];
    act(() => {
      source.emit({
        id: 50,
        session_id: "session-2",
        event_seq: 1,
        type: "codex.event",
        payload: { item: { type: "agent_message", id: "agent-2", text: "background answer" } }
      });
    });

    expect(screen.queryByText("background answer")).not.toBeInTheDocument();
    await userEvent.click(await screen.findByText("Background chat"));

    expect(await screen.findByText("background answer")).toBeInTheDocument();
  });

  it("does not disable active composer when another session is busy", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions: [
        sessions[0],
        { ...sessions[0], id: "session-2", title: "Background chat", status: "running", is_active: false }
      ] },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/sessions/session-1/history": { events: [] },
      "GET /api/sessions/session-1/activity": { events: [] },
      "GET /api/sessions/session-1/status": { status: "idle", session_id: "session-1" }
    });

    render(<GatewayApp />);

    await screen.findByLabelText("Agent Gateway");
    await waitFor(() => expect(MockEventSource.instances.length).toBe(1));
    const source = MockEventSource.instances[0];
    act(() => {
      source.emit({
        id: 51,
        session_id: "session-2",
        event_seq: 1,
        type: "runtime.user_message.started",
        payload: { message: "background work" }
      });
    });

    expect(screen.getByPlaceholderText("Message the agent, or describe a local action...")).not.toBeDisabled();
    expect(screen.getByText("AGENT IDLE")).toBeInTheDocument();
  });

  it("keeps active session state when deleting a running session fails", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [
        { kind: "user", created_at: "2026-07-09T01:00:00Z", payload: { content: "hello" } }
      ] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/sessions/session-1/history": { events: [
        { kind: "user", created_at: "2026-07-09T01:00:00Z", payload: { content: "hello" } }
      ] },
      "GET /api/sessions/session-1/activity": { events: [] },
      "GET /api/sessions/session-1/status": { status: "idle", session_id: "session-1" },
      "DELETE /api/sessions/session-1": () => response({ detail: "Session is running" }, false)
    });

    render(<UiProvider><GatewayApp /></UiProvider>);

    expect(await screen.findByText("hello")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Session actions Main chat" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog", { name: "DELETE SESSION" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

    expect(await screen.findByText("Failed to delete session")).toBeInTheDocument();
    expect(screen.getByText("hello")).toBeInTheDocument();
    expect(screen.queryByText("Session deleted")).not.toBeInTheDocument();
  });

  it("does not recreate the SSE connection when chat busy state changes", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "POST /api/sessions/session-1/chat": { messages: [{ content: "Fallback answer" }], pending_approval: null },
      "GET /api/artifacts": { artifacts: [] }
    });

    render(<GatewayApp />);

    const input = await screen.findByPlaceholderText("Message the agent, or describe a local action...");
    await waitFor(() => expect(MockEventSource.instances.length).toBe(1));
    await userEvent.type(input, "hello");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Fallback answer")).toBeInTheDocument();
    expect(MockEventSource.instances.length).toBe(1);
  });

  it("removes unsent local user row when chat request is rejected", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "POST /api/sessions/session-1/chat": () => response({ detail: "Session is already running" }, false)
    });

    render(<UiProvider><GatewayApp /></UiProvider>);

    const input = await screen.findByPlaceholderText("Message the agent, or describe a local action...");
    await userEvent.type(input, "lost message");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Failed to send message")).toBeInTheDocument();
    expect(screen.queryByText("lost message")).not.toBeInTheDocument();
  });

  it("renders HTTP fallback answer when only runtime activity streamed", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "POST /api/sessions/session-1/chat": { messages: [{ content: "HTTP answer" }], pending_approval: null },
      "GET /api/artifacts": { artifacts: [] }
    });

    render(<GatewayApp />);

    const input = await screen.findByPlaceholderText("Message the agent, or describe a local action...");
    await waitFor(() => expect(MockEventSource.instances.length).toBe(1));
    const source = MockEventSource.instances[0];
    await userEvent.type(input, "hello");
    const send = screen.getByRole("button", { name: "Send" });
    await userEvent.click(send);
    act(() => {
      source.emit({
        id: 101,
        session_id: "session-1",
        event_seq: 1,
        type: "runtime.user_message.started",
        payload: { message: "hello" }
      });
      source.emit({
        id: 102,
        session_id: "session-1",
        event_seq: 2,
        type: "runtime.completed",
        payload: { pending_approval: null }
      });
    });

    expect(await screen.findByText("HTTP answer")).toBeInTheDocument();
  });

  it("reconciles a late streamed agent answer with the HTTP fallback answer", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "POST /api/sessions/session-1/chat": { messages: [{ content: "Same final answer" }], pending_approval: null },
      "GET /api/artifacts": { artifacts: [] }
    });

    render(<GatewayApp />);

    const input = await screen.findByPlaceholderText("Message the agent, or describe a local action...");
    await waitFor(() => expect(MockEventSource.instances.length).toBe(1));
    const source = MockEventSource.instances[0];
    await userEvent.type(input, "hello");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Same final answer")).toBeInTheDocument();

    act(() => {
      source.emit({
        id: 103,
        session_id: "session-1",
        event_seq: 3,
        type: "codex.event",
        payload: { item: { type: "agent_message", id: "agent-1", text: "Same final answer" } }
      });
    });

    expect(screen.getAllByText("Same final answer")).toHaveLength(1);
  });

  it("ignores duplicate SSE event ids", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null }
    });

    render(<GatewayApp />);

    await screen.findByLabelText("Agent Gateway");
    await waitFor(() => expect(MockEventSource.instances.length).toBe(1));
    const source = MockEventSource.instances[0];

    act(() => {
      source.emit({ id: 10, session_id: "session-1", item: { type: "agent_message", text: "streamed once" } });
      source.emit({ id: 10, session_id: "session-1", item: { type: "agent_message", text: "streamed once" } });
    });

    expect(await screen.findByText("streamed once")).toBeInTheDocument();
    expect(screen.getAllByText("streamed once")).toHaveLength(1);
  });

  it("loads Personas for an empty session and saves the selected Persona", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": { ...status, session_config: { agent_id: "codex", model: "default", options: {}, editable: true } },
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [
        { id: "codex", label: "Codex CLI", available: true, models: ["default"], default_model: "default", defaults: {}, options_schema: [] },
        { id: "claude", label: "Claude Code", available: true, models: ["sonnet"], default_model: "sonnet", defaults: { effort: "medium" }, options_schema: [{ name: "effort", kind: "select", choices: ["medium", "high"] }] }
      ] },
      "GET /api/sessions/active/config": { config: { agent_id: "codex", model: "default", options: {}, editable: true } },
      "GET /api/personas": { personas: [{
        id: "p1", name: "Mail Manager", role: "Inbox triage",
        default_backend: "claude", default_model: "sonnet", default_options: { effort: "medium" }
      }] },
      "PUT /api/sessions/active/config": {
        config: {
          persona_id: "p1", persona_snapshot: { id: "p1", name: "Mail Manager" },
          agent_id: "claude", model: "sonnet", options: { effort: "medium" }, editable: true
        }
      }
    });

    render(<GatewayApp />);

    await screen.findByRole("option", { name: "Mail Manager — Inbox triage" });
    await userEvent.selectOptions(await screen.findByLabelText("Persona"), "p1");

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/sessions/active/config",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ persona_id: "p1" })
      })
    ));
  });

  it("clears config save errors after activating another session", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": { ...status, session_config: { agent_id: "codex", model: "default", options: {}, editable: true } },
      "GET /api/sessions": { sessions: [
        sessions[0],
        { ...sessions[0], id: "session-2", title: "Old chat", is_active: false }
      ] },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [
        { id: "codex", label: "Codex CLI", available: true, models: ["default"], default_model: "default", defaults: {}, options_schema: [] },
        { id: "claude", label: "Claude Code", available: true, models: ["sonnet"], default_model: "sonnet", defaults: { effort: "medium" }, options_schema: [{ name: "effort", kind: "select", choices: ["medium", "high"] }] }
      ] },
      "GET /api/sessions/active/config": { config: { agent_id: "codex", model: "default", options: {}, editable: true } },
      "GET /api/personas": { personas: [{
        id: "p1", name: "Mail Manager", role: "Inbox triage",
        default_backend: "claude", default_model: "sonnet", default_options: { effort: "medium" }
      }] },
      "PUT /api/sessions/active/config": response({}, false),
      "POST /api/sessions/session-2/activate": {
        session_id: "session-2",
        events: []
      }
    });

    render(<GatewayApp />);

    await screen.findByRole("option", { name: "Mail Manager — Inbox triage" });
    await userEvent.selectOptions(await screen.findByLabelText("Persona"), "p1");
    expect(await screen.findByText("Config update failed")).toBeInTheDocument();

    await userEvent.click(await screen.findByText("Old chat"));

    await waitFor(() => expect(screen.queryByText("Config update failed")).not.toBeInTheDocument());
  });

  it("shows locked session config read-only after history has messages", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": { ...status, provider: "claude", model: "sonnet", session_config: { agent_id: "claude", model: "sonnet", options: { effort: "high" }, editable: false } },
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [{ kind: "user", created_at: "2026-07-08T01:02:00Z", payload: { content: "previous" } }] },
      "GET /api/agents": { agents: [{ id: "claude", label: "Claude Code", available: true, models: ["sonnet"], default_model: "sonnet", defaults: {}, options_schema: [] }] },
      "GET /api/sessions/active/config": { config: { agent_id: "claude", model: "sonnet", options: { effort: "high" }, editable: false } }
    });

    render(<GatewayApp />);

    const compactStatus = await screen.findByLabelText("Locked session status");

    expect(compactStatus).toHaveTextContent("SESSION CONFIG");
    expect(compactStatus).toHaveTextContent(/AGENT\s*Claude Code/);
    expect(compactStatus).toHaveTextContent(/MODEL\s*sonnet/);
    expect(compactStatus).toHaveTextContent(/EFFORT\s*high/);
    expect(compactStatus).toHaveTextContent("LOCKED");
    expect(compactStatus).toHaveTextContent("FIRST MESSAGE SENT");
    expect(compactStatus).toHaveTextContent("PHASE");
    expect(compactStatus).toHaveTextContent(/RUNNING\s*0/);
    expect(screen.queryByText("CURRENT PHASE")).not.toBeInTheDocument();
  });

  it("loads the persona library and shows the selected persona detail", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/personas": { personas: [{ id: "p1", name: "Tech Lead", role: "Planning", description: "Owns the plan" }] }
    });

    render(<GatewayApp />);

    await userEvent.click(await screen.findByRole("button", { name: "Personas" }));
    expect(await screen.findByRole("heading", { name: "Personas" })).toBeInTheDocument();

    // the first persona is auto-selected and its detail is shown
    expect(await screen.findByDisplayValue("Tech Lead")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Owns the plan")).toBeInTheDocument();
  });

  it("shows operational metadata in the team-run list", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/personas": { personas: [] },
      "GET /api/team-runs": {
        team_runs: [{
          id: "run-1",
          goal: "Ship it",
          team_name: "Release Crew",
          status: "running",
          display_status: "active",
          run_mode: "plan_and_execute",
          team_id: "t1",
          leader_name: "Tech Lead",
          members: [{ name: "Frontend Dev", avatar: null, initials: "FD" }],
          task_counts: { completed: 1, in_progress: 1, pending: 1 },
          task_done: 1,
          task_total: 3,
          elapsed_seconds: 125,
          cycle_count: 1,
          latest_cycle: { sequence: 1, status: "running" },
          current_objective: "Ship it"
        }]
      }
    });

    render(<GatewayApp />);
    await userEvent.click(await screen.findByRole("button", { name: "Team Runs" }));

    const runButton = await screen.findByRole("button", { name: "Open team run Release Crew · run-1" });
    expect(runButton).toHaveTextContent("PLAN_AND_EXECUTE");
    expect(runButton).toHaveTextContent("Tech Lead");
    expect(runButton).toHaveTextContent("1 / 3 DONE");
  });

  it("deletes a team run from the list after confirmation", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    let teamRunsCalls = 0;
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/personas": { personas: [] },
      "GET /api/team-runs": () => {
        teamRunsCalls += 1;
        return response({
          team_runs: teamRunsCalls > 1 ? [] : [{
            id: "run-1",
            goal: "Ship it",
            status: "running",
            run_mode: "plan_and_execute",
            team_id: "t1",
            leader_name: "Tech Lead",
            members: [],
            task_counts: {},
            task_done: 0,
            task_total: 1,
            elapsed_seconds: 10
          }]
        });
      },
      "DELETE /api/team-runs/run-1": {}
    });

    render(<GatewayApp />);
    await userEvent.click(await screen.findByRole("button", { name: "Team Runs" }));
    await screen.findByText("Ship it");

    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/team-runs/run-1",
      expect.objectContaining({ method: "DELETE" })
    ));
    await waitFor(() => expect(teamRunsCalls).toBeGreaterThanOrEqual(2));
    await waitFor(() => expect(screen.queryByText("Ship it")).not.toBeInTheDocument());
    window.confirm.mockRestore();
  });

  it("submits additional work and refreshes the selected team run", async () => {
    let detailCalls = 0;
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/personas": { personas: [] },
      "GET /api/team-runs": { team_runs: [{ id: "run-1", goal: "Ship it", status: "running", run_mode: "plan_and_execute" }] },
      "GET /api/team-runs/run-1": () => {
        detailCalls += 1;
        return response({ team_run: { id: "run-1", goal: "Ship it", status: "running", run_mode: "plan_and_execute" } });
      },
      "GET /api/team-runs/run-1/agents": { agents: [] },
      "GET /api/team-runs/run-1/tasks": { tasks: [] },
      "GET /api/team-runs/run-1/messages": { messages: [] },
      "POST /api/team-runs/run-1/add-work": { team_run: { id: "run-1", status: "running" } }
    });

    render(<UiProvider><GatewayApp /></UiProvider>);
    await userEvent.click(await screen.findByRole("button", { name: "Team Runs" }));
    await userEvent.click(await screen.findByRole("button", { name: "Open team run Ship it" }));
    await userEvent.click(await screen.findByRole("button", { name: "Add work" }));
    await userEvent.type(screen.getByLabelText("Additional work"), "write release notes");
    await userEvent.click(screen.getByRole("button", { name: "Request work" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/team-runs/run-1/add-work",
      expect.objectContaining({ body: JSON.stringify({ instruction: "write release notes" }) })
    ));
    await waitFor(() => expect(detailCalls).toBeGreaterThanOrEqual(2));
    expect(await screen.findByText("추가 업무를 전달했습니다")).toBeInTheDocument();
  });

  it("keeps the add-work dialog open when the request fails", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/personas": { personas: [] },
      "GET /api/team-runs": { team_runs: [{ id: "run-1", goal: "Ship it", status: "running", run_mode: "plan_and_execute" }] },
      "GET /api/team-runs/run-1": { team_run: { id: "run-1", goal: "Ship it", status: "running", run_mode: "plan_and_execute" } },
      "GET /api/team-runs/run-1/agents": { agents: [] },
      "GET /api/team-runs/run-1/tasks": { tasks: [] },
      "GET /api/team-runs/run-1/messages": { messages: [] },
      "POST /api/team-runs/run-1/add-work": () => response({}, false)
    });

    render(<UiProvider><GatewayApp /></UiProvider>);
    await userEvent.click(await screen.findByRole("button", { name: "Team Runs" }));
    await userEvent.click(await screen.findByRole("button", { name: "Open team run Ship it" }));
    await userEvent.click(await screen.findByRole("button", { name: "Add work" }));
    await userEvent.type(screen.getByLabelText("Additional work"), "write release notes");
    await userEvent.click(screen.getByRole("button", { name: "Request work" }));

    expect(await screen.findByText("Failed to add work")).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Add work" })).toBeInTheDocument();
    expect(screen.getByLabelText("Additional work")).toHaveValue("write release notes");
  });

  it("confirms and resumes an interrupted team run, then refreshes detail and list", async () => {
    let listCalls = 0;
    let detailCalls = 0;
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/personas": { personas: [] },
      "GET /api/team-runs": () => {
        listCalls += 1;
        return response({ team_runs: [{ id: "run-1", goal: "Ship it", status: "interrupted", run_mode: "plan_and_execute" }] });
      },
      "GET /api/team-runs/run-1": () => {
        detailCalls += 1;
        return response({ team_run: { id: "run-1", goal: "Ship it", status: "interrupted", run_mode: "plan_and_execute" } });
      },
      "GET /api/team-runs/run-1/agents": { agents: [] },
      "GET /api/team-runs/run-1/tasks": { tasks: [{ id: "t1", title: "Continue UI", status: "pending" }] },
      "GET /api/team-runs/run-1/messages": { messages: [] },
      "POST /api/team-runs/run-1/resume": { team_run: { id: "run-1", status: "interrupted" } }
    });

    render(<UiProvider><GatewayApp /></UiProvider>);
    await userEvent.click(await screen.findByRole("button", { name: "Team Runs" }));
    await userEvent.click(await screen.findByRole("button", { name: "Open team run Ship it" }));
    await userEvent.click(await screen.findByRole("button", { name: "Resume" }));
    const dialog = await screen.findByRole("dialog", { name: "RESUME TEAM RUN" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Resume" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/team-runs/run-1/resume",
      expect.objectContaining({ method: "POST" })
    ));
    await waitFor(() => expect(detailCalls).toBeGreaterThanOrEqual(2));
    await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(2));
    expect(await screen.findByText("팀 작업을 재개했습니다")).toBeInTheDocument();
  });

  it("confirms and stops an active team run, then refreshes detail and list", async () => {
    let listCalls = 0;
    let detailCalls = 0;
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/personas": { personas: [] },
      "GET /api/team-runs": () => {
        listCalls += 1;
        return response({ team_runs: [{ id: "run-1", goal: "Ship it", status: "running", run_mode: "plan_and_execute" }] });
      },
      "GET /api/team-runs/run-1": () => {
        detailCalls += 1;
        return response({ team_run: {
          id: "run-1", goal: "Ship it", status: detailCalls > 1 ? "canceled" : "running", run_mode: "plan_and_execute"
        } });
      },
      "GET /api/team-runs/run-1/agents": { agents: [] },
      "GET /api/team-runs/run-1/tasks": { tasks: [] },
      "GET /api/team-runs/run-1/messages": { messages: [] },
      "POST /api/team-runs/run-1/cancel": { team_run: { id: "run-1", status: "canceled" } }
    });

    render(<UiProvider><GatewayApp /></UiProvider>);
    await userEvent.click(await screen.findByRole("button", { name: "Team Runs" }));
    await userEvent.click(await screen.findByRole("button", { name: "Open team run Ship it" }));
    await userEvent.click(await screen.findByRole("button", { name: "Stop run" }));
    const dialog = await screen.findByRole("dialog", { name: "STOP TEAM RUN" });
    expect(dialog).toHaveTextContent("documents and completed work are kept");
    await userEvent.click(within(dialog).getByRole("button", { name: "Stop run" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/team-runs/run-1/cancel",
      expect.objectContaining({ method: "POST" })
    ));
    await waitFor(() => expect(detailCalls).toBeGreaterThanOrEqual(2));
    await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(2));
    expect(await screen.findByText("팀 작업을 중지했습니다")).toBeInTheDocument();
  });

  it("confirms a failed task retry and refreshes detail and list", async () => {
    let listCalls = 0;
    let detailCalls = 0;
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/personas": { personas: [] },
      "GET /api/team-runs": () => {
        listCalls += 1;
        return response({ team_runs: [{ id: "run-1", goal: "Ship it", status: "completed_with_failures", run_mode: "plan_and_execute" }] });
      },
      "GET /api/team-runs/run-1": () => {
        detailCalls += 1;
        return response({ team_run: { id: "run-1", goal: "Ship it", status: "completed_with_failures", run_mode: "plan_and_execute" } });
      },
      "GET /api/team-runs/run-1/agents": { agents: [] },
      "GET /api/team-runs/run-1/tasks": { tasks: [{ id: "t1", title: "Run QA", status: "failed", error_message: "timed out" }] },
      "GET /api/team-runs/run-1/messages": { messages: [] },
      "POST /api/team-runs/run-1/tasks/t1/retry": {
        team_run: { id: "run-1", status: "interrupted" },
        task: { id: "t1", status: "pending" }
      }
    });

    render(<UiProvider><GatewayApp /></UiProvider>);
    await userEvent.click(await screen.findByRole("button", { name: "Team Runs" }));
    await userEvent.click(await screen.findByRole("button", { name: "Open team run Ship it" }));
    await userEvent.click(await screen.findByRole("tab", { name: /TASKS/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Open task Run QA" }));
    await userEvent.click(screen.getByRole("button", { name: "Retry failed task" }));
    const dialog = await screen.findByRole("dialog", { name: "RETRY FAILED TASK" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/team-runs/run-1/tasks/t1/retry",
      expect.objectContaining({ method: "POST" })
    ));
    await waitFor(() => expect(detailCalls).toBeGreaterThanOrEqual(2));
    await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(2));
    expect(await screen.findByText("실패한 업무를 재시도 대기열에 추가했습니다")).toBeInTheDocument();
  });

  it("creates a fixed TRIGGERED team run without calling start and refreshes on mixed team SSE events", async () => {
    let taskCalls = 0;
    let teamRunsCalls = 0;
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/teams": {
        teams: [{ id: "t1", name: "Release Crew", leader: { name: "Tech Lead", avatar: null }, members: [] }]
      },
      "GET /api/team-runs": () => {
        teamRunsCalls += 1;
        return response({
          team_runs: teamRunsCalls > 2
            ? [{ id: "run-1", goal: "", team_name: "Release Crew", current_objective: "Ready for trigger", display_status: "ready", status: "completed", run_mode: "plan_and_execute", lifecycle_mode: "continuous", execution_policy: "triggered" }]
            : []
        });
      },
      "POST /api/team-runs": { team_run: { id: "run-1", goal: "", team_name: "Release Crew", status: "draft", run_mode: "plan_and_execute", lifecycle_mode: "continuous", execution_policy: "triggered" } },
      "GET /api/team-runs/run-1": {
        team_run: {
          id: "run-1",
          goal: "",
          team_name: "Release Crew",
          status: "running",
          run_mode: "plan_and_execute",
          lifecycle_mode: "continuous",
          execution_policy: "triggered",
          leader_agent_id: "a1",
          max_workers: 1
        }
      },
      "GET /api/team-runs/run-1/agents": {
        agents: [{
          id: "a1",
          team_run_id: "run-1",
          name: "Tech Lead",
          role: "leader",
          persona_snapshot: { role: "Planning" },
          backend: "codex",
          model: "default",
          status: "running",
          current_task_id: null
        }]
      },
      "GET /api/team-runs/run-1/tasks": () => {
        taskCalls += 1;
        return response({ tasks: taskCalls > 1 ? [{ id: "t1", title: "Define schema", status: "in_progress" }] : [] });
      },
      "GET /api/team-runs/run-1/messages": { messages: [] }
    });

    render(<GatewayApp />);

    await userEvent.click(await screen.findByRole("button", { name: "Team Runs" }));
    await userEvent.click(await screen.findByRole("button", { name: /new team run/i }));
    expect(screen.queryByLabelText(/base objective/i)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Create team run" }));

    const createCall = fetch.mock.calls.find(([url, init]) => (
      url === "/api/team-runs" && init?.method === "POST"
    ));
    expect(JSON.parse(createCall[1].body)).toEqual({
      team_id: "t1",
      execution_policy: "triggered"
    });
    expect(fetch).not.toHaveBeenCalledWith(
      "/api/team-runs/run-1/start",
      expect.anything()
    );

    expect((await screen.findAllByText("Tech Lead")).length).toBeGreaterThan(0);
    expect(screen.getByText("LEAD")).toBeInTheDocument();
    expect(screen.queryByText("Define schema")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: /TASKS/ }));

    const source = MockEventSource.instances[0];
    source.emit({
      type: "team.task.updated",
      team_run_id: "run-1",
      session_id: "session-1",
      task_id: "t1",
      payload: { item: { type: "agent_message", text: "should not enter chat" } }
    });

    expect(await screen.findByText("Define schema")).toBeInTheDocument();

    const selectedDetailCalls = taskCalls;
    act(() => {
      source.emit({
        stream_id: "boot-a",
        id: 2,
        type: "team.cycle.settled",
        team_run_id: "another-run"
      });
    });
    await waitFor(() => expect(teamRunsCalls).toBeGreaterThan(2));
    expect(taskCalls).toBe(selectedDetailCalls);

    const refreshEvents = [
      "team.cycle_request.queued",
      "team.cycle.started",
      "team.cycle.settled",
      "team.auto_series.paused",
      "team.auto_series.completed"
    ];
    const listCallsBeforeCycles = teamRunsCalls;
    act(() => {
      refreshEvents.forEach((type, index) => source.emit({
        stream_id: "boot-a",
        id: 10 + index,
        type,
        team_run_id: "run-1"
      }));
    });
    await waitFor(() => {
      expect(teamRunsCalls).toBeGreaterThanOrEqual(listCallsBeforeCycles + refreshEvents.length);
      expect(taskCalls).toBeGreaterThanOrEqual(selectedDetailCalls + refreshEvents.length);
    });

    act(() => {
      source.emit({
        stream_id: "boot-a",
        id: 20,
        type: "team.run.completed",
        team_run_id: "run-1"
      });
    });
    await waitFor(() => expect(teamRunsCalls).toBeGreaterThan(2));
    await userEvent.click(screen.getByText("← TEAM RUNS"));
    expect(await screen.findByText(/Release Crew · run-1/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Chat" }));
    expect(screen.queryByText("should not enter chat")).not.toBeInTheDocument();
  });

  it("creates an AUTO Team Run with its default numeric policy settings", async () => {
    const continuousRun = {
      id: "mail-run",
      goal: "Watch inbox",
      status: "draft",
      run_mode: "plan_and_execute",
      lifecycle_mode: "continuous",
      execution_policy: "auto",
      max_workers: 1
    };
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/teams": {
        teams: [{ id: "t1", name: "Mail Crew", leader: { name: "Mail Lead", avatar: null }, members: [] }]
      },
      "GET /api/team-runs": { team_runs: [continuousRun] },
      "POST /api/team-runs": { team_run: continuousRun },
      "GET /api/team-runs/mail-run/detail": {
        team_run: continuousRun,
        agents: [], tasks: [], messages: [], cycles: []
      },
      "GET /api/team-runs/mail-run/documents": { documents: [] },
      "GET /api/team-runs/mail-run/delivery": { delivery: { available: false } }
    });

    render(<UiProvider><GatewayApp /></UiProvider>);
    await userEvent.click(await screen.findByRole("button", { name: "Team Runs" }));
    await userEvent.click(await screen.findByRole("button", { name: /new team run/i }));
    await userEvent.click(screen.getByRole("button", { name: "AUTO" }));
    await userEvent.type(await screen.findByLabelText("Base objective"), "Watch inbox");
    await userEvent.click(screen.getByRole("button", { name: "Create team run" }));

    expect(await screen.findByText("AUTO Team Run started")).toBeInTheDocument();
    const createCall = fetch.mock.calls.find(([url, init]) => (
      url === "/api/team-runs" && init?.method === "POST"
    ));
    expect(createCall).toBeDefined();
    expect(JSON.parse(createCall[1].body)).toEqual({
      team_id: "t1",
      goal: "Watch inbox",
      execution_policy: "auto",
      auto_repeat_count: 3,
      auto_interval_minutes: 5
    });
    expect(fetch).not.toHaveBeenCalledWith(
      "/api/team-runs/mail-run/start",
      expect.anything()
    );
  });

  it("wires cycle policy actions through the controller and refreshes detail and list", async () => {
    let detailCalls = 0;
    let listCalls = 0;
    let triggerCalls = 0;
    vi.stubGlobal("crypto", { randomUUID: vi.fn(() => "ui-1") });
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/personas": { personas: [] },
      "GET /api/team-runs": () => {
        listCalls += 1;
        return response({ team_runs: [{
          id: "run 1", goal: "Maintain", status: "draft",
          lifecycle_mode: "continuous", run_mode: "plan_and_execute",
          execution_policy: "auto"
        }] });
      },
      "GET /api/team-runs/run%201/detail": () => {
        detailCalls += 1;
        return response({
          team_run: {
            id: "run 1", goal: "Maintain", status: "draft",
            lifecycle_mode: "continuous", run_mode: "plan_and_execute",
            execution_policy: "auto"
          },
          agents: [], tasks: [], messages: [], cycles: [],
          active_auto_series: { id: "series 1" }
        });
      },
      "GET /api/team-runs/run%201/documents": { documents: [] },
      "GET /api/team-runs/run%201/delivery": { delivery: { available: false } },
      "POST /api/team-runs/run%201/cycle-requests": () => {
        triggerCalls += 1;
        return triggerCalls === 1
          ? response({ cycle_request: { id: "q1" } })
          : response({}, false);
      },
      "POST /api/team-runs/run%201/auto-series/series%201/retry": { cycle_request: { id: "q2" } },
      "POST /api/team-runs/run%201/auto-series/series%201/continue": { auto_series: { id: "series 1" } },
      "POST /api/team-runs/run%201/auto-series/restart": { auto_series: { id: "series 2" } }
    });

    render(<UiProvider><GatewayApp /></UiProvider>);
    await userEvent.click(await screen.findByRole("button", { name: "Team Runs" }));
    await userEvent.click(await screen.findByRole("button", { name: "Open team run Maintain" }));
    await waitFor(() => expect(teamRunDetailCapture.props?.detail?.run?.id).toBe("run 1"));
    await waitFor(() => expect(teamRunDetailCapture.props?.delivery).toEqual({ available: false }));
    expect(teamRunDetailCapture.props.onRefreshDelivery).toEqual(expect.any(Function));
    expect(teamRunDetailCapture.props.onCommitDelivery).toEqual(expect.any(Function));
    expect(teamRunDetailCapture.props.onApplyDelivery).toEqual(expect.any(Function));

    let accepted;
    await act(async () => {
      accepted = await teamRunDetailCapture.props.onTriggerCycle({
        instruction: "next",
        previous_cycle_id: "cycle-7"
      });
    });
    expect(accepted).toBe(true);
    expect(fetch).toHaveBeenCalledWith(
      "/api/team-runs/run%201/cycle-requests",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          instruction: "next",
          previous_cycle_id: "cycle-7",
          client_request_id: "ui-1"
        })
      })
    );

    await act(async () => {
      expect(await teamRunDetailCapture.props.onRetryAuto("series 1")).toBe(true);
      expect(await teamRunDetailCapture.props.onContinueAuto("series 1")).toBe(true);
      expect(await teamRunDetailCapture.props.onRestartAuto()).toBe(true);
    });
    expect(await teamRunDetailCapture.props.onRetryAuto("")).toBe(false);
    expect(detailCalls).toBeGreaterThanOrEqual(5);
    expect(listCalls).toBeGreaterThanOrEqual(5);

    await act(async () => {
      accepted = await teamRunDetailCapture.props.onTriggerCycle({ instruction: "fail" });
    });
    expect(accepted).toBe(false);
    expect(await screen.findByText("Failed to trigger cycle")).toBeInTheDocument();
  });

  it("shows Team Run loading until the selected detail response matches", async () => {
    const pendingDetail = deferredResponse();
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/personas": { personas: [] },
      "GET /api/teams": { teams: [] },
      "GET /api/settings": { settings: {} },
      "GET /api/team-runs": {
        team_runs: [{ id: "run-1", goal: "Slow run", status: "running" }]
      },
      "GET /api/team-runs/run-1/detail": () => pendingDetail.promise,
      "GET /api/team-runs/run-1/documents": { documents: [] },
      "GET /api/team-runs/run-1/delivery": { delivery: { available: false } }
    });

    render(<UiProvider><GatewayApp /></UiProvider>);
    await userEvent.click(await screen.findByRole("button", { name: "Team Runs" }));
    await userEvent.click(await screen.findByRole("button", { name: "Open team run Slow run" }));

    expect(await screen.findByRole("status")).toHaveTextContent("LOADING TEAM RUN...");
    expect(teamRunDetailCapture.props.detail).toBeNull();
    expect(teamRunDetailCapture.props.documents).toEqual([]);

    await act(async () => {
      pendingDetail.resolve({
        team_run: { id: "run-1", goal: "Slow run", status: "running" },
        agents: [], tasks: [], messages: [], cycles: []
      });
      await pendingDetail.promise;
    });
    expect(await screen.findByText("Slow run")).toBeInTheDocument();
    expect(teamRunDetailCapture.props.loading).toBe(false);
  });

  it("shows an error and keeps the app usable when creating a team run fails", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/teams": {
        teams: [{ id: "t1", name: "Release Crew", leader: { name: "Tech Lead", avatar: null }, members: [] }]
      },
      "GET /api/team-runs": { team_runs: [] },
      "POST /api/team-runs": response({}, false)
    });

    render(<UiProvider><GatewayApp /></UiProvider>);

    await userEvent.click(await screen.findByRole("button", { name: "Team Runs" }));
    await userEvent.click(await screen.findByRole("button", { name: /new team run/i }));
    await userEvent.click(screen.getByRole("button", { name: "Create team run" }));

    expect(await screen.findByText("Failed to create team run")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create team run" })).toBeInTheDocument();
  });

  it("filters the team-run list by Cycle-aware display status", async () => {
    const runs = [
      { id: "run-active", goal: "Active objective", current_objective: "Active objective", status: "running", display_status: "active", run_mode: "plan_and_execute", leader_name: "Tech Lead", members: [], task_counts: {}, task_done: 0, task_total: 1, team_id: "t1" },
      { id: "run-ready", goal: "", current_objective: "Ready objective", status: "completed", display_status: "ready", run_mode: "plan_and_execute", leader_name: "Tech Lead", members: [], task_counts: {}, task_done: 1, task_total: 1, team_id: "t1" },
      { id: "run-waiting", goal: "Auto objective", current_objective: "Auto objective", status: "completed", display_status: "auto_waiting", run_mode: "plan_and_execute", leader_name: "Tech Lead", members: [], task_counts: {}, task_done: 1, task_total: 1, team_id: "t1" },
      { id: "run-attention", goal: "Fix objective", current_objective: "Fix objective", status: "failed", display_status: "needs_attention", run_mode: "plan_and_execute", leader_name: "Tech Lead", members: [], task_counts: {}, task_done: 0, task_total: 1, team_id: "t1" },
      { id: "run-canceled", goal: "Canceled objective", current_objective: "Canceled objective", status: "canceled", display_status: "canceled", run_mode: "plan_and_execute", leader_name: "Tech Lead", members: [], task_counts: {}, task_done: 0, task_total: 1, team_id: "t1" }
    ];
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/personas": { personas: [] },
      "GET /api/team-runs": { team_runs: runs }
    });

    render(<GatewayApp />);
    await userEvent.click(await screen.findByRole("button", { name: "Team Runs" }));
    await screen.findByText("Active objective");

    await userEvent.click(screen.getByRole("button", { name: "Active" }));
    expect(screen.getByText("Active objective")).toBeInTheDocument();
    expect(screen.queryByText("Ready objective")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Ready" }));
    expect(screen.getByText("Ready objective")).toBeInTheDocument();
    expect(screen.queryByText("Active objective")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Auto waiting" }));
    expect(screen.getByText("Auto objective")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Needs attention" }));
    expect(screen.getByText("Fix objective")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "All" }));
    expect(screen.getByText("Active objective")).toBeInTheDocument();
    expect(screen.getByText("Ready objective")).toBeInTheDocument();
    expect(screen.getByText("Auto objective")).toBeInTheDocument();
    expect(screen.getByText("Fix objective")).toBeInTheDocument();
    expect(screen.getByText("Canceled objective")).toBeInTheDocument();
  });

  it("loads the hooks screen and lists hooks from the API", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/hooks": { hooks: [{
        id: "hook-1",
        name: "Invoice Watcher",
        enabled: true,
        target_backend: "codex",
        target_model: "default",
        prompt_template: "Summarize {{subject}}",
        filter: { folder: "INBOX" },
        last_polled_at: null
      }] },
      "GET /api/team-runs": { team_runs: [{
        id: "mail-run",
        goal: "Mail inbox",
        status: "draft",
        run_mode: "plan_and_execute",
        lifecycle_mode: "continuous",
        execution_policy: "triggered"
      }] }
    });

    render(<GatewayApp />);

    await userEvent.click(await screen.findByRole("button", { name: "Hooks" }));
    expect(await screen.findByRole("heading", { name: "Hooks" })).toBeInTheDocument();
    expect(await screen.findByText("Invoice Watcher")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "CREATE NEW" }));
    await userEvent.click(screen.getByRole("button", { name: "TEAM RUN" }));
    expect(screen.getByRole("option", { name: "Mail inbox" })).toBeInTheDocument();
  });

  it("shows a success toast and increments the Hooks nav badge on hook.run.updated SSE", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/hooks": { hooks: [{
        id: "hook-1",
        name: "Invoice Watcher",
        enabled: true,
        target_backend: "codex",
        target_model: "default",
        prompt_template: "Summarize {{subject}}",
        filter: { folder: "INBOX" },
        last_polled_at: null
      }] }
    });

    render(<UiProvider><GatewayApp /></UiProvider>);

    await screen.findByLabelText("Agent Gateway");
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0));
    const source = MockEventSource.instances[0];

    act(() => {
      source.emit({ type: "hook.run.updated", hook_id: "hook-1", run_id: "run-1", status: "succeeded" });
    });

    const toastEl = await screen.findByRole("status");
    expect(toastEl).toHaveClass("toast-success");

    const hooksButton = screen.getByRole("button", { name: "Hooks" });
    expect(within(hooksButton).getByText("1")).toBeInTheDocument();
  });

  it("does not notify for replayed hook events after login", async () => {
    let hooksCalls = 0;
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/hooks": () => {
        hooksCalls += 1;
        return response({ hooks: [] });
      }
    });

    render(<UiProvider><GatewayApp /></UiProvider>);

    await screen.findByLabelText("Agent Gateway");
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0));
    const source = MockEventSource.instances[0];

    act(() => {
      source.emit({
        stream_id: "boot-a",
        id: 3,
        replayed: true,
        type: "hook.run.updated",
        hook_id: "hook-1",
        run_id: "run-1",
        status: "succeeded"
      });
    });

    await waitFor(() => expect(hooksCalls).toBe(1));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    const hooksButton = screen.getByRole("button", { name: "Hooks" });
    expect(within(hooksButton).queryByText("1")).not.toBeInTheDocument();
  });

  it("refreshes the Hook collection after a background hook event", async () => {
    let hooksCalls = 0;
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/team-runs": { team_runs: [] },
      "GET /api/hooks": () => {
        hooksCalls += 1;
        return response({ hooks: [{
          id: "hook-1",
          name: "Invoice Watcher",
          enabled: true,
          target_backend: "codex",
          target_model: "default",
          prompt_template: "Summarize {{subject}}",
          filter: { folder: "INBOX" },
          last_polled_at: hooksCalls > 1 ? "2026-07-20T00:00:00Z" : null,
          last_error: hooksCalls > 1 ? "Mailbox unavailable" : null
        }] });
      }
    });

    render(<UiProvider><GatewayApp /></UiProvider>);

    await userEvent.click(await screen.findByRole("button", { name: "Hooks" }));
    expect(await screen.findByText("Invoice Watcher")).toBeInTheDocument();
    const source = MockEventSource.instances[0];

    act(() => {
      source.emit({
        stream_id: "boot-a",
        id: 3,
        type: "hook.run.updated",
        hook_id: "hook-1",
        run_id: "run-1",
        status: "failed"
      });
    });

    expect(await screen.findByText("Mailbox unavailable")).toBeInTheDocument();
    expect(hooksCalls).toBeGreaterThan(1);
  });

  it("sends one private terminal notification after opt-in and opens the Team Run on click", async () => {
    const notifications = [];
    class FakeNotification {
      static permission = "default";
      static requestPermission = vi.fn(async () => {
        FakeNotification.permission = "granted";
        return "granted";
      });

      constructor(title, options) {
        this.title = title;
        this.options = options;
        this.close = vi.fn();
        notifications.push(this);
      }
    }
    vi.stubGlobal("Notification", FakeNotification);
    const focus = vi.spyOn(window, "focus").mockImplementation(() => {});
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/settings": { settings: { access_mode: "restricted", agent_availability: [] } },
      "GET /api/auth/sessions": { sessions: [] },
      "GET /api/team-runs": { team_runs: [{ id: "run-private", goal: "Private goal", status: "failed" }] },
      "GET /api/teams": { teams: [] },
      "GET /api/team-runs/run-private/detail": {
        team_run: { id: "run-private", goal: "Private goal", status: "failed", run_mode: "plan_and_execute" },
        agents: [], tasks: [], messages: [], document_summary: null
      },
      "GET /api/team-runs/run-private/documents": { documents: [] },
      "GET /api/team-runs/run-private/delivery": { delivery: { available: false } }
    });

    render(<GatewayApp />);
    await screen.findByLabelText("Agent Gateway");
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0));
    const source = MockEventSource.instances[0];

    act(() => {
      source.emit({ id: "before-opt-in", type: "team.run.failed", team_run_id: "run-private" });
    });
    expect(notifications).toHaveLength(0);

    await userEvent.click(screen.getByRole("button", { name: "Settings" }));
    await userEvent.click(await screen.findByRole("button", { name: /enable notifications/i }));
    expect(FakeNotification.requestPermission).toHaveBeenCalledTimes(1);

    act(() => {
      source.emit({
        id: "terminal-1",
        type: "team.run.failed",
        team_run_id: "run-private",
        prompt: "private prompt",
        run: {
          status: "failed",
          finished_at: "2026-07-16T00:00:00Z",
          summary: "secret summary",
          error_message: "C:/secret/path"
        }
      });
      source.emit({
        id: "terminal-2",
        type: "team.run.failed",
        team_run_id: "run-private",
        run: { status: "failed", finished_at: "2026-07-16T00:00:00Z" }
      });
    });

    expect(notifications).toHaveLength(1);
    expect(JSON.stringify([notifications[0].title, notifications[0].options.body])).not.toMatch(/secret|private|C:\//i);

    act(() => notifications[0].onclick());
    expect(focus).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("← TEAM RUNS")).toBeInTheDocument();
  });

  it("clears the selected team run detail when navigating away from the teams screen", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/personas": { personas: [{ id: "p1", name: "Tech Lead", role: "Planning" }] },
      "GET /api/team-runs": { team_runs: [{ id: "run-1", goal: "Ship it", status: "running" }] },
      "GET /api/team-runs/run-1": {
        team_run: { id: "run-1", goal: "Ship it", status: "running", run_mode: "planning_only" }
      },
      "GET /api/team-runs/run-1/agents": { agents: [] },
      "GET /api/team-runs/run-1/tasks": { tasks: [] },
      "GET /api/team-runs/run-1/messages": { messages: [] }
    });

    render(<GatewayApp />);

    await userEvent.click(await screen.findByRole("button", { name: "Team Runs" }));
    await userEvent.click(await screen.findByText("Ship it"));
    expect(await screen.findByText("← TEAM RUNS")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Chat" }));
    await userEvent.click(await screen.findByRole("button", { name: "Team Runs" }));

    expect(await screen.findByRole("heading", { name: "Team Runs" })).toBeInTheDocument();
    expect(screen.queryByText("← TEAM RUNS")).not.toBeInTheDocument();
  });
});
