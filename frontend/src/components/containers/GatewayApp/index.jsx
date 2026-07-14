import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../../api/client.js";
import { entryFromSse, normalizeApproval, timelineFromHistory, timelineFromSession } from "../../../lib/timeline.js";
import { nowDateTime } from "../../../lib/time.js";
import { AuthCard } from "../../molecules/AuthCard/index.jsx";
import { AuthTemplate } from "../../templates/AuthTemplate/index.jsx";
import { AppShell } from "../../templates/AppShell/index.jsx";
import { ChatView } from "../../organisms/ChatView/index.jsx";
import { NAV } from "../../organisms/Sidebar/index.jsx";
import { Button } from "../../atoms/Button/index.jsx";
import { PersonaLibrary } from "../../organisms/PersonaLibrary/index.jsx";
import { TeamRunCard } from "../../molecules/TeamRunCard/index.jsx";
import { TeamRunForm } from "../../organisms/TeamRunForm/index.jsx";
import { TeamRunDetail } from "../../organisms/TeamRunDetail/index.jsx";
import { SettingsView } from "../../organisms/SettingsView/index.jsx";
import { ArtifactsView } from "../../organisms/ArtifactsView/index.jsx";
import { JobsView } from "../../organisms/JobsView/index.jsx";
import { SchedulesView } from "../../organisms/SchedulesView/index.jsx";
import { useConfirm, useToast } from "../../providers/UiProvider/index.jsx";

function appendOrReconcileCommand(entries, entry) {
  if (entry.type !== "command") return [...entries, entry];
  const index = entries.findIndex((candidate) => candidate.type === "command" && candidate.key === entry.key);
  if (index < 0) return [...entries, entry];
  const next = entries.slice();
  next[index] = { ...entry, order: entries[index].order ?? entry.order };
  return next;
}

function emptyChatSessionState() {
  return {
    entries: [],
    pendingApproval: null,
    busy: false,
    turnStart: null,
    turnEnd: null,
    turnStreamed: false,
    turnHadAgent: false,
    nextLocalOrder: 0,
    lastServerEventId: null,
    lastLoadedAt: null
  };
}

function updateOneSession(current, sessionId, updater) {
  const base = current[sessionId] || emptyChatSessionState();
  return { ...current, [sessionId]: updater(base) };
}

function appendOrReconcileEntry(entries, entry) {
  if (!entry.key) return appendOrReconcileCommand(entries, entry);
  const index = entries.findIndex((candidate) => candidate.key === entry.key);
  if (index < 0 && entry.type === "agent" && !String(entry.key).startsWith("fallback:")) {
    for (let candidateIndex = entries.length - 1; candidateIndex >= 0; candidateIndex -= 1) {
      const candidate = entries[candidateIndex];
      if (candidate.type === "user") break;
      if (
        candidate.type === "agent"
        && String(candidate.key || "").startsWith("fallback:")
        && candidate.text === entry.text
      ) {
        const next = entries.slice();
        next[candidateIndex] = { ...candidate, ...entry, order: candidate.order ?? entry.order };
        return next;
      }
    }
  }
  if (index < 0) return appendOrReconcileCommand(entries, entry);
  const next = entries.slice();
  next[index] = { ...entries[index], ...entry, order: entries[index].order ?? entry.order };
  return next;
}

