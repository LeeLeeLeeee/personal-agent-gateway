import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, apiErrorAction } from "../../../api/client.js";
import { useGatewayBootstrap } from "../../../hooks/useGatewayBootstrap.js";
import { useSessionController } from "../../../hooks/useSessionController.js";
import { useTeamRunController } from "../../../hooks/useTeamRunController.js";
import {
  disableBrowserNotifications,
  enableBrowserNotifications,
  getBrowserNotificationState,
  showTeamRunNotification
} from "../../../lib/browserNotification.js";
import { AuthCard } from "../../molecules/AuthCard/index.jsx";
import { AuthTemplate } from "../../templates/AuthTemplate/index.jsx";
import { AppShell } from "../../templates/AppShell/index.jsx";
import { ChatView } from "../../organisms/ChatView/index.jsx";
import { NAV } from "../../organisms/Sidebar/index.jsx";
import { Button } from "../../atoms/Button/index.jsx";
import { PersonaLibrary } from "../../organisms/PersonaLibrary/index.jsx";
import { TeamsView } from "../../organisms/TeamsView/index.jsx";
import { TeamRunCard } from "../../molecules/TeamRunCard/index.jsx";
import { TeamPicker } from "../../organisms/TeamPicker/index.jsx";
import { TeamRunDetail } from "../../organisms/TeamRunDetail/index.jsx";
import { RulesView } from "../../organisms/RulesView/index.jsx";
import { SettingsView } from "../../organisms/SettingsView/index.jsx";
import { ArtifactsView } from "../../organisms/ArtifactsView/index.jsx";
import { JobsView } from "../../organisms/JobsView/index.jsx";
import { SchedulesView } from "../../organisms/SchedulesView/index.jsx";
import { OperationsView } from "../../organisms/OperationsView/index.jsx";
import { HooksView } from "../../organisms/HooksView/index.jsx";
import { useConfirm, useToast } from "../../providers/UiProvider/index.jsx";

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
  const [screen, setScreen] = useState("chat");
  const [sessionStateById, setSessionStateById] = useState({});
  const [navOpen, setNavOpen] = useState(false);
  const [personas, setPersonas] = useState([]);
  const [avatarChoices, setAvatarChoices] = useState([]);
  const [teams, setTeams] = useState([]);
  const [settings, setSettings] = useState(null);
  const [notificationState, setNotificationState] = useState(getBrowserNotificationState);
  const [authSessions, setAuthSessions] = useState([]);
  const [rules, setRules] = useState(null);
  const [artifacts, setArtifacts] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [schedules, setSchedules] = useState([]);
  const [hooks, setHooks] = useState([]);
  const [hookRuns, setHookRuns] = useState([]);
  const [openHookRunsId, setOpenHookRunsId] = useState(null);
  const [hooksBadge, setHooksBadge] = useState(0);
  const hooksRef = useRef([]);
  const notificationStateRef = useRef(notificationState);
  const notifiedTeamRunsRef = useRef(new Set());
  const screenRef = useRef("chat");
  const openHookRunsIdRef = useRef(null);
  const [focusedJobId, setFocusedJobId] = useState(null);
  const [focusedScheduleId, setFocusedScheduleId] = useState(null);
  const [operations, setOperations] = useState(null);
  const [operationsError, setOperationsError] = useState(null);
  const [operationsLoading, setOperationsLoading] = useState(false);
  const [screenError, setScreenError] = useState(null);
  const [screenReloadKey, setScreenReloadKey] = useState(0);
  const turnStartRef = useRef(null);
  const lastConfigAttemptRef = useRef(null);
  const activeSessionIdRef = useRef(null);
  const busyRef = useRef(false);
  const {
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
  } = useGatewayBootstrap({ activeSessionIdRef, setSessionStateById });

  const {
    teamRuns,
    setTeamRuns,
    creatingTeamRun,
    setCreatingTeamRun,
    runFilter,
    setRunFilter,
    selectedTeamRunId,
    setSelectedTeamRunId,
    teamRunDetail,
    teamRunDocuments,
    handleTeamEvent,
    handleCreateTeamRun,
    handleAddWork,
    handleResumeTeamRun,
    handleAnswerTeamDecision,
    handleCancelTeamRun,
    handleRetryTeamTask,
    handleDeleteTeamRun,
    handleSelectTeamRun,
    handleBackToTeamRuns,
    clearTeamRunView
  } = useTeamRunController({ toast, confirm, setScreenError });

  const handleEnableNotifications = useCallback(async () => {
    const next = await enableBrowserNotifications();
    notificationStateRef.current = next;
    setNotificationState(next);
  }, []);

  const handleDisableNotifications = useCallback(() => {
    const next = disableBrowserNotifications();
    notificationStateRef.current = next;
    setNotificationState(next);
  }, []);

  const handleTeamEventWithNotification = useCallback((event) => {
    if (
      notificationStateRef.current.enabled
      && ["team.run.completed", "team.run.failed", "team.run.input_requested"].includes(event.type)
    ) {
      const key = `${event.team_run_id}:${event.type}:${
        event.decision_request_id || event.run?.finished_at || "event"
      }`;
      if (!notifiedTeamRunsRef.current.has(key)) {
        const notification = showTeamRunNotification(event, (teamRunId) => {
          setSelectedTeamRunId(teamRunId);
          setScreen("teams");
        });
        if (notification) notifiedTeamRunsRef.current.add(key);
      }
    }
    handleTeamEvent(event);
  }, [handleTeamEvent, setSelectedTeamRunId]);

  useEffect(() => { hooksRef.current = hooks; }, [hooks]);
  useEffect(() => { screenRef.current = screen; }, [screen]);
  useEffect(() => { openHookRunsIdRef.current = openHookRunsId; }, [openHookRunsId]);

  const handleHookEvent = useCallback(async (event) => {
    const hook = hooksRef.current.find((item) => item.id === event.hook_id);
    const name = hook?.name || event.hook_id;
    if (event.status === "succeeded" || event.status === "failed") {
      toast(
        `Hook "${name}": ${event.status === "succeeded" ? "완료" : "실패"}`,
        event.status === "succeeded" ? "success" : "error"
      );
      if (screenRef.current !== "hooks") setHooksBadge((count) => count + 1);
    }
    const refreshes = [
      api.listHooks().then(setHooks)
    ];
    if (event.hook_id === openHookRunsIdRef.current) {
      refreshes.push(api.listHookRuns(event.hook_id).then(setHookRuns));
    }
    await Promise.allSettled(refreshes);
  }, [toast]);

  const {
    sessionConfigError,
    sseState,
    entries,
    pendingApproval,
    busy,
    turnStart,
    turnEnd,
    turnStreamed,
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
  } = useSessionController({
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
    onTeamEvent: handleTeamEventWithNotification,
    onHookEvent: handleHookEvent
  });

  const loadOperations = useCallback(async () => {
    setOperationsLoading(true);
    try {
      const next = await api.operations();
      setOperations(next);
      setOperationsError(null);
    } catch (error) {
      setOperationsError(error);
    } finally {
      setOperationsLoading(false);
    }
  }, []);

  useForceTick(screen === "chat" && busy);

  const registeredByPath = useMemo(() => {
    const map = new Map();
    for (const a of artifacts) {
      const key = a.metadata?.original_path;
      if (key) map.set(key, a);
    }
    return map;
  }, [artifacts]);

  useEffect(() => {
    if (!authenticated) return;
    setScreenError(null);
    const load = (promise, setter) => promise.then(setter).catch(setScreenError);
    if (screen === "personas") {
      load(api.personas(), setPersonas);
      load(api.avatarManifest(), setAvatarChoices);
    } else if (screen === "teams") {
      load(api.teamRuns(), setTeamRuns);
      load(api.teams(), setTeams);
      load(api.settings(), setSettings);
    } else if (screen === "team-admin") {
      load(api.teams(), setTeams);
      load(api.personas(), setPersonas);
    } else if (screen === "rules") {
      load(api.teams(), setTeams);
      load(api.rules(), setRules);
    } else if (screen === "settings") {
      load(api.settings(), setSettings);
      load(api.authSessions(), setAuthSessions);
    } else if (screen === "artifacts") {
      load(api.artifacts(), setArtifacts);
    } else if (screen === "chat") {
      load(api.artifacts(), setArtifacts);
      api.personas().then(setPersonas).catch(() => {});
    } else if (screen === "jobs") {
      load(api.jobs(), setJobs);
    } else if (screen === "schedules") {
      load(api.schedules(), setSchedules);
      load(api.settings(), setSettings);
    } else if (screen === "hooks") {
      load(api.listHooks(), setHooks);
      load(api.teamRuns(), setTeamRuns);
      api.personas().then(setPersonas).catch(() => {});
      setHooksBadge(0);
    } else if (screen === "operations") {
      loadOperations();
    }
  }, [screen, authenticated, loadOperations, screenReloadKey]);

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

  async function handleCreateTeam(payload) {
    try {
      const created = await api.createTeam(payload);
      if (!created) { toast("Failed to create team", "error"); return null; }
      setTeams(await api.teams());
      toast("Team created", "success");
      return created;
    } catch (_error) { toast("Failed to create team", "error"); return null; }
  }
  async function handleUpdateTeam(id, payload) {
    try {
      const updated = await api.updateTeam(id, payload);
      if (!updated) { toast("Failed to save team", "error"); return null; }
      setTeams(await api.teams());
      toast("Team saved", "success");
      return updated;
    } catch (_error) { toast("Failed to save team", "error"); return null; }
  }
  async function handleDeleteTeam(id) {
    const ok = await confirm({ title: "DELETE TEAM", message: "Delete this team? Running snapshots are unaffected.", confirmLabel: "Delete", danger: true });
    if (!ok) return;
    const done = await api.deleteTeam(id);
    if (!done) { toast("Failed to delete team", "error"); return; }
    setTeams(await api.teams());
    toast("Team deleted", "success");
  }

  async function handleSaveGlobalRules(payload) {
    const saved = await api.updateGlobalRules(payload);
    if (!saved) { toast("Failed to save rules", "error"); return null; }
    setRules(await api.rules()); toast("Rules saved", "success"); return saved;
  }
  async function handleSavePersonaBaselineRules(payload) {
    const saved = await api.updatePersonaBaselineRules(payload);
    if (!saved) { toast("Failed to save rules", "error"); return null; }
    setRules(await api.rules()); toast("Rules saved", "success"); return saved;
  }
  async function handleSaveTeamRules(teamId, payload) {
    const saved = await api.updateTeamRules(teamId, payload);
    if (!saved) { toast("Failed to save rules", "error"); return null; }
    setRules(await api.rules()); toast("Rules saved", "success"); return saved;
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
      const result = await api.runScheduleNow(id);
      if (!result?.job?.id) {
        toast("Failed to run schedule", "error");
        return;
      }
      const [nextJobs, nextSchedules] = await Promise.all([
        api.jobs(),
        api.schedules()
      ]);
      setJobs(nextJobs);
      setSchedules(nextSchedules);
      setFocusedJobId(result.job.id);
      setScreen("jobs");
      toast("실행을 시작했습니다", "success");
    } catch (_error) {
      toast("Failed to run schedule", "error");
    }
  }

  async function handleCreateHook(body) {
    try {
      const created = await api.createHook(body);
      if (!created) { toast("Failed to create hook", "error"); return; }
      setHooks(await api.listHooks());
      toast("Hook created", "success");
    } catch (_error) { toast("Failed to create hook", "error"); }
  }
  async function handleToggleHook(id, enabled) {
    try {
      await api.updateHook(id, { enabled });
      setHooks(await api.listHooks());
    } catch (_error) { toast("Failed to update hook", "error"); }
  }
  async function handleDeleteHook(id) {
    try {
      const ok = await api.deleteHook(id);
      if (!ok) { toast("Failed to delete hook", "error"); return; }
      setHooks(await api.listHooks());
      if (openHookRunsId === id) { setOpenHookRunsId(null); setHookRuns([]); }
      toast("Hook deleted", "success");
    } catch (_error) { toast("Failed to delete hook", "error"); }
  }
  async function handleRunHookNow(id) {
    try {
      await api.runHookNow(id);
      setHooks(await api.listHooks());
      if (openHookRunsId === id) setHookRuns(await api.listHookRuns(id));
      toast("폴링을 실행했습니다", "success");
    } catch (_error) { toast("Failed to run hook", "error"); }
  }
  async function handleOpenHookRuns(id) {
    setOpenHookRunsId(id);
    try { setHookRuns(await api.listHookRuns(id)); } catch (error) { setScreenError(error); }
  }
  function handleCloseHookRuns() { setOpenHookRunsId(null); setHookRuns([]); }
  function handleTestHookConnection(body) { return api.testHookConnection(body); }
  function handleOpenHookTeamRun(teamRunId) {
    handleCloseHookRuns();
    setSelectedTeamRunId(teamRunId);
    setScreen("teams");
    setNavOpen(false);
  }

  async function handleRetryJob(id) {
    try {
      const retried = await api.retryJob(id);
      if (!retried) {
        toast("Failed to retry job", "error");
        return null;
      }
      setJobs(await api.jobs());
      toast("재시도 Job을 생성했습니다", "success");
      return retried;
    } catch (_error) {
      toast("Failed to retry job", "error");
      return null;
    }
  }

  async function refreshOperationsDomains() {
    const [nextJobs, nextSchedules, nextRuns, nextSettings] = await Promise.all([
      api.jobs(),
      api.schedules(),
      api.teamRuns(),
      api.settings()
    ]);
    setJobs(nextJobs);
    setSchedules(nextSchedules);
    setTeamRuns(nextRuns);
    setSettings(nextSettings);
    await loadOperations();
  }

  async function handleEmergencyStop() {
    try {
      await api.emergencyStop();
      await refreshOperationsDomains();
      toast("모든 실행 intake를 중단했습니다", "warning");
    } catch (error) {
      setOperationsError(error);
    }
  }

  async function handleResumeIntake() {
    try {
      await api.resumeIntake();
      await loadOperations();
      toast("실행 intake를 재개했습니다", "success");
    } catch (error) {
      setOperationsError(error);
    }
  }

  async function handleCreateBackup() {
    try {
      await api.createBackup();
      await loadOperations();
      toast("Backup을 생성했습니다", "success");
    } catch (error) {
      setOperationsError(error);
    }
  }

  async function handleVerifyBackup(id) {
    try {
      await api.verifyBackup(id);
      await loadOperations();
      toast("Backup 검증을 통과했습니다", "success");
    } catch (error) {
      setOperationsError(error);
    }
  }

  async function handleOpenOperationTarget(target) {
    if (target.screen === "chat" && target.session_id) {
      await handleActivate(target.session_id);
    } else if (target.screen === "teams" && target.team_run_id) {
      setSelectedTeamRunId(target.team_run_id);
    } else if (target.screen === "jobs" && target.job_id) {
      setFocusedJobId(target.job_id);
    } else if (target.screen === "schedules" && target.schedule_id) {
      setFocusedScheduleId(target.schedule_id);
    }
    setScreen(target.screen);
  }

  async function handleResumeOperationItem(item) {
    try {
      if (item.domain === "team_run") {
        await api.resumeTeamRun(item.id);
      } else if (item.domain === "schedule") {
        await api.resumeSchedule(item.id);
      }
      await refreshOperationsDomains();
    } catch (error) {
      setOperationsError(error);
    }
  }

  async function handleRetryOperationItem(item) {
    const retried = await handleRetryJob(item.id);
    if (retried) await loadOperations();
  }

  function handleOperationsRelogin() {
    setAuthenticated(false);
    setAuthStage("login");
    setScreen("chat");
  }

  async function handleAccessModeChange(mode, confirmed) {
    try {
      await api.setAccessMode(mode, confirmed);
      setSettings(await api.settings());
      toast(`Access mode changed to ${mode}`, "success");
    } catch (error) {
      setScreenError(error);
    }
  }

  async function handleRevokeAuthSession(id, current) {
    try {
      await api.revokeAuthSession(id);
      if (current) {
        handleOperationsRelogin();
        return;
      }
      setAuthSessions(await api.authSessions());
      toast("Session revoked", "success");
    } catch (error) {
      setScreenError(error);
    }
  }

  async function handleRevokeAllAuthSessions() {
    try {
      await api.revokeAllAuthSessions();
      handleOperationsRelogin();
    } catch (error) {
      setScreenError(error);
    }
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
  const screenErrorAction = apiErrorAction(screenError);

  return (
    <AppShell
      screen={screen}
      teamRunBadge={teamRunBadge}
      hooksBadge={hooksBadge}
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
          clearTeamRunView();
        }
        setScreen(nextScreen);
        setNavOpen(false);
      }}
    >
      {screenError && screen !== "operations" ? (
        <div className="operations-error" role="alert">
          <div>{typeof screenError.detail === "string" ? screenError.detail : "Request failed"}</div>
          <div className="operations-error-preservation">Existing local data was not cleared.</div>
          {screenError.correlationId ? (
            <div className="operations-correlation mono">
              CORRELATION · {screenError.correlationId}
              <button
                type="button"
                className="btn btn-sm"
                aria-label="Copy correlation ID"
                onClick={() => navigator.clipboard?.writeText(screenError.correlationId)}
              >
                Copy
              </button>
            </div>
          ) : null}
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => {
              if (screenErrorAction === "relogin") {
                handleOperationsRelogin();
              } else {
                setScreenReloadKey((value) => value + 1);
              }
            }}
          >
            {screenErrorAction === "relogin" ? "Sign in again" : "Retry request"}
          </button>
        </div>
      ) : null}
      {screen === "chat" ? (
        <ChatView
          agents={agents}
          personas={personas}
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
          onArtifactChange={() => api.artifacts().then(setArtifacts).catch(setScreenError)}
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
              documents={teamRunDocuments}
              onLoadDocument={(path) => api.teamDocumentContent(selectedTeamRunId, path)}
              onAddWork={handleAddWork}
              onResume={handleResumeTeamRun}
              onAnswerDecision={handleAnswerTeamDecision}
              onCancel={handleCancelTeamRun}
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
            <TeamPicker teams={teams} runtime={settings} onStart={handleCreateTeamRun} />
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
                  <div className="team-run-list-item" key={run.id}>
                    <TeamRunCard run={run} onOpen={handleSelectTeamRun} />
                    <Button variant="destructive" size="btn-sm" onClick={() => handleDeleteTeamRun(run.id)}>Delete</Button>
                  </div>
                ))}
            </div>
          </div>
        )
      ) : screen === "team-admin" ? (
        <div className="screen">
          <TeamsView
            teams={teams}
            personas={personas}
            onCreate={handleCreateTeam}
            onUpdate={handleUpdateTeam}
            onDelete={handleDeleteTeam}
          />
        </div>
      ) : screen === "rules" ? (
        <div className="screen">
          {rules ? (
            <RulesView
              rules={rules}
              teams={teams}
              onSaveGlobal={handleSaveGlobalRules}
              onSavePersonaBaseline={handleSavePersonaBaselineRules}
              onSaveTeam={handleSaveTeamRules}
            />
          ) : null}
        </div>
      ) : screen === "settings" ? (
        settings ? (
          <SettingsView
            settings={settings}
            authSessions={authSessions}
            notificationState={notificationState}
            onEnableNotifications={handleEnableNotifications}
            onDisableNotifications={handleDisableNotifications}
            onAccessModeChange={handleAccessModeChange}
            onRevokeSession={handleRevokeAuthSession}
            onRevokeAllSessions={handleRevokeAllAuthSessions}
          />
        ) : null
      ) : screen === "operations" ? (
        <OperationsView
          data={operations}
          loading={operationsLoading}
          error={operationsError}
          onRefresh={loadOperations}
          onEmergencyStop={handleEmergencyStop}
          onResumeIntake={handleResumeIntake}
          onCreateBackup={handleCreateBackup}
          onVerifyBackup={handleVerifyBackup}
          onOpenTarget={handleOpenOperationTarget}
          onResumeItem={handleResumeOperationItem}
          onRetryItem={handleRetryOperationItem}
          onRelogin={handleOperationsRelogin}
        />
      ) : screen === "artifacts" ? (
        <div className="screen">
          <ArtifactsView
            artifacts={artifacts}
            onChange={() => api.artifacts().then(setArtifacts).catch(setScreenError)}
          />
        </div>
      ) : screen === "jobs" ? (
        <div className="screen">
          <JobsView
            jobs={jobs}
            onLoadEvents={api.jobEvents}
            onRetry={handleRetryJob}
            focusJobId={focusedJobId}
            onFocusHandled={() => setFocusedJobId(null)}
          />
        </div>
      ) : screen === "schedules" ? (
        <div className="screen">
          <SchedulesView
            schedules={schedules}
            automationReady={settings?.automation_ready === true}
            automationUnavailableReason={settings?.automation_unavailable_reason || "Automation status is unavailable"}
            onCreate={handleCreateSchedule}
            onPause={handlePauseSchedule}
            onResume={handleResumeSchedule}
            onDelete={handleDeleteSchedule}
            onRunNow={handleRunScheduleNow}
            onLoadDetail={api.scheduleDetail}
            focusScheduleId={focusedScheduleId}
            onFocusHandled={() => setFocusedScheduleId(null)}
          />
        </div>
      ) : screen === "hooks" ? (
        <div className="screen">
          <HooksView
            hooks={hooks}
            hookRuns={hookRuns}
            agents={agents}
            personas={personas}
            teamRuns={teamRuns}
            openHookRunsId={openHookRunsId}
            onCreate={handleCreateHook}
            onToggle={handleToggleHook}
            onRunNow={handleRunHookNow}
            onDelete={handleDeleteHook}
            onOpenRuns={handleOpenHookRuns}
            onCloseRuns={handleCloseHookRuns}
            onTestConnection={handleTestHookConnection}
            onOpenTeamRun={handleOpenHookTeamRun}
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

export { applyTeamRunDelta } from "../../../hooks/useTeamRunController.js";
