import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, apiErrorAction } from "./client.js";

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

  it("supports job retry and schedule detail endpoints", async () => {
    fetch
      .mockResolvedValueOnce(jsonResponse({ job: { id: "j2", source_job_id: "j1" } }))
      .mockResolvedValueOnce(jsonResponse({ schedule: { id: "s1" }, jobs: [], next_runs: [] }));

    await expect(api.retryJob("job 1")).resolves.toEqual({ id: "j2", source_job_id: "j1" });
    await expect(api.scheduleDetail("schedule 1")).resolves.toEqual({
      schedule: { id: "s1" },
      jobs: [],
      next_runs: []
    });

    expect(fetch).toHaveBeenNthCalledWith(
      1,
      "/api/jobs/job%201/retry",
      expect.objectContaining({ method: "POST" })
    );
    expect(fetch).toHaveBeenNthCalledWith(2, "/api/schedules/schedule%201");
  });

  it("supports security settings and auth-session revocation", async () => {
    fetch
      .mockResolvedValueOnce(jsonResponse({ sessions: [{ id: "s1", current: true }] }))
      .mockResolvedValueOnce(jsonResponse({ access_mode: "full_access" }))
      .mockResolvedValueOnce(jsonResponse({ revoked: true, session_id: "s2" }))
      .mockResolvedValueOnce(jsonResponse({ revoked_count: 2 }));

    await expect(api.authSessions()).resolves.toEqual([{ id: "s1", current: true }]);
    await expect(api.setAccessMode("full_access", true)).resolves.toBe("full_access");
    await expect(api.revokeAuthSession("s2")).resolves.toEqual({ revoked: true, session_id: "s2" });
    await expect(api.revokeAllAuthSessions()).resolves.toEqual({ revoked_count: 2 });

    expect(fetch).toHaveBeenNthCalledWith(2, "/api/settings/access-mode", expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ mode: "full_access", confirm_full_access: true })
    }));
    expect(fetch).toHaveBeenNthCalledWith(3, "/api/auth/sessions/s2", { method: "DELETE" });
    expect(fetch).toHaveBeenNthCalledWith(4, "/api/auth/sessions/revoke-all", { method: "POST" });
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

  it("supports encoded manual trigger and AUTO policy action endpoints", async () => {
    fetch
      .mockResolvedValueOnce(jsonResponse({ cycle_request: { id: "q1" }, queue_position: 1 }))
      .mockResolvedValueOnce(jsonResponse({ cycle_request: { id: "q2" } }))
      .mockResolvedValueOnce(jsonResponse({ auto_series: { id: "series 1", status: "waiting_interval" } }))
      .mockResolvedValueOnce(jsonResponse({ auto_series: { id: "series 2" }, cycle_request: { id: "q3" } }));

    const triggerPayload = {
      instruction: "next",
      client_request_id: "ui-1",
      previous_cycle_id: "cycle-7"
    };
    await expect(api.triggerTeamCycle("run 1", triggerPayload)).resolves.toMatchObject({
      cycle_request: { id: "q1" },
      queue_position: 1
    });
    await expect(api.retryAutoCycle("run 1", "series 1")).resolves.toMatchObject({
      cycle_request: { id: "q2" }
    });
    await expect(api.continueAutoCycle("run 1", "series 1")).resolves.toMatchObject({
      auto_series: { id: "series 1", status: "waiting_interval" }
    });
    await expect(api.restartAutoSeries("run 1")).resolves.toMatchObject({
      auto_series: { id: "series 2" },
      cycle_request: { id: "q3" }
    });

    expect(fetch).toHaveBeenNthCalledWith(
      1,
      "/api/team-runs/run%201/cycle-requests",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(triggerPayload)
      })
    );
    expect(fetch).toHaveBeenNthCalledWith(
      2,
      "/api/team-runs/run%201/auto-series/series%201/retry",
      { method: "POST" }
    );
    expect(fetch).toHaveBeenNthCalledWith(
      3,
      "/api/team-runs/run%201/auto-series/series%201/continue",
      { method: "POST" }
    );
    expect(fetch).toHaveBeenNthCalledWith(
      4,
      "/api/team-runs/run%201/auto-series/restart",
      { method: "POST" }
    );
  });

  it("adds work and reads the aggregate team-run detail response", async () => {
    fetch
      .mockResolvedValueOnce(jsonResponse({ team_run: { id: "r1", status: "running" } }))
      .mockResolvedValueOnce(jsonResponse({
        team_run: { id: "r1", goal: "Ship" },
        agents: [{ id: "a1" }],
        tasks: [{ id: "t1" }],
        messages: [{ id: "m1" }],
        cycles: [{ id: "c1", sequence: 1 }],
        policy_status: "paused_failure",
        active_auto_series: { id: "s1", status: "paused_failure" },
        queue_count: 2,
        active_request: { id: "q1", status: "dispatching" },
        document_summary: { count: 2 }
      }));

    await expect(api.addWork("r1", "write docs")).resolves.toEqual({ team_run: { id: "r1", status: "running" } });
    await expect(api.teamRunDetail("r1")).resolves.toEqual({
      run: { id: "r1", goal: "Ship" },
      agents: [{ id: "a1" }],
      tasks: [{ id: "t1" }],
      messages: [{ id: "m1" }],
      cycles: [{ id: "c1", sequence: 1 }],
      decisionRequest: null,
      documentSummary: { count: 2 },
      policyStatus: "paused_failure",
      activeAutoSeries: { id: "s1", status: "paused_failure" },
      queueCount: 2,
      activeRequest: { id: "q1", status: "dispatching" }
    });

    expect(fetch).toHaveBeenNthCalledWith(1, "/api/team-runs/r1/add-work", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ instruction: "write docs" })
    }));
    expect(fetch).toHaveBeenCalledWith("/api/team-runs/r1/detail");
  });

  it("falls back to legacy team-run endpoints with an empty cycle list", async () => {
    fetch
      .mockResolvedValueOnce({
        ok: false,
        status: 404,
        statusText: "Not Found",
        headers: { get: () => null },
        json: async () => ({ detail: "Not Found" })
      })
      .mockResolvedValueOnce(jsonResponse({ team_run: { id: "r1", goal: "Ship" } }))
      .mockResolvedValueOnce(jsonResponse({ agents: [{ id: "a1" }] }))
      .mockResolvedValueOnce(jsonResponse({ tasks: [{ id: "t1" }] }))
      .mockResolvedValueOnce(jsonResponse({ messages: [{ id: "m1" }] }));

    await expect(api.teamRunDetail("r1")).resolves.toEqual({
      run: { id: "r1", goal: "Ship" },
      agents: [{ id: "a1" }],
      tasks: [{ id: "t1" }],
      messages: [{ id: "m1" }],
      cycles: [],
      decisionRequest: null,
      documentSummary: null,
      policyStatus: "ready",
      activeAutoSeries: null,
      queueCount: 0,
      activeRequest: null
    });
  });

  it("preserves non-2xx details and correlation IDs as ApiError", async () => {
    fetch.mockResolvedValueOnce({
      ok: false,
      status: 409,
      statusText: "Conflict",
      headers: { get: () => "corr-409" },
      json: async () => ({
        code: "run_conflict",
        detail: "Team run already running",
        retryable: false,
        correlation_id: "corr-body"
      })
    });

    let error;
    try {
      await api.addWork("r1", "write docs");
    } catch (caught) {
      error = caught;
    }

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 409,
      code: "run_conflict",
      detail: "Team run already running",
      retryable: false,
      correlationId: "corr-body"
    });
    expect(apiErrorAction(error)).toBe("refresh");
  });

  it.each([
    [400, "fix_input"],
    [401, "relogin"],
    [500, "retry"]
  ])("maps status %s to a distinct UI action", async (status, action) => {
    fetch.mockResolvedValueOnce({
      ok: false,
      status,
      statusText: "Error",
      headers: { get: () => null },
      json: async () => ({
        code: `http_${status}`,
        detail: `status ${status}`,
        retryable: status >= 500,
        correlation_id: `corr-${status}`
      })
    });

    await expect(api.jobs()).rejects.toMatchObject({ status, correlationId: `corr-${status}` });
    expect(apiErrorAction(new ApiError({ status, retryable: status >= 500 }))).toBe(action);
  });

  it("normalizes aborted requests as retryable timeout errors", async () => {
    fetch.mockRejectedValueOnce(new DOMException("timed out", "AbortError"));

    await expect(api.jobs()).rejects.toMatchObject({
      status: 0,
      code: "request_timeout",
      retryable: true
    });
  });

  it("resumes an interrupted team run", async () => {
    fetch.mockResolvedValueOnce(jsonResponse({ team_run: { id: "r1", status: "interrupted" } }));

    await expect(api.resumeTeamRun("r1")).resolves.toEqual({ id: "r1", status: "interrupted" });
    expect(fetch).toHaveBeenCalledWith(
      "/api/team-runs/r1/resume",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("cancels an active team run", async () => {
    fetch.mockResolvedValueOnce(jsonResponse({ team_run: { id: "r1", status: "canceled" } }));

    await expect(api.cancelTeamRun("r1")).resolves.toEqual({ id: "r1", status: "canceled" });
    expect(fetch).toHaveBeenCalledWith(
      "/api/team-runs/r1/cancel",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("submits a versioned team decision answer batch", async () => {
    fetch.mockResolvedValueOnce(jsonResponse({
      team_run: { id: "r1", status: "running" },
      decision_request: { id: "d1", status: "resolved" }
    }));

    await expect(api.answerTeamDecision("r1", "d1", 3, { "Q-001": "staging" }))
      .resolves.toEqual({
        run: { id: "r1", status: "running" },
        decisionRequest: { id: "d1", status: "resolved" }
      });
    expect(fetch).toHaveBeenCalledWith(
      "/api/team-runs/r1/decision-request/answer",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          request_id: "d1",
          revision: 3,
          answers: { "Q-001": "staging" }
        })
      })
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

  it("supports hook endpoints", async () => {
    fetch
      .mockResolvedValueOnce(jsonResponse({ hooks: [{ id: "h1" }] }))
      .mockResolvedValueOnce(jsonResponse({ hook: { id: "h1", name: "n" } }))
      .mockResolvedValueOnce(jsonResponse({ hook: { id: "h1", enabled: false } }))
      .mockResolvedValueOnce(jsonResponse({ created: 2 }))
      .mockResolvedValueOnce(jsonResponse({ runs: [{ id: "r1" }] }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));

    await expect(api.listHooks()).resolves.toEqual([{ id: "h1" }]);
    await expect(api.createHook({ name: "n" })).resolves.toEqual({ id: "h1", name: "n" });
    await expect(api.updateHook("h 1", { enabled: false })).resolves.toEqual({ id: "h1", enabled: false });
    await expect(api.runHookNow("h1")).resolves.toEqual({ created: 2 });
    await expect(api.listHookRuns("h1")).resolves.toEqual([{ id: "r1" }]);
    await expect(api.testHookConnection({ secret: "x" })).resolves.toEqual({ ok: true });

    expect(fetch).toHaveBeenNthCalledWith(1, "/api/hooks");
    expect(fetch).toHaveBeenNthCalledWith(2, "/api/hooks", expect.objectContaining({ method: "POST" }));
    expect(fetch).toHaveBeenNthCalledWith(3, "/api/hooks/h%201", expect.objectContaining({ method: "PATCH" }));
    expect(fetch).toHaveBeenNthCalledWith(4, "/api/hooks/h1/run-now", expect.objectContaining({ method: "POST" }));
    expect(fetch).toHaveBeenNthCalledWith(5, "/api/hooks/h1/runs");
    expect(fetch).toHaveBeenNthCalledWith(6, "/api/hooks/test-connection", expect.objectContaining({ method: "POST" }));
  });
});
