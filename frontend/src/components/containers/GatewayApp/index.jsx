import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../../api/client.js";
import { entryFromSse, normalizeApproval, timelineFromHistory } from "../../../lib/timeline.js";
import { nowHM } from "../../../lib/time.js";
import { AuthCard } from "../../molecules/AuthCard/index.jsx";
import { AuthTemplate } from "../../templates/AuthTemplate/index.jsx";
import { AppShell } from "../../templates/AppShell/index.jsx";
import { ChatView } from "../../organisms/ChatView/index.jsx";
import { NAV } from "../../organisms/Sidebar/index.jsx";
import { Button } from "../../atoms/Button/index.jsx";
import { StatusBadge } from "../../atoms/StatusBadge/index.jsx";
import { PersonaLibrary } from "../../organisms/PersonaLibrary/index.jsx";
import { TeamRunForm } from "../../organisms/TeamRunForm/index.jsx";
import { TeamRunDetail } from "../../organisms/TeamRunDetail/index.jsx";
import { SettingsView } from "../../organisms/SettingsView/index.jsx";
import { ArtifactsView } from "../../organisms/ArtifactsView/index.jsx";
import { useConfirm, useToast } from "../../providers/UiProvider/index.jsx";

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
  const confirm = useConfirm();
  const toast = useToast();
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
  const [avatarChoices, setAvatarChoices] = useState([]);
  const [teamRuns, setTeamRuns] = useState([]);
  const [creatingTeamRun, setCreatingTeamRun] = useState(false);
  const [selectedTeamRunId, setSelectedTeamRunId] = useState(null);
  const [teamRunDetail, setTeamRunDetail] = useState(null);
  const [settings, setSettings] = useState(null);
  const [artifacts, setArtifacts] = useState([]);
  const turnHadAgentRef = useRef(false);
  const turnStreamedRef = useRef(false);
  const turnStartRef = useRef(null);
  const selectedTeamRunIdRef = useRef(null);
  const lastConfigAttemptRef = useRef(null);

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
      api.avatarManifest().then(setAvatarChoices);
    } else if (screen === "teams") {
      api.personas().then(setPersonas);
      api.teamRuns().then(setTeamRuns);
    } else if (screen === "settings") {
      api.settings().then(setSettings);
    } else if (screen === "artifacts") {
      api.artifacts().then(setArtifacts);
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
    lastConfigAttemptRef.current = nextConfig;
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

  function handleSessionConfigRetry() {
    if (lastConfigAttemptRef.current) handleSessionConfigChange(lastConfigAttemptRef.current);
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
    toast("Session deleted", "success");
  }

  // Logout moved off the sidebar to match the design; to be surfaced from the Settings screen.
  async function handleLogout() {
    await api.logout();
    window.location.reload();
  }

  async function handleCreatePersona(payload) {
    try {
      const created = await api.createPersona(payload);
      if (!created) {
        toast("Failed to create persona", "error");
        return;
      }
      setPersonas(await api.personas());
      toast("Persona created", "success");
    } catch (_error) {
      toast("Failed to create persona", "error");
    }
  }

  async function handleUpdatePersona(id, payload) {
    try {
      const updated = await api.updatePersona(id, payload);
      if (!updated) {
        toast("Failed to save persona", "error");
        return;
      }
      setPersonas(await api.personas());
      toast("Persona saved", "success");
    } catch (_error) {
      toast("Failed to save persona", "error");
    }
  }

  async function handleDeletePersona(id) {
    try {
      const ok = await api.deletePersona(id);
      if (!ok) {
        toast("Failed to delete persona (it may be in use by a team run)", "error");
        return;
      }
      setPersonas(await api.personas());
      toast("Persona deleted", "success");
    } catch (_error) {
      toast("Failed to delete persona", "error");
    }
  }

  async function handleDeleteTeamRun(id) {
    try {
      const ok = await api.deleteTeamRun(id);
      if (!ok) {
        toast("Failed to delete team run", "error");
        return;
      }
      if (selectedTeamRunId === id) {
        setSelectedTeamRunId(null);
        setTeamRunDetail(null);
      }
      setTeamRuns(await api.teamRuns());
      toast("Team run deleted", "success");
    } catch (_error) {
      toast("Failed to delete team run", "error");
    }
  }

  async function handleCreateTeamRun(payload) {
    try {
      const created = await api.createTeamRun(payload);
      if (!created) {
        toast("Failed to create team run", "error");
        return;
      }
      const started = await api.startTeamRun(created.id);
      if (!started) {
        toast("Failed to start team run", "error");
        return;
      }
      setCreatingTeamRun(false);
      setTeamRuns(await api.teamRuns());
      setSelectedTeamRunId(started.id);
      toast("Team run started", "success");
    } catch (_error) {
      toast("Failed to create team run", "error");
    }
  }

  function handleSelectTeamRun(id) {
    setSelectedTeamRunId(id);
  }

  function handleBackToTeamRuns() {
    setSelectedTeamRunId(null);
    setCreatingTeamRun(false);
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
  const teamRunBadge = teamRuns.filter((run) => run.status === "running" || run.status === "planning").length;

  return (
    <AppShell
      screen={screen}
      teamRunBadge={teamRunBadge}
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
        if (screen === "teams" && nextScreen !== "teams") {
          setSelectedTeamRunId(null);
          setTeamRunDetail(null);
          setCreatingTeamRun(false);
        }
        setScreen(nextScreen);
        setNavOpen(false);
      }}
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
          onSessionConfigRetry={handleSessionConfigRetry}
          onSend={handleSend}
          onSearch={handleSearch}
          onActivate={handleActivate}
          onReset={handleReset}
          onRename={handleRename}
          onDelete={handleDelete}
          onResolveApproval={handleResolveApproval}
        />
      ) : screen === "personas" ? (
        <div className="screen">
          <PersonaLibrary
            personas={personas}
            avatars={avatarChoices}
            onCreate={handleCreatePersona}
            onSave={handleUpdatePersona}
            onDelete={handleDeletePersona}
          />
        </div>
      ) : screen === "teams" ? (
        selectedTeamRunId ? (
          <div className="screen">
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
        ) : creatingTeamRun ? (
          <div className="screen">
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
            <h1 className="headline" style={{ fontSize: 34, marginTop: 10 }}>New Team Run</h1>
            <div className="team-run-new-sub">Personas are snapshotted when the run starts and stay locked for its lifetime.</div>
            <TeamRunForm personas={personas} onSubmit={handleCreateTeamRun} />
          </div>
        ) : (
          <div className="screen team-runs-home">
            <div className="team-runs-home-head">
              <div>
                <h1 className="headline" style={{ fontSize: 34 }}>Team Runs</h1>
                <div className="team-runs-home-sub">Multiple agent sessions, one goal · each agent starts from a persona snapshot</div>
              </div>
              <Button variant="primary" onClick={() => setCreatingTeamRun(true)}>New team run</Button>
            </div>
            <div className="team-run-list">
              {teamRuns.map((run) => (
                <div key={run.id} className="team-run-list-item">
                  <button
                    type="button"
                    className="team-run-list-open"
                    onClick={() => handleSelectTeamRun(run.id)}
                  >
                    <span className="mono team-run-list-id">{run.id}</span>
                    <StatusBadge kind={run.status} />
                    <span className="headline team-run-list-goal">{run.goal}</span>
                  </button>
                  <Button
                    variant="destructive"
                    size="btn-sm"
                    aria-label={`Delete team run ${run.goal}`}
                    onClick={async () => {
                      const ok = await confirm({ title: "DELETE TEAM RUN", message: `Delete team run "${run.goal}"? This cannot be undone.`, confirmLabel: "Delete", danger: true });
                      if (ok) handleDeleteTeamRun(run.id);
                    }}
                  >
                    Delete
                  </Button>
                </div>
              ))}
            </div>
          </div>
        )
      ) : screen === "settings" ? (
        settings ? <SettingsView settings={settings} /> : null
      ) : screen === "artifacts" ? (
        <div className="screen">
          <ArtifactsView artifacts={artifacts} />
        </div>
      ) : (
        <div className="screen">
          <div className="planned">{(activeNav?.label || screen).toUpperCase()} - PLANNED</div>
        </div>
      )}
    </AppShell>
  );
}
