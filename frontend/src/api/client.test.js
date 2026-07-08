import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./client.js";

function jsonResponse(body, ok = true) {
  return Promise.resolve({ ok, json: () => Promise.resolve(body) });
}

describe("api client", () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });

  it("keeps existing auth and chat endpoints", async () => {
    fetch
      .mockResolvedValueOnce({ ok: true })
      .mockResolvedValueOnce(jsonResponse({ messages: [], pending_approval: null }));

    expect(await api.login("123456")).toBe(true);
    await api.sendChat("hello");

    expect(fetch).toHaveBeenNthCalledWith(1, "/api/auth/login", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ otp: "123456" })
    }));
    expect(fetch).toHaveBeenNthCalledWith(2, "/api/chat", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ message: "hello" })
    }));
  });

  it("normalizes list responses used by the UI", async () => {
    fetch
      .mockResolvedValueOnce(jsonResponse({ sessions: [{ id: "s1" }] }))
      .mockResolvedValueOnce(jsonResponse({ events: [{ kind: "user" }] }))
      .mockResolvedValueOnce(jsonResponse({ artifacts: [{ id: "a1" }] }));

    await expect(api.sessions()).resolves.toEqual([{ id: "s1" }]);
    await expect(api.history()).resolves.toEqual([{ kind: "user" }]);
    await expect(api.artifacts()).resolves.toEqual([{ id: "a1" }]);
  });

  it("supports agent registry and active session config endpoints", async () => {
    fetch
      .mockResolvedValueOnce(jsonResponse({ agents: [{ id: "codex" }] }))
      .mockResolvedValueOnce(jsonResponse({ config: { agent_id: "codex", model: "default" } }))
      .mockResolvedValueOnce(jsonResponse({ config: { agent_id: "claude", model: "sonnet" } }));

    await expect(api.agents()).resolves.toEqual([{ id: "codex" }]);
    await expect(api.activeSessionConfig()).resolves.toEqual({ agent_id: "codex", model: "default" });
    await api.updateActiveSessionConfig({ agent_id: "claude", model: "sonnet", options: {} });

    expect(fetch).toHaveBeenNthCalledWith(1, "/api/agents");
    expect(fetch).toHaveBeenNthCalledWith(2, "/api/sessions/active/config");
    expect(fetch).toHaveBeenNthCalledWith(3, "/api/sessions/active/config", expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ agent_id: "claude", model: "sonnet", options: {} })
    }));
  });
});
