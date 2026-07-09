async function jsonOrNull(response) {
  return response.ok ? response.json() : null;
}

async function jsonList(response, key) {
  if (!response.ok) return [];
  const body = await response.json();
  return body[key] || [];
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
  async sessions() {
    return jsonList(await fetch("/api/sessions"), "sessions");
  },
  async agents() {
    return jsonList(await fetch("/api/agents"), "agents");
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
  async settings() {
    const body = await jsonOrNull(await fetch("/api/settings"));
    return body?.settings || null;
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
  async startTeamRun(id) {
    const body = await jsonOrNull(await fetch(`/api/team-runs/${encodeURIComponent(id)}/start`, { method: "POST" }));
    return body?.team_run || null;
  },
  async deleteTeamRun(id) {
    const response = await fetch(`/api/team-runs/${encodeURIComponent(id)}`, { method: "DELETE" });
    return response.ok;
  },
  async avatarManifest() {
    return jsonList(await fetch("/static/avatars/manifest.json"), "avatars");
  },
  async teamRunDetail(id) {
    const [run, agents, tasks, messages] = await Promise.all([
      jsonOrNull(await fetch(`/api/team-runs/${encodeURIComponent(id)}`)),
      jsonList(await fetch(`/api/team-runs/${encodeURIComponent(id)}/agents`), "agents"),
      jsonList(await fetch(`/api/team-runs/${encodeURIComponent(id)}/tasks`), "tasks"),
      jsonList(await fetch(`/api/team-runs/${encodeURIComponent(id)}/messages`), "messages")
    ]);
    return { run: run?.team_run || null, agents, tasks, messages };
  }
};
