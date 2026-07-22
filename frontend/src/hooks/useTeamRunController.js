import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";

export function applyTeamRunDelta(detail, event) {
  if (!detail) return detail;
  const run = event.run ? { ...detail.run, ...event.run } : detail.run;
  let tasks = detail.tasks || [];
  if (event.task) {
    const found = tasks.some((task) => task.id === event.task.id);
    tasks = found
      ? tasks.map((task) => task.id === event.task.id ? { ...task, ...event.task } : task)
      : [...tasks, event.task];
  }
  let agents = detail.agents || [];
  if (event.agent) {
    const found = agents.some((agent) => agent.id === event.agent.id);
    agents = found
      ? agents.map((agent) => agent.id === event.agent.id ? { ...agent, ...event.agent } : agent)
      : [...agents, event.agent];
  }
  return { ...detail, run, tasks, agents };
}

export function useTeamRunController({ toast, confirm, setScreenError, reloadKey = 0 }) {
  const [teamRuns, setTeamRuns] = useState([]);
  const [creatingTeamRun, setCreatingTeamRun] = useState(false);
  const [runFilter, setRunFilter] = useState("all");
  const [selectedTeamRunId, setSelectedTeamRunId] = useState(null);
  const [teamRunDetail, setTeamRunDetail] = useState(null);
  const [teamRunDocuments, setTeamRunDocuments] = useState([]);
  const [teamRunDelivery, setTeamRunDelivery] = useState(null);
  const [teamRunDeliveryLoading, setTeamRunDeliveryLoading] = useState(false);
  const [teamRunDetailLoading, setTeamRunDetailLoading] = useState(false);
  const [teamRunDetailLoadErrorId, setTeamRunDetailLoadErrorId] = useState(null);
  const selectedTeamRunIdRef = useRef(null);
  const selectedTeamRunVersionRef = useRef(0);
  const manualCycleRequestRef = useRef(null);

  useEffect(() => {
    selectedTeamRunIdRef.current = selectedTeamRunId;
    selectedTeamRunVersionRef.current += 1;
  }, [selectedTeamRunId]);

  function captureSelectedRun() {
    return {
      id: selectedTeamRunId,
      version: selectedTeamRunVersionRef.current
    };
  }

  function ownsSelectedRun(requestedRun) {
    return selectedTeamRunIdRef.current === requestedRun.id
      && selectedTeamRunVersionRef.current === requestedRun.version;
  }

  useEffect(() => {
    if (!selectedTeamRunId) {
      setTeamRunDetail(null);
      setTeamRunDocuments([]);
      setTeamRunDelivery(null);
      setTeamRunDeliveryLoading(false);
      setTeamRunDetailLoading(false);
      setTeamRunDetailLoadErrorId(null);
      return undefined;
    }
    let alive = true;
    setTeamRunDetail(null);
    setTeamRunDocuments([]);
    setTeamRunDelivery(null);
    setTeamRunDetailLoading(true);
    setTeamRunDeliveryLoading(true);
    setTeamRunDetailLoadErrorId(null);
    api.teamRunDetail(selectedTeamRunId).then((detail) => {
      if (!detail?.run) throw new Error("Team run detail is unavailable");
      if (alive) setTeamRunDetail(detail);
    }).catch((error) => {
      if (alive) {
        setTeamRunDetailLoadErrorId(selectedTeamRunId);
        setScreenError(error);
      }
    }).finally(() => {
      if (alive) setTeamRunDetailLoading(false);
    });
    api.teamDocuments(selectedTeamRunId).then((documents) => {
      if (alive) setTeamRunDocuments(documents);
    }).catch((error) => {
      if (alive) setScreenError(error);
    });
    api.teamRunDelivery(selectedTeamRunId).then((delivery) => {
      if (alive) setTeamRunDelivery(delivery);
    }).catch((error) => {
      if (alive) setScreenError(error);
    }).finally(() => {
      if (alive) setTeamRunDeliveryLoading(false);
    });
    return () => {
      alive = false;
    };
  }, [selectedTeamRunId, setScreenError, reloadKey]);

  const handleTeamEvent = useCallback((event) => {
    const requiresRefresh = [
      "team.run.completed",
      "team.run.failed",
      "team.run.input_requested",
      "team.run.input_resolved",
      "team.cycle_request.queued",
      "team.cycle.started",
      "team.cycle.settled",
      "team.auto_series.paused",
      "team.auto_series.completed"
    ].includes(event.type);
    if (requiresRefresh) {
      api.teamRuns()
        .then(setTeamRuns)
        .catch(setScreenError);
    }
    if (event.team_run_id !== selectedTeamRunIdRef.current) return;
    const requestedRun = {
      id: event.team_run_id,
      version: selectedTeamRunVersionRef.current
    };
    const hasDelta = event.run || event.task || event.agent;
    if (hasDelta) {
      setTeamRunDetail((current) => applyTeamRunDelta(current, event));
    }
    if (!hasDelta || requiresRefresh) {
      api.teamRunDetail(event.team_run_id)
        .then((detail) => {
          if (ownsSelectedRun(requestedRun)) setTeamRunDetail(detail);
        })
        .catch((error) => {
          if (ownsSelectedRun(requestedRun)) setScreenError(error);
        });
      api.teamDocuments(event.team_run_id)
        .then((documents) => {
          if (ownsSelectedRun(requestedRun)) setTeamRunDocuments(documents);
        })
        .catch((error) => {
          if (ownsSelectedRun(requestedRun)) setScreenError(error);
        });
      api.teamRunDelivery(event.team_run_id)
        .then((delivery) => {
          if (ownsSelectedRun(requestedRun)) setTeamRunDelivery(delivery);
        })
        .catch((error) => {
          if (ownsSelectedRun(requestedRun)) setScreenError(error);
        });
    }
  }, [setScreenError]);

  async function handleCreateTeamRun(payload) {
    try {
      const created = await api.createTeamRun(payload);
      if (!created) {
        toast("Failed to create team run", "error");
        return;
      }
      setCreatingTeamRun(false);
      setTeamRuns(await api.teamRuns());
      setSelectedTeamRunId(created.id);
      toast(
        payload.execution_policy === "auto"
          ? "AUTO Team Run started"
          : "TRIGGERED Team Run created",
        "success"
      );
    } catch (_error) {
      toast("Failed to create team run", "error");
    }
  }

  async function refreshSelectedRun(requestedRun) {
    const [detail, runs] = await Promise.all([
      api.teamRunDetail(requestedRun.id),
      api.teamRuns()
    ]);
    if (ownsSelectedRun(requestedRun)) setTeamRunDetail(detail);
    setTeamRuns(runs);
  }

  async function handleTriggerTeamCycle(payload) {
    const requestedRun = captureSelectedRun();
    const instruction = payload?.instruction?.trim();
    if (!requestedRun.id || !instruction) return false;
    const identity = JSON.stringify([
      requestedRun.id,
      instruction,
      payload.previous_cycle_id ?? null
    ]);
    if (manualCycleRequestRef.current?.identity !== identity) {
      manualCycleRequestRef.current = {
        identity,
        clientRequestId: crypto.randomUUID()
      };
    }
    const clientRequestId = manualCycleRequestRef.current.clientRequestId;
    try {
      await api.triggerTeamCycle(requestedRun.id, {
        ...payload,
        instruction,
        client_request_id: clientRequestId
      });
      if (manualCycleRequestRef.current?.identity === identity
        && manualCycleRequestRef.current.clientRequestId === clientRequestId) {
        manualCycleRequestRef.current = null;
      }
      await refreshSelectedRun(requestedRun);
      toast("Cycle을 대기열에 추가했습니다", "success");
      return true;
    } catch (_error) {
      toast("Failed to trigger cycle", "error");
      return false;
    }
  }

  async function handleRetryAuto(seriesId) {
    const requestedRun = captureSelectedRun();
    if (!requestedRun.id || !seriesId) return false;
    try {
      await api.retryAutoCycle(requestedRun.id, seriesId);
      await refreshSelectedRun(requestedRun);
      return true;
    } catch (_error) {
      toast("Failed to retry AUTO cycle", "error");
      return false;
    }
  }

  async function handleContinueAuto(seriesId) {
    const requestedRun = captureSelectedRun();
    if (!requestedRun.id || !seriesId) return false;
    try {
      await api.continueAutoCycle(requestedRun.id, seriesId);
      await refreshSelectedRun(requestedRun);
      return true;
    } catch (_error) {
      toast("Failed to continue AUTO series", "error");
      return false;
    }
  }

  async function handleRestartAuto() {
    const requestedRun = captureSelectedRun();
    if (!requestedRun.id) return false;
    try {
      await api.restartAutoSeries(requestedRun.id);
      await refreshSelectedRun(requestedRun);
      return true;
    } catch (_error) {
      toast("Failed to restart AUTO series", "error");
      return false;
    }
  }

  async function handleAddWork(instruction) {
    const requestedRun = captureSelectedRun();
    if (!requestedRun.id || !instruction.trim()) return false;
    try {
      const result = await api.addWork(requestedRun.id, instruction.trim());
      if (!result) {
        toast("Failed to add work", "error");
        return false;
      }
      const detail = await api.teamRunDetail(requestedRun.id);
      if (ownsSelectedRun(requestedRun)) setTeamRunDetail(detail);
      toast("추가 업무를 전달했습니다", "success");
      return true;
    } catch (_error) {
      toast("Failed to add work", "error");
      return false;
    }
  }

  async function handleResumeTeamRun() {
    const requestedRun = captureSelectedRun();
    if (!requestedRun.id) return false;
    const accepted = await confirm({
      title: "RESUME TEAM RUN",
      message: "Resume pending work for this interrupted team run? Completed tasks will be kept.",
      confirmLabel: "Resume"
    });
    if (!accepted) return false;
    try {
      const result = await api.resumeTeamRun(requestedRun.id);
      if (!result) {
        toast("Failed to resume team run", "error");
        return false;
      }
      const [detail, runs] = await Promise.all([
        api.teamRunDetail(requestedRun.id),
        api.teamRuns()
      ]);
      if (ownsSelectedRun(requestedRun)) setTeamRunDetail(detail);
      setTeamRuns(runs);
      toast("팀 작업을 재개했습니다", "success");
      return true;
    } catch (_error) {
      toast("Failed to resume team run", "error");
      return false;
    }
  }

  async function handleCancelTeamRun() {
    const requestedRun = captureSelectedRun();
    if (!requestedRun.id) return false;
    const accepted = await confirm({
      title: "STOP TEAM RUN",
      message: "Stop the active processes? Existing documents and completed work are kept.",
      confirmLabel: "Stop run",
      danger: true
    });
    if (!accepted) return false;
    try {
      const result = await api.cancelTeamRun(requestedRun.id);
      if (!result) {
        toast("Failed to stop team run", "error");
        return false;
      }
      const [detail, runs] = await Promise.all([
        api.teamRunDetail(requestedRun.id),
        api.teamRuns()
      ]);
      if (ownsSelectedRun(requestedRun)) setTeamRunDetail(detail);
      setTeamRuns(runs);
      toast("팀 작업을 중지했습니다", "success");
      return true;
    } catch (_error) {
      toast("Failed to stop team run", "error");
      return false;
    }
  }

  async function handleAnswerTeamDecision(answers) {
    const requestedRun = captureSelectedRun();
    const request = teamRunDetail?.decisionRequest;
    if (!requestedRun.id || !request) return false;
    try {
      const result = await api.answerTeamDecision(
        requestedRun.id,
        request.id,
        request.revision,
        answers
      );
      if (!result) {
        toast("Failed to answer decision request", "error");
        return false;
      }
      const [detail, runs, documents] = await Promise.all([
        api.teamRunDetail(requestedRun.id),
        api.teamRuns(),
        api.teamDocuments(requestedRun.id)
      ]);
      setTeamRuns(runs);
      if (ownsSelectedRun(requestedRun)) {
        setTeamRunDetail(detail);
        setTeamRunDocuments(documents);
      }
      toast("답변을 전달하고 팀 작업을 재개했습니다", "success");
      return true;
    } catch (_error) {
      toast("Failed to answer decision request", "error");
      return false;
    }
  }

  async function handleRetryTeamTask(taskId) {
    const requestedRun = captureSelectedRun();
    if (!requestedRun.id) return false;
    const task = teamRunDetail?.tasks?.find((item) => item.id === taskId);
    const accepted = await confirm({
      title: "RETRY FAILED TASK",
      message: "Queue “" + (task?.title || "this task")
        + "” for retry? You will need to resume the team run afterward.",
      confirmLabel: "Retry"
    });
    if (!accepted) return false;
    try {
      const result = await api.retryTeamTask(requestedRun.id, taskId);
      if (!result) {
        toast("Failed to retry task", "error");
        return false;
      }
      const [detail, runs] = await Promise.all([
        api.teamRunDetail(requestedRun.id),
        api.teamRuns()
      ]);
      if (ownsSelectedRun(requestedRun)) setTeamRunDetail(detail);
      setTeamRuns(runs);
      toast("실패한 업무를 재시도 대기열에 추가했습니다", "success");
      return true;
    } catch (_error) {
      toast("Failed to retry task", "error");
      return false;
    }
  }

  async function handleRefreshTeamRunDelivery() {
    const requestedRun = captureSelectedRun();
    if (!requestedRun.id) return false;
    setTeamRunDeliveryLoading(true);
    try {
      const delivery = await api.teamRunDelivery(requestedRun.id);
      if (ownsSelectedRun(requestedRun)) setTeamRunDelivery(delivery);
      return true;
    } catch (_error) {
      toast("Failed to refresh Team Run changes", "error");
      return false;
    } finally {
      if (ownsSelectedRun(requestedRun)) setTeamRunDeliveryLoading(false);
    }
  }

  async function handleCommitTeamRunDelivery(message) {
    const requestedRun = captureSelectedRun();
    if (!requestedRun.id || !message.trim()) return false;
    try {
      const delivery = await api.commitTeamRunDelivery(requestedRun.id, message.trim());
      if (ownsSelectedRun(requestedRun)) setTeamRunDelivery(delivery);
      toast("Team Run changes committed", "success");
      return true;
    } catch (_error) {
      toast("Failed to commit Team Run changes", "error");
      return false;
    }
  }

  async function handleApplyTeamRunDelivery() {
    const requestedRun = captureSelectedRun();
    if (!requestedRun.id || !teamRunDelivery?.can_apply) return false;
    const commitCount = teamRunDelivery.pending_commits?.length || 0;
    const accepted = await confirm({
      title: "APPLY TEAM RUN CHANGES",
      message: `Apply ${commitCount} commit(s) to ${teamRunDelivery.target?.branch || "the target repository"}?`,
      confirmLabel: "Apply"
    });
    if (!accepted) return false;
    try {
      const delivery = await api.applyTeamRunDelivery(requestedRun.id);
      if (ownsSelectedRun(requestedRun)) setTeamRunDelivery(delivery);
      if (delivery?.conflict_session) {
        toast("Repository conflicts need your resolution", "error");
      } else {
        toast("Team Run changes applied to the repository", "success");
      }
      return true;
    } catch (error) {
      toast(error?.message || "Failed to apply Team Run changes", "error");
      return false;
    }
  }

  async function handleResolveTeamRunDeliveryConflict(conflictId, resolution) {
    const requestedRun = captureSelectedRun();
    if (!requestedRun.id || !conflictId) return false;
    try {
      const delivery = await api.resolveTeamRunDeliveryConflict(
        requestedRun.id,
        conflictId,
        resolution
      );
      if (ownsSelectedRun(requestedRun)) setTeamRunDelivery(delivery);
      return true;
    } catch (error) {
      toast(error?.message || "Failed to resolve repository conflict", "error");
      return false;
    }
  }

  async function handleContinueTeamRunDelivery() {
    const requestedRun = captureSelectedRun();
    if (!requestedRun.id || !teamRunDelivery?.conflict_session?.can_continue) return false;
    try {
      const delivery = await api.continueTeamRunDelivery(requestedRun.id);
      if (ownsSelectedRun(requestedRun)) setTeamRunDelivery(delivery);
      if (delivery?.conflict_session) {
        toast("More repository conflicts need your resolution", "error");
      } else {
        toast("Resolved changes applied to the repository", "success");
      }
      return true;
    } catch (error) {
      toast(error?.message || "Failed to continue repository delivery", "error");
      return false;
    }
  }

  async function handleCancelTeamRunDeliveryConflicts() {
    const requestedRun = captureSelectedRun();
    if (!requestedRun.id || !teamRunDelivery?.conflict_session) return false;
    const accepted = await confirm({
      title: "CANCEL CONFLICT RESOLUTION",
      message: "Discard the current conflict resolutions? The source and target repositories stay unchanged.",
      confirmLabel: "Cancel resolution"
    });
    if (!accepted) return false;
    try {
      const delivery = await api.cancelTeamRunDeliveryConflicts(requestedRun.id);
      if (ownsSelectedRun(requestedRun)) setTeamRunDelivery(delivery);
      toast("Repository conflict resolution canceled", "success");
      return true;
    } catch (error) {
      toast(error?.message || "Failed to cancel repository conflict resolution", "error");
      return false;
    }
  }

  async function handleDeleteTeamRun(id) {
    const accepted = await confirm({
      title: "DELETE TEAM RUN",
      message: "Delete this team run? This cannot be undone.",
      confirmLabel: "Delete",
      danger: true
    });
    if (!accepted) return;
    const deleted = await api.deleteTeamRun(id);
    if (!deleted) {
      toast("Failed to delete team run", "error");
      return;
    }
    if (id === selectedTeamRunId) setSelectedTeamRunId(null);
    setTeamRuns(await api.teamRuns());
    toast("Team run deleted", "success");
  }

  function handleSelectTeamRun(id) {
    setSelectedTeamRunId(id);
  }

  function handleBackToTeamRuns() {
    setSelectedTeamRunId(null);
    setCreatingTeamRun(false);
  }

  function clearTeamRunView() {
    setSelectedTeamRunId(null);
    setTeamRunDetail(null);
    setTeamRunDocuments([]);
    setTeamRunDelivery(null);
    setTeamRunDeliveryLoading(false);
    setCreatingTeamRun(false);
  }

  return {
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
    teamRunDelivery,
    teamRunDeliveryLoading,
    teamRunDetailLoading,
    teamRunDetailLoadError: Boolean(
      selectedTeamRunId && teamRunDetailLoadErrorId === selectedTeamRunId
    ),
    handleTeamEvent,
    handleCreateTeamRun,
    handleTriggerTeamCycle,
    handleRetryAuto,
    handleContinueAuto,
    handleRestartAuto,
    handleAddWork,
    handleResumeTeamRun,
    handleAnswerTeamDecision,
    handleCancelTeamRun,
    handleRetryTeamTask,
    handleRefreshTeamRunDelivery,
    handleCommitTeamRunDelivery,
    handleApplyTeamRunDelivery,
    handleResolveTeamRunDeliveryConflict,
    handleContinueTeamRunDelivery,
    handleCancelTeamRunDeliveryConflicts,
    handleDeleteTeamRun,
    handleSelectTeamRun,
    handleBackToTeamRuns,
    clearTeamRunView
  };
}
