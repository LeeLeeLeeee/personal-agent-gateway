import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import { entryFromSse, normalizeApproval, timelineFromSession } from "../lib/timeline.js";
import { nowDateTime } from "../lib/time.js";
import { emptyChatSessionState, withSessionConfigStatus } from "./sessionState.js";

function appendOrReconcileCommand(entries, entry) {
  if (entry.type !== "command") return [...entries, entry];
  const index = entries.findIndex(
    (candidate) => candidate.type === "command" && candidate.key === entry.key
  );
  if (index < 0) return [...entries, entry];
  const next = entries.slice();
  next[index] = { ...entry, order: entries[index].order ?? entry.order };
  return next;
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
        next[candidateIndex] = {
          ...candidate,
          ...entry,
          order: candidate.order ?? entry.order
        };
        return next;
      }
    }
  }
  if (index < 0) return appendOrReconcileCommand(entries, entry);
  const next = entries.slice();
  next[index] = {
    ...entries[index],
    ...entry,
    order: entries[index].order ?? entry.order
  };
  return next;
}

function removeEntryByKey(entries, key) {
  return entries.filter((entry) => entry.key !== key);
}

function stampSessionEntry(state, entry) {
  const withTime = entry.createdAtMs != null
    ? entry
    : { ...entry, createdAtMs: Date.now() };
  if (withTime.order != null) {
    return { entry: withTime, nextLocalOrder: state.nextLocalOrder };
  }
  const order = state.nextLocalOrder;
  return { entry: { ...withTime, order }, nextLocalOrder: order + 1 };
}