function removeEntryByKey(entries, key) {
  return entries.filter((entry) => entry.key !== key);
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

function normalizeEnvironmentTitle(value) {
  return typeof value === "string" ? value.trim() : "";
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
  const [activeSessionId, setActiveSessionId] = useState(null);
  const [sessionStateById, setSessionStateById] = useState({});
  const [navOpen, setNavOpen] = useState(false);
  const [sseState, setSseState] = useState("idle");
  const [personas, setPersonas] = useState([]);
  const [avatarChoices, setAvatarChoices] = useState([]);
  const [teamRuns, setTeamRuns] = useState([]);
  const [creatingTeamRun, setCreatingTeamRun] = useState(false);
  const [runFilter, setRunFilter] = useState("all");
  const [selectedTeamRunId, setSelectedTeamRunId] = useState(null);
  const [teamRunDetail, setTeamRunDetail] = useState(null);
  const [settings, setSettings] = useState(null);
  const [artifacts, setArtifacts] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [schedules, setSchedules] = useState([]);
  const turnStartRef = useRef(null);
  const selectedTeamRunIdRef = useRef(null);
  const lastConfigAttemptRef = useRef(null);
  const activeSessionIdRef = useRef(null);
  const busyRef = useRef(false);
  const seenSseEventIdsRef = useRef(new Set());

  const activeSessionState = activeSessionId
    ? (sessionStateById[activeSessionId] || emptyChatSessionState())
    : emptyChatSessionState();
  const entries = activeSessionState.entries;
  const pendingApproval = activeSessionState.pendingApproval;
  const busy = activeSessionState.busy;
  const turnStart = activeSessionState.turnStart;
  const turnEnd = activeSessionState.turnEnd;
  const turnStreamed = activeSessionState.turnStreamed;
  const environmentTitle = normalizeEnvironmentTitle(status?.environment_title);

  useForceTick(screen === "chat" && busy);

  useEffect(() => {
    document.title = environmentTitle ? `${environmentTitle} · Agent Gateway` : "Agent Gateway";
  }, [environmentTitle]);

  const registeredByPath = useMemo(() => {
    const map = new Map();
    for (const a of artifacts) {
      const key = a.metadata?.original_path;
      if (key) map.set(key, a);
    }
    return map;
  }, [artifacts]);

  const loadApp = useCallback(async () => {
    const [nextStatus, nextSessions, history, nextAgents, nextConfig] = await Promise.all([
      api.getStatus(),
      api.sessions(),
      api.history(),
      api.agents(),
      api.activeSessionConfig()
    ]);
    const sessionId = nextStatus?.session_id || null;
    setActiveSessionId(sessionId);
    activeSessionIdRef.current = sessionId;
    setStatus(withSessionConfigStatus(nextStatus, nextConfig));
    setSessions(nextSessions);
    setAgents(nextAgents);
    setSessionConfig(nextConfig || nextStatus?.session_config || null);
    let nextEntries = timelineFromHistory(history);
    if (sessionId && api.sessionHistory && api.sessionActivity) {
      const [sessionHistory, sessionActivity] = await Promise.all([
        api.sessionHistory(sessionId),
        api.sessionActivity(sessionId)
      ]);
      nextEntries = timelineFromSession(sessionHistory, sessionActivity);
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
  }, []);

  function stampSessionEntry(sessionId, state, entry) {
    const withTime = entry.createdAtMs != null ? entry : { ...entry, createdAtMs: Date.now() };
    if (withTime.order != null) return { entry: withTime, nextLocalOrder: state.nextLocalOrder };
    const order = state.nextLocalOrder;
    return {
      entry: { ...withTime, order },
      nextLocalOrder: order + 1
    };
  }

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
      if (!busyRef.current) setSseState("connected");
    };
    source.onerror = () => setSseState("error");
    source.onmessage = (event) => {
      let parsed;
      try {
        parsed = JSON.parse(event.data);
      } catch (_error) {
        return;
      }
      if (parsed.id != null) {
        const eventId = String(parsed.id);
        if (seenSseEventIdsRef.current.has(eventId)) return;
        seenSseEventIdsRef.current.add(eventId);
      }
      if (parsed.type?.startsWith("team.") && parsed.team_run_id) {
        if (parsed.team_run_id === selectedTeamRunIdRef.current) {
          api.teamRunDetail(parsed.team_run_id).then(setTeamRunDetail);
        }
        return;
      }
      if (parsed.session_id) {
        const sessionId = parsed.session_id;
        if (sessionId === activeSessionIdRef.current) {
          if (parsed.type === "runtime.user_message.started") {
            turnStartRef.current = Date.now();
            busyRef.current = true;
          } else if (parsed.type === "runtime.completed" || parsed.type === "runtime.error" || parsed.type === "runtime.interrupted") {
            busyRef.current = false;
          }
        }
        const entry = entryFromSse(parsed);
        setSessionStateById((current) => updateOneSession(current, sessionId, (state) => {
          const started = parsed.type === "runtime.user_message.started" ? Date.now() : state.turnStart;
          const ended = parsed.type === "runtime.completed" || parsed.type === "runtime.error" || parsed.type === "runtime.interrupted" ? Date.now() : (parsed.type === "runtime.user_message.started" ? null : state.turnEnd);
          const busyNext = parsed.type === "runtime.user_message.started"
            ? true
            : (parsed.type === "runtime.completed" || parsed.type === "runtime.error" || parsed.type === "runtime.interrupted")
              ? false
              : state.busy;
          if (!entry) {
            return {
              ...state,
              busy: busyNext,
              turnStart: started,
              turnEnd: ended,
              lastServerEventId: parsed.id ?? state.lastServerEventId
            };
          }
          const stamped = stampSessionEntry(sessionId, state, entry);
          return {
            ...state,
            entries: appendOrReconcileEntry(state.entries, stamped.entry),
            busy: busyNext,
            turnStart: started,
            turnEnd: ended,
            turnStreamed: true,
            turnHadAgent: state.turnHadAgent || entry.type === "agent",
            nextLocalOrder: stamped.nextLocalOrder,
            lastServerEventId: parsed.id ?? state.lastServerEventId
          };
        }));
        return;
      }
      const entry = entryFromSse(parsed);
      if (!entry) return;
      const sessionId = activeSessionIdRef.current;
      if (!sessionId) return;
      setSessionStateById((current) => updateOneSession(current, sessionId, (state) => {
        const stamped = stampSessionEntry(sessionId, state, entry);
        return {
          ...state,
          entries: appendOrReconcileEntry(state.entries, stamped.entry),
          turnStreamed: true,
          turnHadAgent: state.turnHadAgent || entry.type === "agent",
          nextLocalOrder: stamped.nextLocalOrder,
          lastServerEventId: parsed.id ?? state.lastServerEventId
        };
      }));
    };
    return () => source.close();
  }, [authenticated]);

  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
    busyRef.current = busy;
    turnStartRef.current = turnStart;
  }, [activeSessionId, busy, turnStart]);

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
    } else if (screen === "chat") {
      api.artifacts().then(setArtifacts);
    } else if (screen === "jobs") {
      api.jobs().then(setJobs);
    } else if (screen === "schedules") {
      api.schedules().then(setSchedules);
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

  function clearActiveConversationState() {
    const sessionId = activeSessionIdRef.current;
    activeSessionIdRef.current = null;
    setActiveSessionId(null);
    if (sessionId) {
      setSessionStateById((current) => {
        const next = { ...current };
        delete next[sessionId];
        return next;
      });
    }
    turnStartRef.current = null;
    busyRef.current = false;
    setSessionConfigError("");
  }

  async function maybeAppendArtifact(sessionId) {
    if (!sessionId || !turnStartRef.current) return;
    const artifacts = await api.artifacts();
    const fresh = artifacts.filter((artifact) => (
      artifact.source_session_id === sessionId && Date.parse(artifact.created_at) >= turnStartRef.current - 2000
    ));
    if (fresh.length) {
      setSessionStateById((current) => updateOneSession(current, sessionId, (state) => {
        let nextState = state;
        for (const artifact of fresh) {
          const stamped = stampSessionEntry(sessionId, nextState, { type: "artifact", artifact });
          nextState = {
            ...nextState,
            entries: appendOrReconcileEntry(nextState.entries, stamped.entry),
            nextLocalOrder: stamped.nextLocalOrder
          };
        }
        return nextState;
      }));
    }
  }

  async function postTurn(sessionId, data) {
    setSessionStateById((current) => updateOneSession(current, sessionId, (state) => {
      const pending = data ? normalizeApproval(data.pending_approval) : null;
      const agentEntries = !state.turnHadAgent && data && Array.isArray(data.messages)
        ? data.messages
          .filter((message) => typeof message.content === "string")
          .map((message, index) => ({
            type: "agent",
            text: message.content,
            time: nowDateTime(),
            key: `fallback:${sessionId}:${data?.request_id || data?.last_event_id || "none"}:${index}`
          }))
        : [];
      let nextState = { ...state, pendingApproval: pending };
      for (const entry of agentEntries) {
        const stamped = stampSessionEntry(sessionId, nextState, entry);
        nextState = {
          ...nextState,
          entries: appendOrReconcileEntry(nextState.entries, stamped.entry),
          nextLocalOrder: stamped.nextLocalOrder,
          turnHadAgent: true
        };
      }
      return nextState;
    }));
    await refreshStatusAndSessions();
    await maybeAppendArtifact(sessionId);
  }

  async function handleSend(message) {
    let sessionId = activeSessionIdRef.current || activeSessionId;
    if (!sessionId) {
      const reset = await api.reset();
      sessionId = reset?.session_id || null;
      setActiveSessionId(sessionId);
      activeSessionIdRef.current = sessionId;
    }
    if (!sessionId) return;
    const started = Date.now();
    const userEntryKey = `local:user:${started}`;
    turnStartRef.current = started;
    busyRef.current = true;
    setSessionStateById((current) => updateOneSession(current, sessionId, (state) => {
      const stamped = stampSessionEntry(sessionId, state, {
        type: "user",
        text: message,
        time: nowDateTime(),
        key: userEntryKey
      });
      return {
        ...state,
        entries: [...state.entries, stamped.entry],
        busy: true,
        turnStart: started,
        turnEnd: null,
        turnStreamed: false,
        turnHadAgent: false,
        nextLocalOrder: stamped.nextLocalOrder
      };
    }));
    try {
      const data = await api.sendSessionChat(sessionId, message);
      if (!data) {
        setSessionStateById((current) => updateOneSession(current, sessionId, (state) => ({
          ...state,
          entries: removeEntryByKey(state.entries, userEntryKey)
        })));
        toast("Failed to send message", "error");
        return;
      }
      await postTurn(sessionId, data);
    } finally {
      setSessionStateById((current) => updateOneSession(current, sessionId, (state) => ({
        ...state,
        busy: false,
        turnEnd: Date.now()
      })));
      busyRef.current = false;
    }
  }

  async function handleInterrupt() {
    const sessionId = activeSessionIdRef.current;
    if (!sessionId || !busyRef.current) return;
    await api.interruptSession(sessionId);
  }

  async function handleResolveApproval(action) {
    const sessionId = activeSessionId;
    if (!sessionId || !pendingApproval || busy) return;
    const started = turnStartRef.current || Date.now();
    turnStartRef.current = started;
    busyRef.current = true;
    setSessionStateById((current) => updateOneSession(current, sessionId, (state) => ({
      ...state,
      busy: true,
      turnStart: started,
      turnEnd: null,
      turnStreamed: true,
      turnHadAgent: false
    })));
    try {
      const data = action === "approve"
        ? await api.approveSession(sessionId, pendingApproval.id)
        : await api.denySession(sessionId, pendingApproval.id);
      if (!data) {
        toast("Failed to resolve approval", "error");
        return;
      }
      await postTurn(sessionId, data);
    } finally {
      setSessionStateById((current) => updateOneSession(current, sessionId, (state) => ({
        ...state,
        busy: false,
        turnEnd: Date.now()
      })));
      busyRef.current = false;
    }
  }

  async function handleSearch(query) {
    setSessions(query ? await api.searchSessions(query) : await api.sessions());
  }

  async function handleActivate(id) {
    const data = await api.activate(id);
    if (!data) return;
    const sessionId = data.session_id || id;
    setActiveSessionId(sessionId);
    activeSessionIdRef.current = sessionId;
    setSessionConfigError("");
    const [historyEvents, activityEvents, nextSessionStatus] = await Promise.all([
      api.sessionHistory(sessionId),
      api.sessionActivity(sessionId),
      api.sessionStatus(sessionId)
    ]);
    const nextEntries = timelineFromSession(historyEvents, activityEvents);
    setSessionStateById((current) => updateOneSession(current, sessionId, (state) => ({
      ...state,
      entries: nextEntries.length ? nextEntries : state.entries,
      pendingApproval: normalizeApproval(nextSessionStatus?.pending_approval),
      busy: nextSessionStatus?.status === "running",
      turnStart: nextSessionStatus?.status === "running" ? state.turnStart : null,
      turnEnd: nextSessionStatus?.status === "running" ? null : state.turnEnd,
      nextLocalOrder: Math.max(state.nextLocalOrder, nextEntries.length),
      lastLoadedAt: Date.now()
    })));
    turnStartRef.current = nextSessionStatus?.status === "running" ? turnStartRef.current : null;
    busyRef.current = nextSessionStatus?.status === "running";
    await refreshStatusAndSessions();
  }

  async function handleReset() {
    const reset = await api.reset();
    const sessionId = reset?.session_id || null;
    if (sessionId) {
      setActiveSessionId(sessionId);
      activeSessionIdRef.current = sessionId;
      setSessionStateById((current) => updateOneSession(current, sessionId, (state) => ({
        ...emptyChatSessionState(),
        entries: state.entries,
        nextLocalOrder: state.entries.length,
        lastLoadedAt: Date.now()
      })));
      turnStartRef.current = null;
      busyRef.current = false;
      setSessionConfigError("");
    } else {
      clearActiveConversationState();
    }
    await refreshStatusAndSessions();
  }

  async function handleRename(id, title) {
    await api.renameSession(id, title);
    setSessions(await api.sessions());
  }

  async function handleDelete(id) {
    const deleted = await api.deleteSession(id);
    if (!deleted) {
      toast("Failed to delete session", "error");
      return;
    }
    if (deleted?.active_session_id == null && status?.session_id === id) {
      clearActiveConversationState();
      await refreshStatusAndSessions();
    } else {
      setSessions(await api.sessions());
    }
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

  async function handleCreateSchedule(payload) {
    try {
      const created = await api.createSchedule(payload);
      if (!created) {
        toast("Failed to create schedule", "error");
        return;
      }
      setSchedules(await api.schedules());
      toast("Schedule created", "success");
    } catch (_error) {
      toast("Failed to create schedule", "error");
    }
  }

  async function handlePauseSchedule(id) {
    try {
      await api.pauseSchedule(id);
      setSchedules(await api.schedules());
    } catch (_error) {
      toast("Failed to pause schedule", "error");
    }
  }

  async function handleResumeSchedule(id) {
    try {
      await api.resumeSchedule(id);
      setSchedules(await api.schedules());
    } catch (_error) {
      toast("Failed to resume schedule", "error");
    }
  }

  async function handleDeleteSchedule(id) {
    try {
      const ok = await api.deleteSchedule(id);
      if (!ok) {
        toast("Failed to delete schedule", "error");
        return;
      }
      setSchedules(await api.schedules());
      toast("Schedule deleted", "success");
    } catch (_error) {
      toast("Failed to delete schedule", "error");
    }
  }

  async function handleRunScheduleNow(id) {
    try {
      await api.runScheduleNow(id);
      toast("실행을 시작했습니다", "success");
    } catch (_error) {
      toast("Failed to run schedule", "error");
    }
  }

  function handleSelectTeamRun(id) {
    setSelectedTeamRunId(id);
  }

  async function handleAddWork(instruction) {
    if (!selectedTeamRunId || !instruction.trim()) return false;
    try {
      const result = await api.addWork(selectedTeamRunId, instruction.trim());
      if (!result) {
        toast("Failed to add work", "error");
        return false;
      }
      setTeamRunDetail(await api.teamRunDetail(selectedTeamRunId));
      toast("추가 업무를 전달했습니다", "success");
      return true;
    } catch (_error) {
      toast("Failed to add work", "error");
      return false;
    }
  }

  async function handleResumeTeamRun() {
    if (!selectedTeamRunId) return false;
    const accepted = await confirm({
      title: "RESUME TEAM RUN",
      message: "Resume pending work for this interrupted team run? Completed tasks will be kept.",
      confirmLabel: "Resume"
    });
    if (!accepted) return false;
    try {
      const result = await api.resumeTeamRun(selectedTeamRunId);
      if (!result) {
        toast("Failed to resume team run", "error");
        return false;
      }
      const [detail, runs] = await Promise.all([
        api.teamRunDetail(selectedTeamRunId),
        api.teamRuns()
      ]);
      setTeamRunDetail(detail);
      setTeamRuns(runs);
      toast("팀 작업을 재개했습니다", "success");
      return true;
    } catch (_error) {
      toast("Failed to resume team run", "error");
      return false;
    }
  }

  async function handleRetryTeamTask(taskId) {
    if (!selectedTeamRunId) return false;
    const task = teamRunDetail?.tasks?.find((item) => item.id === taskId);
    const accepted = await confirm({
      title: "RETRY FAILED TASK",
      message: "Queue “" + (task?.title || "this task")
        + "” for retry? You will need to resume the team run afterward.",
      confirmLabel: "Retry"
    });
    if (!accepted) return false;
    try {
      const result = await api.retryTeamTask(selectedTeamRunId, taskId);
      if (!result) {
        toast("Failed to retry task", "error");
        return false;
      }
      const [detail, runs] = await Promise.all([
        api.teamRunDetail(selectedTeamRunId),
        api.teamRuns()
      ]);
      setTeamRunDetail(detail);
      setTeamRuns(runs);
      toast("실패한 업무를 재시도 대기열에 추가했습니다", "success");
      return true;
    } catch (_error) {
      toast("Failed to retry task", "error");
      return false;
    }
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
      environmentTitle={environmentTitle}
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
          onInterrupt={handleInterrupt}
          registeredByPath={registeredByPath}
          onArtifactChange={() => api.artifacts().then(setArtifacts)}
        />
      ) : screen === "personas" ? (
        <div className="screen">
          <PersonaLibrary
            personas={personas}
            avatars={avatarChoices}
            agents={agents}
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
            <TeamRunDetail
              detail={teamRunDetail}
              onAddWork={handleAddWork}
              onResume={handleResumeTeamRun}
              onRetryTask={handleRetryTeamTask}
            />
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
            <div className="team-runs-filter">
              <span className="mono team-runs-filter-k">STATUS</span>
              {[["all", "All"], ["running", "Running"], ["completed", "Completed"], ["failed", "Failed"]].map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  className={`chip${runFilter === key ? " chip-active" : ""}`}
                  aria-pressed={runFilter === key}
                  onClick={() => setRunFilter(key)}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="team-run-list">
              {teamRuns
                .filter((run) => {
                  if (runFilter === "all") return true;
                  if (runFilter === "running") return run.status === "running" || run.status === "planning";
                  if (runFilter === "completed") return run.status === "completed" || run.status === "completed_with_failures";
                  if (runFilter === "failed") return run.status === "failed";
                  return true;
                })
                .map((run) => (
                  <TeamRunCard key={run.id} run={run} onOpen={handleSelectTeamRun} />
                ))}
            </div>
          </div>
        )
      ) : screen === "settings" ? (
        settings ? <SettingsView settings={settings} /> : null
      ) : screen === "artifacts" ? (
        <div className="screen">
          <ArtifactsView artifacts={artifacts} onChange={() => api.artifacts().then(setArtifacts)} />
        </div>
      ) : screen === "jobs" ? (
        <div className="screen">
          <JobsView jobs={jobs} onLoadEvents={api.jobEvents} />
        </div>
      ) : screen === "schedules" ? (
        <div className="screen">
          <SchedulesView
            schedules={schedules}
            onCreate={handleCreateSchedule}
            onPause={handlePauseSchedule}
            onResume={handleResumeSchedule}
            onDelete={handleDeleteSchedule}
            onRunNow={handleRunScheduleNow}
          />
        </div>
      ) : (
        <div className="screen">
          <div className="planned">{(activeNav?.label || screen).toUpperCase()} - PLANNED</div>
        </div>
      )}
    </AppShell>
  );
}
