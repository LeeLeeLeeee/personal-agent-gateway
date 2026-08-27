import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";

// 이벤트를 놓쳤을 때의 안전망이다. 짧게 두면 이벤트가 제 몫을 하는
// 동안에도 불필요한 요청이 계속 나가고, 길게 두면 낡은 화면을 보는
// 시간이 그만큼 길어진다.
const TEAM_RUN_REFRESH_MS = 15000;

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

// 질문·답변만 서버 목록으로 갈아끼운다. 나머지 메시지는 그대로 둔다.
// 이어붙이지 않고 갈아끼우는 것이 요점이다 -- 화면이 보낸 것을 덧붙이면
// 다음 상세 갱신 때 저장된 행이 함께 도착해 같은 문답이 두 번 그려진다.
export function withQuestionMessages(detail, messages) {
  if (!detail) return detail;
  const others = (detail.messages || []).filter(
    (message) => message.kind !== "user_question" && message.kind !== "lead_answer"
  );
  return { ...detail, messages: [...others, ...messages] };
}

export function useTeamRunController({ toast, confirm, setScreenError, reloadKey = 0 }) {
  const [teamRuns, setTeamRuns] = useState([]);
  const [creatingTeamRun, setCreatingTeamRun] = useState(false);
  const [runFilter, setRunFilter] = useState("all");
  const [selectedTeamRunId, setSelectedTeamRunId] = useState(null);
  const [teamRunDetail, setTeamRunDetail] = useState(null);
  // 리드가 답을 쓰는 동안의 진행. 상세와 따로 두는 이유: 이것은 서버가
  // 가진 상태가 아니라 지나가는 신호라, 상세를 다시 읽으면 사라진다.
  const [questionProgress, setQuestionProgress] = useState(null);
  const [teamRunDocuments, setTeamRunDocuments] = useState([]);
  const [teamRunDelivery, setTeamRunDelivery] = useState(null);
  const [teamRunDeliveryLoading, setTeamRunDeliveryLoading] = useState(false);
  const [teamRunDetailLoading, setTeamRunDetailLoading] = useState(false);
  const [teamRunDetailLoadErrorId, setTeamRunDetailLoadErrorId] = useState(null);
  const selectedTeamRunIdRef = useRef(null);
  const selectedTeamRunVersionRef = useRef(0);
  const teamRunDetailRef = useRef(null);
  const teamRunDetailRequestPendingRef = useRef(false);
  const teamRunDetailRequestVersionRef = useRef(0);
  const manualCycleRequestRef = useRef(null);

  useEffect(() => {
    teamRunDetailRef.current = teamRunDetail;
  }, [teamRunDetail]);

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

  function beginTeamRunDetailRequest() {
    teamRunDetailRequestVersionRef.current += 1;
    return teamRunDetailRequestVersionRef.current;
  }

  function ownsTeamRunDetailRequest(requestVersion) {
    return teamRunDetailRequestVersionRef.current === requestVersion;
  }

  useEffect(() => {
    if (!selectedTeamRunId) {
      teamRunDetailRequestPendingRef.current = false;
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
    const detailRequestVersion = beginTeamRunDetailRequest();
    teamRunDetailRequestPendingRef.current = true;
    api.teamRunDetail(selectedTeamRunId).then((detail) => {
      if (!detail?.run) throw new Error("Team run detail is unavailable");
      if (alive && ownsTeamRunDetailRequest(detailRequestVersion)) {
        teamRunDetailRequestPendingRef.current = false;
        setTeamRunDetail(detail);
      }
    }).catch((error) => {
      if (alive && ownsTeamRunDetailRequest(detailRequestVersion)) {
        teamRunDetailRequestPendingRef.current = false;
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

  // 화면은 이벤트로만 갱신된다. 탭이 뒤에 있거나 SSE 를 놓치면 그대로 낡고,
  // 사용자는 그것이 지금인지 알 방법이 없다. 돌아왔을 때와 주기적으로 한 번씩
  // 다시 읽는다.
  //
  // 처음 불러오기와 달리 상세를 비우지 않고 로딩도 켜지 않는다. 그렇게 하면
  // 이미 보고 있던 화면이 갱신할 때마다 몇 백 밀리초씩 사라진다.
  const refreshSelectedTeamRun = useCallback(() => {
    const runId = selectedTeamRunIdRef.current;
    if (!runId) return;
    const requestedRun = {
      id: runId,
      version: selectedTeamRunVersionRef.current
    };
    const requestVersion = beginTeamRunDetailRequest();
    teamRunDetailRequestPendingRef.current = true;
    api.teamRunDetail(runId)
      .then((detail) => {
        if (!detail?.run) return;
        if (ownsSelectedRun(requestedRun) && ownsTeamRunDetailRequest(requestVersion)) {
          teamRunDetailRequestPendingRef.current = false;
          setTeamRunDetail(detail);
        }
      })
      .catch(() => {
        // 조용한 갱신이라 오류를 화면에 올리지 않는다. 사용자가 아무것도 하지
        // 않았는데 뜨는 오류는 무엇을 하라는 말인지 알 수 없고, 다음 주기에
        // 다시 시도한다.
        teamRunDetailRequestPendingRef.current = false;
      });
  }, []);

  useEffect(() => {
    if (!selectedTeamRunId) return undefined;
    const refreshIfVisible = () => {
      // 숨어 있는 탭까지 주기적으로 읽으면 열어둔 창 수만큼 트래픽이 는다.
      if (document.visibilityState === "hidden") return;
      refreshSelectedTeamRun();
    };
    window.addEventListener("focus", refreshIfVisible);
    document.addEventListener("visibilitychange", refreshIfVisible);
    const timer = window.setInterval(refreshIfVisible, TEAM_RUN_REFRESH_MS);
    return () => {
      window.removeEventListener("focus", refreshIfVisible);
      document.removeEventListener("visibilitychange", refreshIfVisible);
      window.clearInterval(timer);
    };
  }, [selectedTeamRunId, refreshSelectedTeamRun]);

  const handleTeamEvent = useCallback((event) => {
    const requiresRefresh = [
      "team.run.completed",
      "team.run.blocked",
      "team.run.failed",
      "team.run.input_requested",
      "team.run.input_resolved",
      // 정지는 실행 중에 걸리므로 목록의 상태도 같이 낡는다. 상세는 델타로도
      // 갱신되지만, 이 자리에 없으면 목록이 `running` 인 채로 남는다.
      "team.run.paused",
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
    // 진행 이벤트는 상세를 다시 읽지 않는다. 아래 규칙은 델타가 없는 이벤트를
    // 전부 재조회로 보내는데, 답이 써지는 동안 조각마다 상세를 다시 읽으면
    // 화면이 그 요청들에 잠긴다. 활동과 답변 조각은 다른 이벤트로 오므로
    // 통째로 갈아치우지 않고 각각 남긴다.
    if (event.type === "team.question.progress") {
      setQuestionProgress((current) => ({
        activity: event.activity ?? current?.activity ?? null,
        answerPartial: event.answer_partial ?? current?.answerPartial ?? null
      }));
      return;
    }
    const invalidatesPendingDetail = teamRunDetailRequestPendingRef.current;
    const detailEventVersion = beginTeamRunDetailRequest();
    const requestedRun = {
      id: event.team_run_id,
      version: selectedTeamRunVersionRef.current
    };
    const hasDelta = event.run || event.task || event.agent;
    if (hasDelta) {
      setTeamRunDetail((current) => applyTeamRunDelta(current, event));
    }
    const requiresDetailRefresh = !hasDelta
      || requiresRefresh
      || event.acceptance_reviewed
      || invalidatesPendingDetail
      || teamRunDetailRef.current?.run?.id !== event.team_run_id;
    if (requiresDetailRefresh) {
      teamRunDetailRequestPendingRef.current = true;
      api.teamRunDetail(event.team_run_id)
        .then((detail) => {
          if (
            ownsSelectedRun(requestedRun)
            && ownsTeamRunDetailRequest(detailEventVersion)
          ) {
            teamRunDetailRequestPendingRef.current = false;
            setTeamRunDetail(detail);
          }
        })
        .catch((error) => {
          if (
            ownsSelectedRun(requestedRun)
            && ownsTeamRunDetailRequest(detailEventVersion)
          ) {
            teamRunDetailRequestPendingRef.current = false;
            setScreenError(error);
          }
        });
    }
    if (!hasDelta || requiresRefresh) {
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
      const { initial_instruction: initialInstruction, ...createPayload } = payload;
      const created = await api.createTeamRun(createPayload);
      if (!created) {
        toast("Failed to create team run", "error");
        return false;
      }
      let firstRequestFailed = false;
      if (payload.execution_policy === "triggered" && initialInstruction) {
        try {
          await api.triggerTeamCycle(created.id, {
            instruction: initialInstruction,
            previous_cycle_id: null,
            client_request_id: crypto.randomUUID()
          });
        } catch (_error) {
          firstRequestFailed = true;
        }
      }
      setCreatingTeamRun(false);
      setTeamRuns(await api.teamRuns());
      setSelectedTeamRunId(created.id);
      if (firstRequestFailed) {
        toast("Team Run created, but the first request did not start", "error");
        return false;
      }
      toast(
        payload.execution_policy === "auto"
          ? "AUTO Team Run started"
          : initialInstruction ? "Team Run started" : "TRIGGERED Team Run created",
        "success"
      );
      return true;
    } catch (_error) {
      toast("Failed to create team run", "error");
      return false;
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

  async function handlePauseTeamRun(runId) {
    const requestedRun = captureSelectedRun();
    const targetId = runId || requestedRun.id;
    if (!targetId) return false;
    // 실패를 삼키지 않는다. 다른 조작과 달리 정지 실패는 물어보기 대화상자
    // 안에서 보여주고 그 자리에서 다시 시도하게 해야 한다 -- toast 로만
    // 알리면 대화상자는 영영 정지를 기다리는 모습으로 남는다.
    await api.pauseRun(targetId);
    const [detail, runs] = await Promise.all([
      api.teamRunDetail(targetId),
      api.teamRuns()
    ]);
    if (ownsSelectedRun(requestedRun)) setTeamRunDetail(detail);
    setTeamRuns(runs);
    return true;
  }

  async function handleAskTeamRun(runId, question) {
    const requestedRun = captureSelectedRun();
    const targetId = runId || requestedRun.id;
    const text = question?.trim();
    if (!targetId || !text) throw new Error("Question is empty");
    // 이전 질문의 진행이 남아 있으면 새 질문이 시작하자마자 남의 답이 보인다.
    setQuestionProgress(null);
    try {
      var result = await api.askQuestion(targetId, text);
    } finally {
      // 성공이든 실패든 진행은 여기서 끝난다. 남기면 대화상자가 이미 끝난
      // 호출의 조각을 계속 보여준다.
      setQuestionProgress(null);
    }
    if (!result?.answer) throw new Error("The lead returned no answer");
    // 서버가 가진 질문 기록을 다시 읽는다. 보낸 것을 화면에서 이어붙이면
    // 다음 상세 갱신 때 저장된 행과 겹쳐 같은 문답이 두 번 그려진다.
    // /detail 의 messages 는 잘릴 수 있으므로 전용 목록을 쓴다.
    const questions = await api.listQuestions(targetId);
    if (ownsSelectedRun(requestedRun)) {
      setTeamRunDetail((current) => withQuestionMessages(current, questions?.messages || []));
    }
    return result;
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

  async function handleReopenTeamRun() {
    // Stop 은 결정 질문 하나를 끊으려고 누르기도 하는데, 그러면 런 전체가
    // 취소되고 일감 추가가 거절된다. 되살릴 길이 화면에 없으면 그 버튼은
    // 함정이 된다.
    const requestedRun = captureSelectedRun();
    if (!requestedRun.id) return false;
    try {
      const result = await api.reopenTeamRun(requestedRun.id);
      if (!result) {
        toast("팀 작업을 다시 열지 못했습니다", "error");
        return false;
      }
      const [detail, runs] = await Promise.all([
        api.teamRunDetail(requestedRun.id),
        api.teamRuns()
      ]);
      if (ownsSelectedRun(requestedRun)) setTeamRunDetail(detail);
      setTeamRuns(runs);
      toast("다시 열었습니다. Resume 으로 이어가세요", "success");
      return true;
    } catch (error) {
      toast(error?.detail || "팀 작업을 다시 열지 못했습니다", "error");
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
      } else if (delivery?.auto_resolved_files?.length) {
        toast(
          `Auto-resolved ${delivery.auto_resolved_files.length} generated file conflict(s) and applied changes`,
          "success"
        );
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
      } else if (delivery?.auto_resolved_files?.length) {
        toast(
          `Auto-resolved ${delivery.auto_resolved_files.length} generated file conflict(s) and applied changes`,
          "success"
        );
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
    if (!deleted.ok) {
      toast(deleted.detail || "Failed to delete team run", "error");
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
    handlePauseTeamRun,
    handleAskTeamRun,
    refreshSelectedTeamRun,
    questionProgress,
    handleResumeTeamRun,
    handleAnswerTeamDecision,
    handleCancelTeamRun,
    handleReopenTeamRun,
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
