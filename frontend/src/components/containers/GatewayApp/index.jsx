import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../../api/client.js";
import { entryFromSse, normalizeApproval, timelineFromHistory } from "../../../lib/timeline.js";
import { nowHM } from "../../../lib/time.js";
import { AuthCard } from "../../molecules/AuthCard/index.jsx";
import { AuthTemplate } from "../../templates/AuthTemplate/index.jsx";
import { AppShell } from "../../templates/AppShell/index.jsx";
import { ChatView } from "../../organisms/ChatView/index.jsx";
import { NAV } from "../../organisms/Sidebar/index.jsx";

function appendOrReconcileCommand(entries, entry) {
  if (entry.type !== "command") return [...entries, entry];
  const index = entries.findIndex((candidate) => candidate.type === "command" && candidate.key === entry.key);
  if (index < 0) return [...entries, entry];
  const next = entries.slice();
  next[index] = entry;
  return next;
}

function useForceTick(active) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!active) return undefined;
    const timer = window.setInterval(() => setTick((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
}

export function GatewayApp() {
  const [booting, setBooting] = useState(true);
  const [authenticated, setAuthenticated] = useState(false);
  const [authStage, setAuthStage] = useState("login");
  const [authError, setAuthError] = useState("");
  const [setup, setSetup] = useState(null);
  const [recoveryCodes, setRecoveryCodes] = useState([]);
  const [screen, setScreen] = useState("chat");
  const [status, setStatus] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [entries, setEntries] = useState([]);
  const [pendingApproval, setPendingApproval] = useState(null);
  const [busy, setBusy] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [sseState, setSseState] = useState("idle");
  const [turnStart, setTurnStart] = useState(null);
  const [turnEnd, setTurnEnd] = useState(null);
  const [turnStreamed, setTurnStreamed] = useState(false);
  const turnHadAgentRef = useRef(false);
  const turnStreamedRef = useRef(false);
  const turnStartRef = useRef(null);

  useForceTick(screen === "chat" && busy);

  const loadApp = useCallback(async () => {
    const [nextStatus, nextSessions, history] = await Promise.all([
      api.getStatus(),
      api.sessions(),
      api.history()
    ]);
    setStatus(nextStatus);
    setSessions(nextSessions);
    setEntries(timelineFromHistory(history));
    setAuthenticated(true);
    setBooting(false);
  }, []);

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

  useEffect(() => {
    if (!authenticated || typeof EventSource === "undefined") return undefined;
    const source = new EventSource("/api/events");
    source.onopen = () => {
      if (!busy) setSseState("connected");
    };
    source.onerror = () => setSseState("error");
    source.onmessage = (event) => {
      let parsed;
      try {
        parsed = JSON.parse(event.data);
      } catch (_error) {
        return;
      }
      if (parsed.type === "runtime.user_message.started") {
        const started = Date.now();
        turnStartRef.current = started;
        setTurnStart(started);
        setTurnEnd(null);
      }
      const entry = entryFromSse(parsed);
      if (!entry) return;
      turnStreamedRef.current = true;
      setTurnStreamed(true);
      if (entry.type === "agent") turnHadAgentRef.current = true;
      setEntries((current) => appendOrReconcileCommand(current, entry));
    };
    return () => source.close();
  }, [authenticated, busy]);

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

  async function refreshStatusAndSessions() {
    const [nextStatus, nextSessions] = await Promise.all([api.getStatus(), api.sessions()]);
    setStatus(nextStatus);
    setSessions(nextSessions);
    return nextStatus;
  }

  async function maybeAppendArtifact(nextStatus) {
    if (!turnStartRef.current) return;
    const artifacts = await api.artifacts();
    const sessionId = nextStatus?.session_id;
    const fresh = artifacts.filter((artifact) => (
      artifact.source_session_id === sessionId && Date.parse(artifact.created_at) >= turnStartRef.current - 2000
    ));
    if (fresh.length) setEntries((current) => [...current, ...fresh.map((artifact) => ({ type: "artifact", artifact }))]);
  }

  async function postTurn(data) {
    setPendingApproval(data ? normalizeApproval(data.pending_approval) : null);
    if (!turnHadAgentRef.current && data && Array.isArray(data.messages)) {
      const agentEntries = data.messages
        .filter((message) => typeof message.content === "string")
        .map((message) => ({ type: "agent", text: message.content, time: nowHM() }));
      if (agentEntries.length) setEntries((current) => [...current, ...agentEntries]);
    }
    const nextStatus = await refreshStatusAndSessions();
    await maybeAppendArtifact(nextStatus);
  }

  async function handleSend(message) {
    const started = Date.now();
    turnStartRef.current = started;
    turnHadAgentRef.current = false;
    turnStreamedRef.current = false;
    setEntries((current) => [...current, { type: "user", text: message, time: nowHM() }]);
    setBusy(true);
    setTurnStart(started);
    setTurnEnd(null);
    setTurnStreamed(false);
    try {
      await postTurn(await api.sendChat(message));
    } finally {
      setBusy(false);
      setTurnEnd(Date.now());
    }
  }

  async function handleResolveApproval(action) {
    if (!pendingApproval || busy) return;
    const started = turnStartRef.current || Date.now();
    turnStartRef.current = started;
    turnHadAgentRef.current = false;
    turnStreamedRef.current = true;
    setBusy(true);
    setTurnStart(started);
    setTurnEnd(null);
    setTurnStreamed(true);
    try {
      const data = action === "approve" ? await api.approve(pendingApproval.id) : await api.deny(pendingApproval.id);
      await postTurn(data);
    } finally {
      setBusy(false);
      setTurnEnd(Date.now());
    }
  }

  async function handleSearch(query) {
    setSessions(query ? await api.searchSessions(query) : await api.sessions());
  }

  async function handleActivate(id) {
    const data = await api.activate(id);
    if (!data) return;
    setEntries(timelineFromHistory(data.events || []));
    setPendingApproval(null);
    setTurnStart(null);
    setTurnEnd(null);
    turnStartRef.current = null;
    turnHadAgentRef.current = false;
    turnStreamedRef.current = false;
    setTurnStreamed(false);
    await refreshStatusAndSessions();
  }

  async function handleReset() {
    await api.reset();
    setEntries([]);
    setPendingApproval(null);
    setTurnStart(null);
    setTurnEnd(null);
    turnStartRef.current = null;
    await refreshStatusAndSessions();
  }

  async function handleRename(id, title) {
    await api.renameSession(id, title);
    setSessions(await api.sessions());
  }

  async function handleDelete(id) {
    await api.deleteSession(id);
    setSessions(await api.sessions());
  }

  async function handleLogout() {
    await api.logout();
    window.location.reload();
  }

  if (booting) {
    return <AuthTemplate><div className="headline" style={{ fontSize: 22 }}>Loading</div></AuthTemplate>;
  }

  if (!authenticated) {
    return (
      <AuthTemplate>
        <AuthCard
          stage={authStage}
          setup={setup}
          recoveryCodes={recoveryCodes}
          authError={authError}
          onLogin={handleLogin}
          onSetupStart={handleSetupStart}
          onSetupVerify={handleSetupVerify}
          onContinue={loadApp}
        />
      </AuthTemplate>
    );
  }

  const activeNav = NAV.find((item) => item.key === screen);

  return (
    <AppShell
      screen={screen}
      status={status}
      entries={entries}
      busy={busy}
      turnStart={turnStart}
      turnEnd={turnEnd}
      sseState={sseState}
      navOpen={navOpen}
      onToggleNav={() => setNavOpen((value) => !value)}
      onCloseNav={() => setNavOpen(false)}
      onScreenChange={(nextScreen) => {
        setScreen(nextScreen);
        setNavOpen(false);
      }}
      onLogout={handleLogout}
    >
      {screen === "chat" ? (
        <ChatView
          sessions={sessions}
          entries={entries}
          busy={busy}
          turnStart={turnStart}
          turnEnd={turnEnd}
          pendingApproval={pendingApproval}
          turnStreamed={turnStreamed}
          onSend={handleSend}
          onSearch={handleSearch}
          onActivate={handleActivate}
          onReset={handleReset}
          onRename={handleRename}
          onDelete={handleDelete}
          onResolveApproval={handleResolveApproval}
        />
      ) : (
        <div className="planned">{(activeNav?.label || screen).toUpperCase()} - PLANNED</div>
      )}
    </AppShell>
  );
}