export function useSessionController({
  authenticated,
  toast,
  status,
  setStatus,
  setSessions,
  sessionConfig,
  setSessionConfig,
  activeSessionId,
  setActiveSessionId,
  sessionStateById,
  setSessionStateById,
  activeSessionIdRef,
  busyRef,
  turnStartRef,
  lastConfigAttemptRef,
  setScreenError,
  onTeamEvent,
  onHookEvent
}) {
  const [sessionConfigError, setSessionConfigError] = useState("");
  const [sseState, setSseState] = useState("idle");
  const seenSseEventIdsRef = useRef(new Set());
  const activeSessionState = activeSessionId
    ? (sessionStateById[activeSessionId] || emptyChatSessionState())
    : emptyChatSessionState();

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
        onTeamEvent(parsed);
        return;
      }
      if (parsed.type === "hook.run.updated") {
        onHookEvent(parsed);
        return;
      }
      if (parsed.session_id) {
        const sessionId = parsed.session_id;
        if (sessionId === activeSessionIdRef.current) {
          if (parsed.type === "runtime.user_message.started") {
            turnStartRef.current = Date.now();
            busyRef.current = true;
          } else if ([
            "runtime.completed",
            "runtime.error",
            "runtime.interrupted"
          ].includes(parsed.type)) {
            busyRef.current = false;
          }
        }
        const entry = entryFromSse(parsed);
        setSessionStateById((current) => updateOneSession(current, sessionId, (state) => {
          const started = parsed.type === "runtime.user_message.started"
            ? Date.now()
            : state.turnStart;
          const terminal = [
            "runtime.completed",
            "runtime.error",
            "runtime.interrupted"
          ].includes(parsed.type);
          const ended = terminal
            ? Date.now()
            : (parsed.type === "runtime.user_message.started" ? null : state.turnEnd);
          const busyNext = parsed.type === "runtime.user_message.started"
            ? true
            : (terminal ? false : state.busy);
          if (!entry) {
            return {
              ...state,
              busy: busyNext,
              turnStart: started,
              turnEnd: ended,
              lastServerEventId: parsed.id ?? state.lastServerEventId
            };
          }
          const stamped = stampSessionEntry(state, entry);
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
        const stamped = stampSessionEntry(state, entry);
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
  }, [authenticated, activeSessionIdRef, busyRef, onTeamEvent, onHookEvent, setSessionStateById, turnStartRef]);

  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
    busyRef.current = activeSessionState.busy;
    turnStartRef.current = activeSessionState.turnStart;
  }, [
    activeSessionId,
    activeSessionIdRef,
    activeSessionState.busy,
    activeSessionState.turnStart,
    busyRef,
    turnStartRef
  ]);

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
    if (lastConfigAttemptRef.current) {
      handleSessionConfigChange(lastConfigAttemptRef.current);
    }
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
      artifact.source_session_id === sessionId
      && Date.parse(artifact.created_at) >= turnStartRef.current - 2000
    ));
    if (fresh.length) {
      setSessionStateById((current) => updateOneSession(current, sessionId, (state) => {
        let nextState = state;
        for (const artifact of fresh) {
          const stamped = stampSessionEntry(nextState, { type: "artifact", artifact });
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
        const stamped = stampSessionEntry(nextState, entry);
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
      const stamped = stampSessionEntry(state, {
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
    } catch (error) {
      setSessionStateById((current) => updateOneSession(current, sessionId, (state) => ({
        ...state,
        entries: removeEntryByKey(state.entries, userEntryKey)
      })));
      setScreenError(error);
      toast("Failed to send message", "error");
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
    const pendingApproval = activeSessionState.pendingApproval;
    if (!activeSessionId || !pendingApproval || activeSessionState.busy) return;
    const started = turnStartRef.current || Date.now();
    turnStartRef.current = started;
    busyRef.current = true;
    setSessionStateById((current) => updateOneSession(current, activeSessionId, (state) => ({
      ...state,
      busy: true,
      turnStart: started,
      turnEnd: null,
      turnStreamed: true,
      turnHadAgent: false
    })));
    try {
      const data = action === "approve"
        ? await api.approveSession(activeSessionId, pendingApproval.id)
        : await api.denySession(activeSessionId, pendingApproval.id);
      if (!data) {
        toast("Failed to resolve approval", "error");
        return;
      }
      await postTurn(activeSessionId, data);
    } catch (error) {
      setScreenError(error);
      toast("Failed to resolve approval", "error");
    } finally {
      setSessionStateById((current) => updateOneSession(current, activeSessionId, (state) => ({
        ...state,
        busy: false,
        turnEnd: Date.now()
      })));
      busyRef.current = false;
    }
  }

  async function handleSearch(query) {
    try {
      setSessions(query ? await api.searchSessions(query) : await api.sessions());
    } catch (error) {
      setScreenError(error);
    }
  }

  async function handleActivate(id) {
    let data;
    try {
      data = await api.activate(id);
    } catch (error) {
      setScreenError(error);
      return;
    }
    if (!data) return;
    const sessionId = data.session_id || id;
    setActiveSessionId(sessionId);
    activeSessionIdRef.current = sessionId;
    setSessionConfigError("");
    const [historyResult, activityResult, statusResult] = await Promise.allSettled([
      api.sessionHistory(sessionId),
      api.sessionActivity(sessionId),
      api.sessionStatus(sessionId)
    ]);
    const historyEvents = historyResult.status === "fulfilled" ? historyResult.value : [];
    const activityEvents = activityResult.status === "fulfilled" ? activityResult.value : [];
    const nextSessionStatus = statusResult.status === "fulfilled" ? statusResult.value : null;
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
    turnStartRef.current = nextSessionStatus?.status === "running"
      ? turnStartRef.current
      : null;
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
    let deleted;
    try {
      deleted = await api.deleteSession(id);
    } catch (error) {
      setScreenError(error);
      toast("Failed to delete session", "error");
      return;
    }
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

  return {
    sessionConfig,
    sessionConfigError,
    sseState,
    entries: activeSessionState.entries,
    pendingApproval: activeSessionState.pendingApproval,
    busy: activeSessionState.busy,
    turnStart: activeSessionState.turnStart,
    turnEnd: activeSessionState.turnEnd,
    turnStreamed: activeSessionState.turnStreamed,
    refreshStatusAndSessions,
    clearActiveConversationState,
    handleSessionConfigChange,
    handleSessionConfigRetry,
    handleSend,
    handleInterrupt,
    handleResolveApproval,
    handleSearch,
    handleActivate,
    handleReset,
    handleRename,
    handleDelete
  };
}
