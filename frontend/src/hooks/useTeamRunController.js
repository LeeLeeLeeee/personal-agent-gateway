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

export function useTeamRunController({ toast, confirm, setScreenError }) {
  const [teamRuns, setTeamRuns] = useState([]);
  const [creatingTeamRun, setCreatingTeamRun] = useState(false);
  const [runFilter, setRunFilter] = useState("all");
  const [selectedTeamRunId, setSelectedTeamRunId] = useState(null);
  const [teamRunDetail, setTeamRunDetail] = useState(null);
  const [teamRunDocuments, setTeamRunDocuments] = useState([]);
  const selectedTeamRunIdRef = useRef(null);

  useEffect(() => {
    selectedTeamRunIdRef.current = selectedTeamRunId;
  }, [selectedTeamRunId]);

  useEffect(() => {
    if (!selectedTeamRunId) {
      setTeamRunDetail(null);
      setTeamRunDocuments([]);
      return undefined;
    }
    let alive = true;
    api.teamRunDetail(selectedTeamRunId).then((detail) => {
      if (alive) setTeamRunDetail(detail);
    }).catch((error) => {
      if (alive) setScreenError(error);
    });
    api.teamDocuments(selectedTeamRunId).then((documents) => {
      if (alive) setTeamRunDocuments(documents);
    }).catch((error) => {
      if (alive) setScreenError(error);
    });
    return () => {
      alive = false;
    };
  }, [selectedTeamRunId, setScreenError]);

  const handleTeamEvent = useCallback((event) => {
    const requiresRefresh = [
      "team.run.completed",
      "team.run.failed",
      "team.run.input_requested",
      "team.run.input_resolved"
    ].includes(event.type);
    if (requiresRefresh) {
      api.teamRuns()
        .then(setTeamRuns)
        .catch(setScreenError);
    }
    if (event.team_run_id !== selectedTeamRunIdRef.current) return;
    const hasDelta = event.run || event.task || event.agent;
    if (hasDelta) {
      setTeamRunDetail((current) => applyTeamRunDelta(current, event));
    }
    if (!hasDelta || requiresRefresh) {
      api.teamRunDetail(event.team_run_id)
        .then(setTeamRunDetail)
        .catch(setScreenError);
      api.teamDocuments(event.team_run_id)
        .then(setTeamRunDocuments)
        .catch(setScreenError);
    }
  }, [setScreenError]);

  async function handleCreateTeamRun(payload) {
    try {
      const created = await api.createTeamRun(payload);
      if (!created) {
        toast("Failed to create team run", "error");
        return;
      }
      const isContinuous = payload.lifecycle_mode === "continuous";
      let selectedRun = created;
      if (!isContinuous) {
        const started = await api.startTeamRun(created.id);
        if (!started) {
          toast("Failed to start team run", "error");
          return;
        }
        selectedRun = started;
      }
      setCreatingTeamRun(false);
      setTeamRuns(await api.teamRuns());
      setSelectedTeamRunId(selectedRun.id);
      toast(
        isContinuous ? "Continuous team run created; waiting for hook triggers" : "Team run started",
        "success"
      );
    } catch (_error) {
      toast("Failed to create team run", "error");
    }
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

  async function handleCancelTeamRun() {
    if (!selectedTeamRunId) return false;
    const accepted = await confirm({
      title: "STOP TEAM RUN",
      message: "Stop the active processes? Existing documents and completed work are kept.",
      confirmLabel: "Stop run",
      danger: true
    });
    if (!accepted) return false;
    try {
      const result = await api.cancelTeamRun(selectedTeamRunId);
      if (!result) {
        toast("Failed to stop team run", "error");
        return false;
      }
      const [detail, runs] = await Promise.all([
        api.teamRunDetail(selectedTeamRunId),
        api.teamRuns()
      ]);
      setTeamRunDetail(detail);
      setTeamRuns(runs);
      toast("팀 작업을 중지했습니다", "success");
      return true;
    } catch (_error) {
      toast("Failed to stop team run", "error");
      return false;
    }
  }

  async function handleAnswerTeamDecision(answers) {
    const request = teamRunDetail?.decisionRequest;
    if (!selectedTeamRunId || !request) return false;
    try {
      const result = await api.answerTeamDecision(
        selectedTeamRunId,
        request.id,
        request.revision,
        answers
      );
      if (!result) {
        toast("Failed to answer decision request", "error");
        return false;
      }
      const [detail, runs, documents] = await Promise.all([
        api.teamRunDetail(selectedTeamRunId),
        api.teamRuns(),
        api.teamDocuments(selectedTeamRunId)
      ]);
      setTeamRunDetail(detail);
      setTeamRuns(runs);
      setTeamRunDocuments(documents);
      toast("답변을 전달하고 팀 작업을 재개했습니다", "success");
      return true;
    } catch (_error) {
      toast("Failed to answer decision request", "error");
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
  };
}
