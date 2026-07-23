export class ApiError extends Error {
  constructor({
    status = 0,
    code = "request_failed",
    detail = "Request failed",
    retryable = false,
    correlationId = null
  } = {}) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
    this.retryable = retryable;
    this.correlationId = correlationId;
  }
}

export function apiErrorAction(error) {
  if (!(error instanceof ApiError)) return "dismiss";
  if (error.status === 401) return "relogin";
  if (error.status === 400 || error.status === 422) return "fix_input";
  if (error.status === 409) return "refresh";
  if (error.retryable || error.status === 0 || error.status >= 500) return "retry";
  return "dismiss";
}

async function fetch(input, init) {
  try {
    return await (init === undefined
      ? globalThis.fetch(input)
      : globalThis.fetch(input, init));
  } catch (error) {
    const timedOut = error?.name === "AbortError";
    throw new ApiError({
      status: 0,
      code: timedOut ? "request_timeout" : "network_error",
      detail: timedOut ? "Request timed out" : "Network request failed",
      retryable: true
    });
  }
}

async function jsonOrNull(response) {
  const body = await response.json().catch(() => null);
  if (!response.ok) throw apiErrorFromResponse(response, body);
  return body;
}

async function jsonList(response, key) {
  const body = await jsonOrNull(response);
  return body[key] || [];
}

function apiErrorFromResponse(response, body) {
  return new ApiError({
    status: response.status || 0,
    code: body?.code || `http_${response.status || 0}`,
    detail: body?.detail || response.statusText || "Request failed",
    retryable: body?.retryable ?? response.status >= 500,
    correlationId: body?.correlation_id
      || response.headers?.get?.("X-Correlation-ID")
      || null
  });
}

