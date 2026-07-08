import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../../api/client.js";
import { entryFromSse, normalizeApproval, timelineFromHistory } from "../../../lib/timeline.js";
import { nowHM } from "../../../lib/time.js";
import { AuthCard } from "../../molecules/AuthCard/index.jsx";
import { AuthTemplate } from "../../templates/AuthTemplate/index.jsx";
import { AppShell } from "../../templates/AppShell/index.jsx";
import { ChatView } from "../../organisms/ChatView/index.jsx";
import { NAV } from "../../organisms/Sidebar/index.jsx";
import { StatusBadge } from "../../atoms/StatusBadge/index.jsx";
import { PersonaLibrary } from "../../organisms/PersonaLibrary/index.jsx";
import { TeamRunForm } from "../../organisms/TeamRunForm/index.jsx";
import { TeamRunDetail } from "../../organisms/TeamRunDetail/index.jsx";

const DEFAULT_PERSONAS = [
  {
    name: "Tech Lead",
    role: "Planning",
    description: "Breaks a goal into a task plan and coordinates the team.",
    responsibilities: ["Plan tasks", "Assign owners", "Review results"],
    constraints: ["Do not write implementation code directly"],
    default_backend: "codex",
    default_model: "default"
  },
  {
    name: "Backend Engineer",
    role: "Implementation",
    description: "Implements server-side logic and APIs.",
    responsibilities: ["Write backend code", "Write tests"],
    constraints: ["Follow existing API contracts"],
    default_backend: "codex",
    default_model: "default"
  },
  {
    name: "Frontend Engineer",
    role: "Implementation",
    description: "Implements UI components and wiring.",
    responsibilities: ["Write UI code", "Write component tests"],
    constraints: ["Match the design system"],
    default_backend: "codex",
    default_model: "default"
  },
  {
    name: "QA Engineer",
    role: "Verification",
    description: "Verifies changes and reports defects.",
    responsibilities: ["Run tests", "Report issues"],
    constraints: ["Do not merge without verification"],
    default_backend: "codex",
    default_model: "default"
  },
  {
    name: "Technical Writer",
    role: "Documentation",
    description: "Writes release notes and documentation.",
    responsibilities: ["Document changes", "Write release notes"],
    constraints: ["Keep docs concise"],
    default_backend: "codex",
    default_model: "default"
  }
];

function appendOrReconcileCommand(entries, entry) {
  if (entry.type !== "command") return [...entries, entry];
  const index = entries.findIndex((candidate) => candidate.type === "command" && candidate.key === entry.key);
  if (index < 0) return [...entries, entry];
  const next = entries.slice();
  next[index] = entry;
  return next;
}

