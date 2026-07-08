import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { GatewayApp } from "./index.jsx";

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
      "GET /api/history": { events: [] }
    });

    render(<GatewayApp />);

    expect(await screen.findByLabelText("Agent Gateway")).toBeInTheDocument();
    expect(screen.getAllByText("Main chat").length).toBeGreaterThan(0);
    expect(screen.getByText("AGENT IDLE")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Jobs" }));
    expect(screen.getByText("JOBS - PLANNED")).toBeInTheDocument();
  });

  it("supports OTP login before loading protected API data", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: false, totp_configured: true },
      "POST /api/auth/login": {},
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] }
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
      "POST /api/chat": { messages: [{ content: "Fallback answer" }], pending_approval: null },
      "GET /api/artifacts": { artifacts: [] }
    });

    render(<GatewayApp />);

    const input = await screen.findByPlaceholderText("Message the agent, or describe a local action...");
    await userEvent.type(input, "hello");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("hello")).toBeInTheDocument();
    expect(await screen.findByText("Fallback answer")).toBeInTheDocument();
  });

  it("searches and activates sessions from the session rail", async () => {
    installFetch({
      "GET /api/auth/status": { authenticated: true, totp_configured: true },
      "GET /api/status": status,
      "GET /api/sessions": { sessions },
      "GET /api/history": { events: [] },
      "GET /api/sessions/search?q=old": { sessions: [{ ...sessions[0], id: "session-2", title: "Old chat", is_active: false }] },
      "POST /api/sessions/session-2/activate": {
        session_id: "session-2",
        events: [{ kind: "user", created_at: "2026-07-08T01:02:00Z", payload: { content: "previous" } }]
      }
    });

    render(<GatewayApp />);

    const rail = await screen.findByLabelText("Sessions");
    await userEvent.type(within(rail).getByPlaceholderText("Search"), "old");
    await userEvent.click(await screen.findByText("Old chat"));

    expect(await screen.findByText("previous")).toBeInTheDocument();
  });
});
