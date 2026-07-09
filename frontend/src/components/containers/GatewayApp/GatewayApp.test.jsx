import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { GatewayApp } from "./index.jsx";
import { UiProvider } from "../../providers/UiProvider/index.jsx";

function response(body, ok = true) {
  return Promise.resolve({ ok, json: () => Promise.resolve(body) });
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
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

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
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
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

  it("loads editable agent config for an empty session and saves changes", async () => {
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
      "PUT /api/sessions/active/config": { config: { agent_id: "claude", model: "sonnet", options: { effort: "medium" }, editable: true } }
    });

    render(<GatewayApp />);

    await userEvent.click(await screen.findByRole("button", { name: "Agent" }));
    await userEvent.click(await screen.findByRole("button", { name: /Claude Code/ }));

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/sessions/active/config",
      expect.objectContaining({ method: "PUT" })
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
      "PUT /api/sessions/active/config": response({}, false),
      "POST /api/sessions/session-2/activate": {
        session_id: "session-2",
        events: []
      }
    });

    render(<GatewayApp />);

    await userEvent.click(await screen.findByRole("button", { name: "Agent" }));
    await userEvent.click(await screen.findByRole("button", { name: /Claude Code/ }));
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

    expect((await screen.findAllByText(/LOCKED/)).length).toBeGreaterThan(0);
    expect(screen.getByText(/Claude Code/)).toBeInTheDocument();
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

  it("creates and starts a team run, shows its detail, and refreshes on mixed team SSE events without adding chat entries", async () => {
    let taskCalls = 0;
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/personas": { personas: [{ id: "p1", name: "Tech Lead", role: "Planning" }] },
      "GET /api/team-runs": { team_runs: [] },
      "POST /api/team-runs": { team_run: { id: "run-1", goal: "Ship it", status: "draft", run_mode: "planning_only" } },
      "POST /api/team-runs/run-1/start": {
        team_run: { id: "run-1", goal: "Ship it", status: "running", run_mode: "planning_only" }
      },
      "GET /api/team-runs/run-1": {
        team_run: {
          id: "run-1",
          goal: "Ship it",
          status: "running",
          run_mode: "planning_only",
          leader_agent_id: "a1",
          max_workers: 3
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
    await userEvent.type(await screen.findByLabelText("Goal"), "Ship it");
    await userEvent.click(screen.getByRole("button", { name: /select tech lead as leader/i }));
    await userEvent.click(screen.getByRole("button", { name: /start team run/i }));

    expect((await screen.findAllByText("Tech Lead")).length).toBeGreaterThan(0);
    expect(screen.getByText("LEAD")).toBeInTheDocument();
    expect(screen.queryByText("Define schema")).not.toBeInTheDocument();

    const source = MockEventSource.instances[0];
    source.emit({
      type: "team.task.updated",
      team_run_id: "run-1",
      session_id: "session-1",
      task_id: "t1",
      payload: { item: { type: "agent_message", text: "should not enter chat" } }
    });

    expect(await screen.findByText("Define schema")).toBeInTheDocument();

    await userEvent.click(screen.getByText("← TEAM RUNS"));
    expect(screen.queryByText("Ship it")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Chat" }));
    expect(screen.queryByText("should not enter chat")).not.toBeInTheDocument();
  });

  it("shows an error and keeps the app usable when creating a team run fails", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/agents": { agents: [] },
      "GET /api/sessions/active/config": { config: null },
      "GET /api/personas": { personas: [{ id: "p1", name: "Tech Lead", role: "Planning" }] },
      "GET /api/team-runs": { team_runs: [] },
      "POST /api/team-runs": response({}, false)
    });

    render(<UiProvider><GatewayApp /></UiProvider>);

    await userEvent.click(await screen.findByRole("button", { name: "Team Runs" }));
    await userEvent.click(await screen.findByRole("button", { name: /new team run/i }));
    await userEvent.type(await screen.findByLabelText("Goal"), "Ship it");
    await userEvent.click(screen.getByRole("button", { name: /select tech lead as leader/i }));
    await userEvent.click(screen.getByRole("button", { name: /start team run/i }));

    expect(await screen.findByText("Failed to create team run")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /start team run/i })).toBeInTheDocument();
  });

  it("deletes a team run from the home list after confirmation", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    let listCalls = 0;
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
        return response({ team_runs: listCalls > 1 ? [] : [{ id: "run-1", goal: "Ship it", status: "running" }] });
      },
      "DELETE /api/team-runs/run-1": { deleted: true }
    });

    render(<GatewayApp />);

    await userEvent.click(await screen.findByRole("button", { name: "Team Runs" }));
    expect(await screen.findByText("Ship it")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /delete team run ship it/i }));

    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/team-runs/run-1", expect.objectContaining({ method: "DELETE" })));
    await waitFor(() => expect(screen.queryByText("Ship it")).not.toBeInTheDocument());
    window.confirm.mockRestore();
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
