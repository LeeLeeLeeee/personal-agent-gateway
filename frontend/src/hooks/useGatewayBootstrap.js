import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client.js";
import { normalizeApproval, timelineFromHistory, timelineFromSession } from "../lib/timeline.js";
import { emptyChatSessionState, withSessionConfigStatus } from "./sessionState.js";

function normalizeEnvironmentTitle(value) {
  return typeof value === "string" ? value.trim() : "";
}

export function useGatewayBootstrap({
  activeSessionIdRef,
  setSessionStateById
}) {
  const [booting, setBooting] = useState(true);
  const [authenticated, setAuthenticated] = useState(false);
  const [authStage, setAuthStage] = useState("login");
  const [authError, setAuthError] = useState("");
  const [setup, setSetup] = useState(null);
  const [recoveryCodes, setRecoveryCodes] = useState([]);
  const [status, setStatus] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [agents, setAgents] = useState([]);
  const [sessionConfig, setSessionConfig] = useState(null);
  const [activeSessionId, setActiveSessionId] = useState(null);

  const loadApp = useCallback(async () => {
    let [nextStatus, nextSessions, history, nextAgents, nextConfig] = await Promise.all([
      api.getStatus(),
      api.sessions(),
      api.history(),
      api.agents(),
      api.activeSessionConfig()
    ]);
    let sessionId = nextStatus?.session_id || null;
    // On initial load, open the most recent real conversation rather than a
    // blank/empty session. Switch only when the active session is empty — no
    // active session, or one still titled "Untitled session" (no messages yet).
    // An already-active conversation, or an active id we can't classify, is
    // left as-is. Falls back to the current session if activation fails.
    const isConversation = (session) => !!(
      session && session.title && session.title !== "Untitled session"
      && (session.origin == null || session.origin === "chat")
    );
    const activeSummary = (nextSessions || []).find((session) => session.id === sessionId);
    const activeIsEmpty = sessionId == null
      || (activeSummary && activeSummary.title === "Untitled session");
    if (activeIsEmpty) {
      const latest = (nextSessions || []).find(isConversation);
      if (latest && latest.id !== sessionId && api.activate) {
        try {
          await api.activate(latest.id);
          sessionId = latest.id;
          [nextStatus, nextConfig] = await Promise.all([
            api.getStatus(),
            api.activeSessionConfig()
          ]);
        } catch (_error) {
          // keep the backend-active session if activation fails
        }
      }
    }
    setActiveSessionId(sessionId);
    activeSessionIdRef.current = sessionId;
    setStatus(withSessionConfigStatus(nextStatus, nextConfig));
    setSessions(nextSessions);
    setAgents(nextAgents);
    setSessionConfig(nextConfig || nextStatus?.session_config || null);
    let nextEntries = timelineFromHistory(history);
    if (sessionId && api.sessionHistory && api.sessionActivity) {
      const [sessionHistory, sessionActivity] = await Promise.allSettled([
        api.sessionHistory(sessionId),
        api.sessionActivity(sessionId)
      ]);
      if (sessionHistory.status === "fulfilled" && sessionActivity.status === "fulfilled") {
        nextEntries = timelineFromSession(sessionHistory.value, sessionActivity.value);
      }
    }
    setSessionStateById((current) => (
      sessionId
        ? {
          ...current,
          [sessionId]: {
            ...emptyChatSessionState(),
            entries: nextEntries,
            pendingApproval: normalizeApproval(nextStatus?.pending_approval),
            busy: nextStatus?.session_status === "running" || nextStatus?.status === "running",
            nextLocalOrder: nextEntries.length,
            lastLoadedAt: Date.now()
          }
        }
        : current
    ));
    setAuthenticated(true);
    setBooting(false);
  }, [activeSessionIdRef, setSessionStateById]);

  useEffect(() => {
    let alive = true;
    async function bootstrap() {
      const auth = await api.authStatus();
      if (!alive) return;
      if (auth.authenticated) {
        await loadApp();
        return;
      }
      const stage = auth.totp_configured ? "login" : "setup";
      setAuthStage(stage);
      if (stage === "setup") setSetup(await api.setupStart());
      setBooting(false);
    }
    bootstrap();
    return () => {
      alive = false;
    };
  }, [loadApp]);

  const environmentTitle = normalizeEnvironmentTitle(status?.environment_title);
  useEffect(() => {
    document.title = environmentTitle
      ? `${environmentTitle} · Agent Gateway`
      : "Agent Gateway";
  }, [environmentTitle]);

  async function handleLogin(otp) {
    const ok = await api.login(otp);
    if (!ok) {
      setAuthError("Invalid code. Session refused.");
      return;
    }
    setAuthError("");
    await loadApp();
  }

  async function handleSetupStart() {
    setAuthError("");
    setAuthStage("setup");
    setSetup(await api.setupStart());
  }

  async function handleSetupVerify(otp) {
    const result = await api.setupVerify(otp);
    if (result?.enabled) {
      setRecoveryCodes(result.recovery_codes || []);
      setAuthStage("recovery");
      setAuthError("");
      return;
    }
    setAuthError("Code did not match. Try the current code.");
  }

  return {
    booting,
    authenticated,
    setAuthenticated,
    authStage,
    setAuthStage,
    authError,
    setup,
    recoveryCodes,
    status,
    setStatus,
    sessions,
    setSessions,
    agents,
    sessionConfig,
    setSessionConfig,
    activeSessionId,
    setActiveSessionId,
    environmentTitle,
    loadApp,
    handleLogin,
    handleSetupStart,
    handleSetupVerify
  };
}