export const api = {
  async getStatus() {
    return jsonOrNull(await fetch("/api/status"));
  },
  async authStatus() {
    const response = await fetch("/api/auth/status");
    return response.ok ? response.json() : { authenticated: false, totp_configured: false };
  },
  async login(otp) {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ otp })
    });
    return response.ok;
  },
  async setupStart() {
    return jsonOrNull(await fetch("/api/auth/setup/start", { method: "POST" }));
  },
  async setupVerify(otp) {
    return jsonOrNull(await fetch("/api/auth/setup/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ otp })
    }));
  },
  async logout() {
    await fetch("/api/auth/logout", { method: "POST" });
  },
  async history() {
    return jsonList(await fetch("/api/history"), "events");
  },
  async sendChat(message) {
    return jsonOrNull(await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message })
    }));
  },
  async sessionHistory(id) {
    return jsonList(await fetch(`/api/sessions/${encodeURIComponent(id)}/history`), "events");
  },
  async sessionActivity(id) {
    return jsonList(await fetch(`/api/sessions/${encodeURIComponent(id)}/activity`), "events");
  },
  async sessionStatus(id) {
    return jsonOrNull(await fetch(`/api/sessions/${encodeURIComponent(id)}/status`));
  },
  async sendSessionChat(id, message) {
    return jsonOrNull(await fetch(`/api/sessions/${encodeURIComponent(id)}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message })
    }));
  },
  async interruptSession(id) {
    return jsonOrNull(await fetch(`/api/sessions/${encodeURIComponent(id)}/interrupt`, { method: "POST" }));
  },
  async approveSession(id, approvalId) {
    return jsonOrNull(await fetch(
      `/api/sessions/${encodeURIComponent(id)}/approvals/${encodeURIComponent(approvalId)}/approve`,
      { method: "POST" }
    ));
  },
  async denySession(id, approvalId) {
    return jsonOrNull(await fetch(
      `/api/sessions/${encodeURIComponent(id)}/approvals/${encodeURIComponent(approvalId)}/deny`,
      { method: "POST" }
    ));
  },
  async sessions() {
    return jsonList(await fetch("/api/sessions"), "sessions");
  },
  async agents() {
    return jsonList(await fetch("/api/agents"), "agents");
  },
  async dashboardUsage() {
    return jsonOrNull(await fetch("/api/dashboard/usage"));
  },
  async dashboardSessions() {
    return jsonOrNull(await fetch("/api/dashboard/sessions"));
  },
  async searchSessions(query) {
    return jsonList(await fetch(`/api/sessions/search?q=${encodeURIComponent(query)}`), "sessions");
  },
  async activeSessionConfig() {
    const body = await jsonOrNull(await fetch("/api/sessions/active/config"));
    return body?.config || null;
  },
  async updateActiveSessionConfig(config) {
    const body = await jsonOrNull(await fetch("/api/sessions/active/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config)
    }));
    return body?.config || null;
  },
  async activate(id) {
    return jsonOrNull(await fetch(`/api/sessions/${encodeURIComponent(id)}/activate`, { method: "POST" }));
  },
  async deleteSession(id) {
    return jsonOrNull(await fetch(`/api/sessions/${encodeURIComponent(id)}`, { method: "DELETE" }));
  },
  async reset() {
    return jsonOrNull(await fetch("/api/reset", { method: "POST" }));
  },
  async approve(id) {
    return jsonOrNull(await fetch(`/api/approvals/${encodeURIComponent(id)}/approve`, { method: "POST" }));
  },
  async deny(id) {
    return jsonOrNull(await fetch(`/api/approvals/${encodeURIComponent(id)}/deny`, { method: "POST" }));
  },
  async artifacts() {
    return jsonList(await fetch("/api/artifacts"), "artifacts");
  },
  artifactContentUrl(id) {
    return `/api/artifacts/${encodeURIComponent(id)}/content`;
  },
  artifactThumbnailUrl(id) {
    return `/api/artifacts/${encodeURIComponent(id)}/thumbnail`;
  },
  async artifactText(id) {
    const response = await fetch(this.artifactContentUrl(id));
    return response.ok ? response.text() : "";
  },
  async registerArtifact(body) {
    const res = await fetch("/api/artifacts/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    const data = (res.ok || res.status === 409) ? await res.json().catch(() => null) : null;
    return { status: res.status, ok: res.ok, data };
  },
  async deleteArtifact(id) {
    const res = await fetch(`/api/artifacts/${encodeURIComponent(id)}`, { method: "DELETE" });
    return res.ok;
  },
  async settings() {
    const body = await jsonOrNull(await fetch("/api/settings"));
    return body?.settings || null;
  },
  async authSessions() {
    return jsonList(await fetch("/api/auth/sessions"), "sessions");
  },
  async revokeAuthSession(id) {
    return jsonOrNull(await fetch(
      `/api/auth/sessions/${encodeURIComponent(id)}`,
      { method: "DELETE" }
    ));
  },
  async revokeAllAuthSessions() {
    return jsonOrNull(await fetch("/api/auth/sessions/revoke-all", { method: "POST" }));
  },
  async setAccessMode(mode, confirmFullAccess = false) {
    const body = await jsonOrNull(await fetch("/api/settings/access-mode", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, confirm_full_access: confirmFullAccess })
    }));
    return body?.access_mode || null;
  },
  async jobs() {
    return jsonList(await fetch("/api/jobs"), "jobs");
  },
  async jobEvents(id) {
    return jsonList(await fetch(`/api/jobs/${encodeURIComponent(id)}/events`), "events");
  },
  async operations() {
    return jsonOrNull(await fetch("/api/operations"));
  },
  async emergencyStop() {
    return jsonOrNull(await fetch("/api/operations/emergency-stop", { method: "POST" }));
  },
  async resumeIntake() {
    return jsonOrNull(await fetch("/api/operations/resume-intake", { method: "POST" }));
  },
  async createBackup() {
    const body = await jsonOrNull(await fetch("/api/operations/backups", { method: "POST" }));
    return body?.backup || null;
  },
  async verifyBackup(id) {
    return jsonOrNull(await fetch(
      `/api/operations/backups/${encodeURIComponent(id)}/dry-run`,
      { method: "POST" }
    ));
  },
  async retryJob(id) {
    const body = await jsonOrNull(await fetch(
      `/api/jobs/${encodeURIComponent(id)}/retry`,
      { method: "POST" }
    ));
    return body?.job || null;
  },
  async schedules() {
    return jsonList(await fetch("/api/schedules"), "schedules");
  },
  async scheduleDetail(id) {
    return jsonOrNull(await fetch(`/api/schedules/${encodeURIComponent(id)}`));
  },
  async createSchedule(payload) {
    const body = await jsonOrNull(await fetch("/api/schedules", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    }));
    return body?.schedule || null;
  },
  async pauseSchedule(id) {
    const body = await jsonOrNull(await fetch(`/api/schedules/${encodeURIComponent(id)}/pause`, { method: "POST" }));
    return body?.schedule || null;
  },
  async resumeSchedule(id) {
    const body = await jsonOrNull(await fetch(`/api/schedules/${encodeURIComponent(id)}/resume`, { method: "POST" }));
    return body?.schedule || null;
  },
  async runScheduleNow(id) {
    return jsonOrNull(await fetch(`/api/schedules/${encodeURIComponent(id)}/run-now`, { method: "POST" }));
  },
  async deleteSchedule(id) {
    const response = await fetch(`/api/schedules/${encodeURIComponent(id)}`, { method: "DELETE" });
    return response.ok;
  },
  async renameSession(id, title) {
    return jsonOrNull(await fetch(`/api/sessions/${encodeURIComponent(id)}/title`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title })
    }));
  },
  async personas() {
    return jsonList(await fetch("/api/personas"), "personas");
  },
  async createPersona(payload) {
    const body = await jsonOrNull(await fetch("/api/personas", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }));
    return body?.persona || null;
  },
  async updatePersona(id, payload) {
    const body = await jsonOrNull(await fetch(`/api/personas/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }));
    return body?.persona || null;
  },
  async deletePersona(id) {
    const response = await fetch(`/api/personas/${encodeURIComponent(id)}`, { method: "DELETE" });
    return response.ok;
  },
  async spacePolicies() {
    return jsonOrNull(await fetch("/api/spaces"));
  },
  async updateGlobalSpace(payload) {
    const body = await jsonOrNull(await fetch("/api/spaces/global", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    }));
    return body?.space_policy || null;
  },
  async updatePersonaSpace(personaId, payload) {
    const body = await jsonOrNull(await fetch(`/api/spaces/personas/${encodeURIComponent(personaId)}`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    }));
    return body?.space_policy || null;
  },
  async deletePersonaSpace(personaId) {
    const response = await fetch(`/api/spaces/personas/${encodeURIComponent(personaId)}`, { method: "DELETE" });
    if (!response.ok) await jsonOrNull(response);
    return true;
  },
  async updateTeamSpace(teamId, payload) {
    const body = await jsonOrNull(await fetch(`/api/spaces/teams/${encodeURIComponent(teamId)}`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    }));
    return body?.space_policy || null;
  },
  async teamRuns() {
    return jsonList(await fetch("/api/team-runs"), "team_runs");
  },
  async createTeamRun(payload) {
    const body = await jsonOrNull(await fetch("/api/team-runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }));
    return body?.team_run || null;
  },
  async triggerTeamCycle(id, payload) {
    return jsonOrNull(await fetch(
      `/api/team-runs/${encodeURIComponent(id)}/cycle-requests`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      }
    ));
  },
  async retryAutoCycle(id, seriesId) {
    return jsonOrNull(await fetch(
      `/api/team-runs/${encodeURIComponent(id)}/auto-series/`
        + `${encodeURIComponent(seriesId)}/retry`,
      { method: "POST" }
    ));
  },
  async continueAutoCycle(id, seriesId) {
    return jsonOrNull(await fetch(
      `/api/team-runs/${encodeURIComponent(id)}/auto-series/`
        + `${encodeURIComponent(seriesId)}/continue`,
      { method: "POST" }
    ));
  },
  async restartAutoSeries(id) {
    return jsonOrNull(await fetch(
      `/api/team-runs/${encodeURIComponent(id)}/auto-series/restart`,
      { method: "POST" }
    ));
  },
  async startTeamRun(id) {
    const body = await jsonOrNull(await fetch(`/api/team-runs/${encodeURIComponent(id)}/start`, { method: "POST" }));
    return body?.team_run || null;
  },
  async resumeTeamRun(id) {
    const body = await jsonOrNull(await fetch(`/api/team-runs/${encodeURIComponent(id)}/resume`, { method: "POST" }));
    return body?.team_run || null;
  },
  async cancelTeamRun(id) {
    const body = await jsonOrNull(await fetch(
      `/api/team-runs/${encodeURIComponent(id)}/cancel`, { method: "POST" }
    ));
    return body?.team_run || null;
  },
  async answerTeamDecision(id, requestId, revision, answers) {
    const body = await jsonOrNull(await fetch(
      `/api/team-runs/${encodeURIComponent(id)}/decision-request/answer`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ request_id: requestId, revision, answers })
      }
    ));
    return body?.team_run && body?.decision_request
      ? { run: body.team_run, decisionRequest: body.decision_request }
      : null;
  },
  async retryTeamTask(runId, taskId) {
    const path = "/api/team-runs/" + encodeURIComponent(runId)
      + "/tasks/" + encodeURIComponent(taskId) + "/retry";
    const body = await jsonOrNull(await fetch(path, { method: "POST" }));
    return body?.team_run && body?.task ? { run: body.team_run, task: body.task } : null;
  },
  async addWork(id, instruction) {
    return jsonOrNull(await fetch(`/api/team-runs/${encodeURIComponent(id)}/add-work`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ instruction })
    }));
  },
  async deleteTeamRun(id) {
    const response = await fetch(`/api/team-runs/${encodeURIComponent(id)}`, { method: "DELETE" });
    return response.ok;
  },
  async teamRunDelivery(id) {
    const body = await jsonOrNull(await fetch(
      `/api/team-runs/${encodeURIComponent(id)}/delivery`
    ));
    return body?.delivery || null;
  },
  async commitTeamRunDelivery(id, message) {
    const body = await jsonOrNull(await fetch(
      `/api/team-runs/${encodeURIComponent(id)}/delivery/commit`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message })
      }
    ));
    return body?.delivery || null;
  },
  async applyTeamRunDelivery(id) {
    const body = await jsonOrNull(await fetch(
      `/api/team-runs/${encodeURIComponent(id)}/delivery/apply`,
      { method: "POST" }
    ));
    return body?.delivery || null;
  },
  async resolveTeamRunDeliveryConflict(id, conflictId, resolution) {
    const body = await jsonOrNull(await fetch(
      `/api/team-runs/${encodeURIComponent(id)}/delivery/conflicts/${encodeURIComponent(conflictId)}/resolve`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(resolution)
      }
    ));
    return body?.delivery || null;
  },
  async continueTeamRunDelivery(id) {
    const body = await jsonOrNull(await fetch(
      `/api/team-runs/${encodeURIComponent(id)}/delivery/continue`,
      { method: "POST" }
    ));
    return body?.delivery || null;
  },
  async cancelTeamRunDeliveryConflicts(id) {
    const body = await jsonOrNull(await fetch(
      `/api/team-runs/${encodeURIComponent(id)}/delivery/conflicts`,
      { method: "DELETE" }
    ));
    return body?.delivery || null;
  },
  async avatarManifest() {
    return jsonList(await fetch("/static/avatars/manifest.json"), "avatars");
  },
  async teamRunDetail(id) {
    const encodedId = encodeURIComponent(id);
    try {
      const body = await jsonOrNull(await fetch(`/api/team-runs/${encodedId}/detail`));
      return {
        run: body?.team_run || null,
        agents: body?.agents || [],
        tasks: body?.tasks || [],
        messages: body?.messages || [],
        cycles: body?.cycles || [],
        decisionRequest: body?.decision_request || null,
        documentSummary: body?.document_summary || null,
        policyStatus: body?.policy_status || "ready",
        activeAutoSeries: body?.active_auto_series || null,
        queueCount: body?.queue_count || 0,
        activeRequest: body?.active_request || null
      };
    } catch (error) {
      if (!(error instanceof ApiError) || ![0, 404].includes(error.status)) throw error;
      const [run, agents, tasks, messages] = await Promise.all([
        jsonOrNull(await fetch(`/api/team-runs/${encodedId}`)),
        jsonList(await fetch(`/api/team-runs/${encodedId}/agents`), "agents"),
        jsonList(await fetch(`/api/team-runs/${encodedId}/tasks`), "tasks"),
        jsonList(await fetch(`/api/team-runs/${encodedId}/messages`), "messages")
      ]);
      return {
        run: run?.team_run || null,
        agents,
        tasks,
        messages,
        cycles: [],
        decisionRequest: null,
        documentSummary: null,
        policyStatus: "ready",
        activeAutoSeries: null,
        queueCount: 0,
        activeRequest: null
      };
    }
  },
  async teams() {
    return jsonList(await fetch("/api/teams"), "teams");
  },
  async createTeam(payload) {
    const body = await jsonOrNull(await fetch("/api/teams", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    }));
    return body?.team || null;
  },
  async updateTeam(id, payload) {
    const body = await jsonOrNull(await fetch(`/api/teams/${encodeURIComponent(id)}`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    }));
    return body?.team || null;
  },
  async deleteTeam(id) {
    const response = await fetch(`/api/teams/${encodeURIComponent(id)}`, { method: "DELETE" });
    return response.ok;
  },
  async rules() {
    return jsonOrNull(await fetch("/api/rules"));
  },
  async updateGlobalRules(payload) {
    const body = await jsonOrNull(await fetch("/api/rules/global", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    }));
    return body?.rule_set || null;
  },
  async updatePersonaBaselineRules(payload) {
    const body = await jsonOrNull(await fetch("/api/rules/persona-baseline", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    }));
    return body?.rule_set || null;
  },
  async updateTeamRules(teamId, payload) {
    const body = await jsonOrNull(await fetch(`/api/teams/${encodeURIComponent(teamId)}/rules`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    }));
    return body?.rule_set || null;
  },
  async teamDocuments(runId) {
    return jsonList(await fetch(`/api/team-runs/${encodeURIComponent(runId)}/documents`), "documents");
  },
  async teamDocumentContent(runId, path) {
    return jsonOrNull(await fetch(
      `/api/team-runs/${encodeURIComponent(runId)}/documents/content?path=${encodeURIComponent(path)}`
    ));
  },
  async listHooks() {
    return jsonList(await fetch("/api/hooks"), "hooks");
  },
  async createHook(body) {
    const res = await jsonOrNull(await fetch("/api/hooks", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
    }));
    return res?.hook || null;
  },
  async getHook(id) {
    const res = await jsonOrNull(await fetch(`/api/hooks/${encodeURIComponent(id)}`));
    return res?.hook || null;
  },
  async updateHook(id, body) {
    const res = await jsonOrNull(await fetch(`/api/hooks/${encodeURIComponent(id)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
    }));
    return res?.hook || null;
  },
  async deleteHook(id) {
    const response = await fetch(`/api/hooks/${encodeURIComponent(id)}`, { method: "DELETE" });
    return response.ok;
  },
  async runHookNow(id) {
    return jsonOrNull(await fetch(`/api/hooks/${encodeURIComponent(id)}/run-now`, { method: "POST" }));
  },
  async listHookRuns(id) {
    return jsonList(await fetch(`/api/hooks/${encodeURIComponent(id)}/runs`), "runs");
  },
  async testHookConnection(body) {
    return jsonOrNull(await fetch("/api/hooks/test-connection", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
    }));
  }
};
