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

  it("adds work and combines the four team-run detail responses", async () => {
    fetch
      .mockResolvedValueOnce(jsonResponse({ team_run: { id: "r1", status: "running" } }))
      .mockResolvedValueOnce(jsonResponse({ team_run: { id: "r1", goal: "Ship" } }))
      .mockResolvedValueOnce(jsonResponse({ agents: [{ id: "a1" }] }))
      .mockResolvedValueOnce(jsonResponse({ tasks: [{ id: "t1" }] }))
      .mockResolvedValueOnce(jsonResponse({ messages: [{ id: "m1" }] }));

    await expect(api.addWork("r1", "write docs")).resolves.toEqual({ team_run: { id: "r1", status: "running" } });
    await expect(api.teamRunDetail("r1")).resolves.toEqual({
      run: { id: "r1", goal: "Ship" },
      agents: [{ id: "a1" }],
      tasks: [{ id: "t1" }],
      messages: [{ id: "m1" }]
    });

    expect(fetch).toHaveBeenNthCalledWith(1, "/api/team-runs/r1/add-work", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ instruction: "write docs" })
    }));
    expect(fetch).toHaveBeenCalledWith("/api/team-runs/r1");
    expect(fetch).toHaveBeenCalledWith("/api/team-runs/r1/agents");
    expect(fetch).toHaveBeenCalledWith("/api/team-runs/r1/tasks");
    expect(fetch).toHaveBeenCalledWith("/api/team-runs/r1/messages");
  });

  it("returns null when adding work fails", async () => {
    fetch.mockResolvedValueOnce(jsonResponse({}, false));
    await expect(api.addWork("r1", "write docs")).resolves.toBeNull();
  });

  it("resumes an interrupted team run", async () => {
    fetch.mockResolvedValueOnce(jsonResponse({ team_run: { id: "r1", status: "interrupted" } }));

    await expect(api.resumeTeamRun("r1")).resolves.toEqual({ id: "r1", status: "interrupted" });
    expect(fetch).toHaveBeenCalledWith(
      "/api/team-runs/r1/resume",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("queues a failed team task for retry", async () => {
    fetch.mockResolvedValueOnce(jsonResponse({
      team_run: { id: "r1", status: "interrupted" },
      task: { id: "t1", status: "pending" }
    }));

    await expect(api.retryTeamTask("r1", "t1")).resolves.toEqual({
      run: { id: "r1", status: "interrupted" },
      task: { id: "t1", status: "pending" }
    });
    expect(fetch).toHaveBeenCalledWith(
      "/api/team-runs/r1/tasks/t1/retry",
      expect.objectContaining({ method: "POST" })
    );
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

    expect(fetch).toHaveBeenNthCalledWith(1, "/api/sessions/session-1/history");
    expect(fetch).toHaveBeenNthCalledWith(2, "/api/sessions/session-1/activity");
    expect(fetch).toHaveBeenNthCalledWith(3, "/api/sessions/session-1/status");
    expect(fetch).toHaveBeenCalledWith("/api/sessions/session-1/chat", expect.objectContaining({ method: "POST" }));
    expect(fetch).toHaveBeenCalledWith("/api/sessions/session-1/approvals/approval-1/approve", expect.objectContaining({ method: "POST" }));
    expect(fetch).toHaveBeenCalledWith("/api/sessions/session-1/approvals/approval-1/deny", expect.objectContaining({ method: "POST" }));
  });

  it("interruptSession posts to the interrupt endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ interrupting: true }) });
    global.fetch = fetchMock;
    const result = await api.interruptSession("sess 1");
    expect(fetchMock).toHaveBeenCalledWith("/api/sessions/sess%201/interrupt", { method: "POST" });
    expect(result).toEqual({ interrupting: true });
  });

  it("teams() returns the teams array", async () => {
    fetch.mockResolvedValueOnce(jsonResponse({ teams: [{ id: "t1", name: "Release Crew" }] }));
    await expect(api.teams()).resolves.toEqual([{ id: "t1", name: "Release Crew" }]);
    expect(fetch).toHaveBeenCalledWith("/api/teams");
  });

  it("creates, updates, and deletes a team", async () => {
    fetch
      .mockResolvedValueOnce(jsonResponse({ team: { id: "t1", name: "Release Crew" } }))
      .mockResolvedValueOnce(jsonResponse({ team: { id: "t1", name: "Renamed" } }))
      .mockResolvedValueOnce({ ok: true });

    await expect(api.createTeam({ name: "Release Crew" })).resolves.toEqual({ id: "t1", name: "Release Crew" });
    await expect(api.updateTeam("t1", { name: "Renamed" })).resolves.toEqual({ id: "t1", name: "Renamed" });
    await expect(api.deleteTeam("t1")).resolves.toBe(true);

    expect(fetch).toHaveBeenNthCalledWith(1, "/api/teams", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ name: "Release Crew" })
    }));
    expect(fetch).toHaveBeenNthCalledWith(2, "/api/teams/t1", expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ name: "Renamed" })
    }));
    expect(fetch).toHaveBeenNthCalledWith(3, "/api/teams/t1", { method: "DELETE" });
  });

  it("reads and updates global, persona-baseline, and team rule sets", async () => {
    fetch
      .mockResolvedValueOnce(jsonResponse({ global: { rules: [] }, persona_baseline: { rules: [] } }))
      .mockResolvedValueOnce(jsonResponse({ rule_set: { rules: ["global-1"] } }))
      .mockResolvedValueOnce(jsonResponse({ rule_set: { rules: ["baseline-1"] } }))
      .mockResolvedValueOnce(jsonResponse({ rule_set: { rules: ["team-1"] } }));

    await expect(api.rules()).resolves.toEqual({ global: { rules: [] }, persona_baseline: { rules: [] } });
    await expect(api.updateGlobalRules({ rules: ["global-1"] })).resolves.toEqual({ rules: ["global-1"] });
    await expect(api.updatePersonaBaselineRules({ rules: ["baseline-1"] })).resolves.toEqual({ rules: ["baseline-1"] });
    await expect(api.updateTeamRules("t1", { rules: ["team-1"] })).resolves.toEqual({ rules: ["team-1"] });

    expect(fetch).toHaveBeenNthCalledWith(1, "/api/rules");
    expect(fetch).toHaveBeenNthCalledWith(2, "/api/rules/global", expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ rules: ["global-1"] })
    }));
    expect(fetch).toHaveBeenNthCalledWith(3, "/api/rules/persona-baseline", expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ rules: ["baseline-1"] })
    }));
    expect(fetch).toHaveBeenNthCalledWith(4, "/api/teams/t1/rules", expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ rules: ["team-1"] })
    }));
  });

  it("teamDocuments() returns the documents array and teamDocumentContent() fetches by path", async () => {
    fetch
      .mockResolvedValueOnce(jsonResponse({ documents: [{ path: "notes.md", kind: "md", previewable: true }] }))
      .mockResolvedValueOnce(jsonResponse({ path: "notes.md", content: "# Notes" }));

    await expect(api.teamDocuments("run-1")).resolves.toEqual([{ path: "notes.md", kind: "md", previewable: true }]);
    await expect(api.teamDocumentContent("run-1", "notes.md")).resolves.toEqual({ path: "notes.md", content: "# Notes" });

    expect(fetch).toHaveBeenNthCalledWith(1, "/api/team-runs/run-1/documents");
    expect(fetch).toHaveBeenNthCalledWith(2, "/api/team-runs/run-1/documents/content?path=notes.md");
  });
});