function withSessionConfigStatus(nextStatus, nextConfig) {
  if (!nextConfig) return nextStatus;
  if (nextConfig.source !== "explicit") {
    return {
      ...(nextStatus || {}),
      session_config: nextConfig
    };
  }
  return {
    ...(nextStatus || {}),
    provider: nextConfig.agent_id ?? nextStatus?.provider,
    model: nextConfig.model ?? nextStatus?.model,
    session_config: nextConfig
  };
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
  const [agents, setAgents] = useState([]);
  const [sessionConfig, setSessionConfig] = useState(null);
  const [sessionConfigError, setSessionConfigError] = useState("");
  const [entries, setEntries] = useState([]);
  const [pendingApproval, setPendingApproval] = useState(null);
  const [busy, setBusy] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [sseState, setSseState] = useState("idle");
  const [turnStart, setTurnStart] = useState(null);
  const [turnEnd, setTurnEnd] = useState(null);
  const [turnStreamed, setTurnStreamed] = useState(false);
  const [personas, setPersonas] = useState([]);
  const [teamRuns, setTeamRuns] = useState([]);
  const [selectedTeamRunId, setSelectedTeamRunId] = useState(null);
  const [teamRunDetail, setTeamRunDetail] = useState(null);
  const turnHadAgentRef = useRef(false);
  const turnStreamedRef = useRef(false);
  const turnStartRef = useRef(null);
  const selectedTeamRunIdRef = useRef(null);

  useForceTick(screen === "chat" && busy);

  const loadApp = useCallback(async () => {
    const [nextStatus, nextSessions, history, nextAgents, nextConfig] = await Promise.all([
      api.getStatus(),
      api.sessions(),
      api.history(),
      api.agents(),
      api.activeSessionConfig()
    ]);
    setStatus(withSessionConfigStatus(nextStatus, nextConfig));
    setSessions(nextSessions);
    setAgents(nextAgents);
    setSessionConfig(nextConfig || nextStatus?.session_config || null);
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
      if (parsed.type?.startsWith("team.") && parsed.team_run_id === selectedTeamRunIdRef.current) {
        api.teamRunDetail(selectedTeamRunIdRef.current).then(setTeamRunDetail);
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

  useEffect(() => {
    selectedTeamRunIdRef.current = selectedTeamRunId;
  }, [selectedTeamRunId]);

  useEffect(() => {
    if (!authenticated) return;
    if (screen === "personas") {
      api.personas().then(setPersonas);
    } else if (screen === "teams") {
      api.personas().then(setPersonas);
      api.teamRuns().then(setTeamRuns);
    }
  }, [screen, authenticated]);

  useEffect(() => {
    if (!selectedTeamRunId) {
      setTeamRunDetail(null);
      return undefined;
    }
    let alive = true;
    api.teamRunDetail(selectedTeamRunId).then((detail) => {
      if (alive) setTeamRunDetail(detail);
    });
    return () => {
      alive = false;
    };
  }, [selectedTeamRunId]);

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
    const [nextStatus, nextSessions, nextConfig] = await Promise.all([
      api.getStatus(),
      api.sessions(),
      api.activeSessionConfig()
    ]);
    setStatus(withSessionConfigStatus(nextStatus, nextConfig));
    setSessions(nextSessions);
    setSessionConfig(nextConfig || nextStatus?.session_config || null);
    return nextStatus;
  }

  async function handleSessionConfigChange(nextConfig) {
    setSessionConfigError("");
    let saved;
    try {
      saved = await api.updateActiveSessionConfig({
        agent_id: nextConfig.agent_id,
        model: nextConfig.model,
        options: nextConfig.options || {}
      });
    } catch (_error) {
      setSessionConfigError("Config update failed");
      return;
    }
    if (!saved) {
      setSessionConfigError("Config update failed");
      return;
    }
    setSessionConfig(saved);
    await refreshStatusAndSessions();
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
    setSessionConfigError("");
    await refreshStatusAndSessions();
  }

  async function handleReset() {
    await api.reset();
    setEntries([]);
    setPendingApproval(null);
    setTurnStart(null);
    setTurnEnd(null);
    turnStartRef.current = null;
    setSessionConfigError("");
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

  async function handleCreatePersona(payload) {
    await api.createPersona(payload);
    setPersonas(await api.personas());
  }

  async function handleSeedDefaults() {
    await Promise.all(DEFAULT_PERSONAS.map((persona) => api.createPersona(persona)));
    setPersonas(await api.personas());
  }

  async function handleCreateTeamRun(payload) {
    const created = await api.createTeamRun(payload);
    if (!created) return;
    const started = await api.startTeamRun(created.id);
    setTeamRuns(await api.teamRuns());
    setSelectedTeamRunId((started || created).id);
  }

  function handleSelectTeamRun(id) {
    setSelectedTeamRunId(id);
  }

  function handleBackToTeamRuns() {
    setSelectedTeamRunId(null);
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
          agents={agents}
          sessions={sessions}
          sessionConfig={sessionConfig}
          sessionConfigError={sessionConfigError}
          entries={entries}
          busy={busy}
          turnStart={turnStart}
          turnEnd={turnEnd}
          pendingApproval={pendingApproval}
          turnStreamed={turnStreamed}
          onSessionConfigChange={handleSessionConfigChange}
          onSend={handleSend}
          onSearch={handleSearch}
          onActivate={handleActivate}
          onReset={handleReset}
          onRename={handleRename}
          onDelete={handleDelete}
          onResolveApproval={handleResolveApproval}
        />
      ) : screen === "personas" ? (
        <PersonaLibrary personas={personas} onCreate={handleCreatePersona} onSeedDefaults={handleSeedDefaults} />
      ) : screen === "teams" ? (
        selectedTeamRunId ? (
          <div>
            <a
              href="#"
              className="mono team-run-back"
              onClick={(event) => {
                event.preventDefault();
                handleBackToTeamRuns();
              }}
            >
              ← TEAM RUNS
            </a>
            <TeamRunDetail detail={teamRunDetail} />
          </div>
        ) : (
          <div className="team-runs-home">
            <div className="team-runs-home-head">
              <h1 className="headline" style={{ fontSize: 28 }}>Team Runs</h1>
            </div>
            <div className="team-run-list">
              {teamRuns.map((run) => (
                <button
                  key={run.id}
                  type="button"
                  className="team-run-list-item"
                  onClick={() => handleSelectTeamRun(run.id)}
                >
                  <span className="mono team-run-list-id">{run.id}</span>
                  <StatusBadge kind={run.status} />
                  <span className="headline team-run-list-goal">{run.goal}</span>
                </button>
              ))}
            </div>
            <TeamRunForm personas={personas} onSubmit={handleCreateTeamRun} />
          </div>
        )
      ) : (
        <div className="planned">{(activeNav?.label || screen).toUpperCase()} - PLANNED</div>
      )}
    </AppShell>
  );
}
