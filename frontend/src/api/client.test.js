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

  it("supports persona and team-run endpoints", async () => {
    fetch
      .mockResolvedValueOnce(jsonResponse({ personas: [{ id: "p1", name: "Tech Lead" }] }))
      .mockResolvedValueOnce(jsonResponse({ persona: { id: "p2", name: "QA Tester" } }))
      .mockResolvedValueOnce(jsonResponse({ team_runs: [{ id: "r1", goal: "Ship" }] }))
      .mockResolvedValueOnce(jsonResponse({ team_run: { id: "r2", goal: "Design" } }))
      .mockResolvedValueOnce(jsonResponse({ team_run: { id: "r2", status: "planning" } }));

    await expect(api.personas()).resolves.toEqual([{ id: "p1", name: "Tech Lead" }]);
    await expect(api.createPersona({ name: "QA Tester" })).resolves.toEqual({ id: "p2", name: "QA Tester" });
    await expect(api.teamRuns()).resolves.toEqual([{ id: "r1", goal: "Ship" }]);
    await api.createTeamRun({ goal: "Design", leader_persona_id: "p1" });
    await api.startTeamRun("r2");

    expect(fetch).toHaveBeenNthCalledWith(1, "/api/personas");
    expect(fetch).toHaveBeenNthCalledWith(4, "/api/team-runs", expect.objectContaining({ method: "POST" }));
    expect(fetch).toHaveBeenNthCalledWith(5, "/api/team-runs/r2/start", expect.objectContaining({ method: "POST" }));
  });

  it("calls session-explicit chat APIs", async () => {
    fetch
      .mockResolvedValueOnce(jsonResponse({ events: [{ kind: "user" }] }))
      .mockResolvedValueOnce(jsonResponse({ events: [{ type: "runtime.completed" }] }))
      .mockResolvedValueOnce(jsonResponse({ status: "idle" }))
      .mockResolvedValueOnce(jsonResponse({ messages: [] }))
      .mockResolvedValueOnce(jsonResponse({ messages: [] }))
      .mockResolvedValueOnce(jsonResponse({ messages: [] }));

    expect(await api.sessionHistory("session-1")).toEqual([{ kind: "user" }]);
    expect(await api.sessionActivity("session-1")).toEqual([{ type: "runtime.completed" }]);
    expect(await api.sessionStatus("session-1")).toEqual({ status: "idle" });
    await api.sendSessionChat("session-1", "hello");
    await api.approveSession("session-1", "approval-1");
    await api.denySession("session-1", "approval-1");

    expect(fetch).toHaveBeenCalledWith("/api/sessions/session-1/chat", expect.objectContaining({ method: "POST" }));
    expect(fetch).toHaveBeenCalledWith("/api/sessions/session-1/approvals/approval-1/approve", expect.objectContaining({ method: "POST" }));
    expect(fetch).toHaveBeenCalledWith("/api/sessions/session-1/approvals/approval-1/deny", expect.objectContaining({ method: "POST" }));
  });
});
