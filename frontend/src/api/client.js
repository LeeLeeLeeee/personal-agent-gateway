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
  async searchSessions(query) {
    return jsonList(await fetch(`/api/sessions/search?q=${encodeURIComponent(query)}`), "sessions");
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
  async renameSession(id, title) {
    return jsonOrNull(await fetch(`/api/sessions/${encodeURIComponent(id)}/title`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title })
    }));
  }
};
