import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client.js";
import { useTeamRunController } from "./useTeamRunController.js";

vi.mock("../api/client.js", () => ({
  api: {
    answerTeamDecision: vi.fn(),
    applyTeamRunDelivery: vi.fn(),
    cancelTeamRunDeliveryConflicts: vi.fn(),
    commitTeamRunDelivery: vi.fn(),
    continueTeamRunDelivery: vi.fn(),
    resolveTeamRunDeliveryConflict: vi.fn(),
    teamDocuments: vi.fn(),
    teamRunDelivery: vi.fn(),
    teamRunDetail: vi.fn(),
    teamRuns: vi.fn(),
    triggerTeamCycle: vi.fn()
  }
}));

function deferred() {
  let resolve;
  const promise = new Promise((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

function detail(id, extra = {}) {
  return {
    run: { id },
    agents: [],
    tasks: [],
    messages: [],
    ...extra
  };
}

function renderController() {
  const dependencies = {
    toast: vi.fn(),
    confirm: vi.fn().mockResolvedValue(true),
    setScreenError: vi.fn()
  };
  return renderHook(() => useTeamRunController(dependencies));
}

afterEach(() => {
  vi.restoreAllMocks();
});

async function selectRun(result, id) {
  act(() => result.current.handleSelectTeamRun(id));
  await waitFor(() => expect(result.current.teamRunDetail?.run?.id).toBe(id));
}

describe("useTeamRunController request ownership", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.teamRuns.mockResolvedValue([]);
    api.teamRunDetail.mockImplementation(async (id) => detail(id));
    api.teamDocuments.mockImplementation(async (id) => [`${id}.md`]);
    api.teamRunDelivery.mockImplementation(async (id) => ({ source: { path: id } }));
  });

  it("keeps a late SSE refresh for run A from replacing run B detail or documents", async () => {
    const lateDetail = deferred();
    const lateDocuments = deferred();
    let runADetailCalls = 0;
    let runADocumentCalls = 0;
    api.teamRunDetail.mockImplementation((id) => {
      if (id === "run-a" && ++runADetailCalls === 2) return lateDetail.promise;
      return Promise.resolve(detail(id));
    });
    api.teamDocuments.mockImplementation((id) => {
      if (id === "run-a" && ++runADocumentCalls === 2) return lateDocuments.promise;
      return Promise.resolve([`${id}.md`]);
    });
    const { result } = renderController();
    await selectRun(result, "run-a");

    act(() => result.current.handleTeamEvent({
      type: "team.cycle.settled",
      team_run_id: "run-a"
    }));
    await selectRun(result, "run-b");

    await act(async () => {
      lateDetail.resolve(detail("run-a", { queueCount: 99 }));
      lateDocuments.resolve(["stale-a.md"]);
      await Promise.all([lateDetail.promise, lateDocuments.promise]);
    });

    expect(result.current.teamRunDetail.run.id).toBe("run-b");
    expect(result.current.teamRunDocuments).toEqual(["run-b.md"]);
    expect(api.teamRuns).toHaveBeenCalledTimes(1);
  });

  it("keeps a late decision refresh for run A from replacing run B state", async () => {
    const lateDetail = deferred();
    const lateDocuments = deferred();
    let refreshRunA = false;
    api.teamRunDetail.mockImplementation((id) => (
      id === "run-a" && refreshRunA
        ? lateDetail.promise
        : Promise.resolve(detail(id, id === "run-a" ? {
          decisionRequest: { id: "decision-a", revision: 1 }
        } : {}))
    ));
    api.teamDocuments.mockImplementation((id) => (
      id === "run-a" && refreshRunA
        ? lateDocuments.promise
        : Promise.resolve([`${id}.md`])
    ));
    api.answerTeamDecision.mockImplementation(async () => {
      refreshRunA = true;
      return { run: { id: "run-a" }, decisionRequest: null };
    });
    const { result } = renderController();
    await selectRun(result, "run-a");

    let answerPromise;
    act(() => {
      answerPromise = result.current.handleAnswerTeamDecision({ choice: "yes" });
    });
    await waitFor(() => expect(api.teamRunDetail).toHaveBeenCalledTimes(2));
    await selectRun(result, "run-b");

    await act(async () => {
      lateDetail.resolve(detail("run-a"));
      lateDocuments.resolve(["stale-a.md"]);
      await answerPromise;
    });

    expect(result.current.teamRunDetail.run.id).toBe("run-b");
    expect(result.current.teamRunDocuments).toEqual(["run-b.md"]);
    expect(api.teamRuns).toHaveBeenCalledTimes(1);
  });

  it("clears the previous run while detail and documents settle independently", async () => {
    const nextDetail = deferred();
    const nextDocuments = deferred();
    api.teamRunDetail.mockImplementation((id) => (
      id === "run-b" ? nextDetail.promise : Promise.resolve(detail(id))
    ));
    api.teamDocuments.mockImplementation((id) => (
      id === "run-b" ? nextDocuments.promise : Promise.resolve([`${id}.md`])
    ));
    const { result } = renderController();
    await selectRun(result, "run-a");

    act(() => result.current.handleSelectTeamRun("run-b"));
    await waitFor(() => expect(result.current.teamRunDetailLoading).toBe(true));
    expect(result.current.teamRunDetail).toBeNull();
    expect(result.current.teamRunDocuments).toEqual([]);

    await act(async () => {
      nextDetail.resolve(detail("run-b"));
      await nextDetail.promise;
    });
    expect(result.current.teamRunDetailLoading).toBe(false);
    expect(result.current.teamRunDetail.run.id).toBe("run-b");
    expect(result.current.teamRunDocuments).toEqual([]);

    await act(async () => {
      nextDocuments.resolve(["run-b.md"]);
      await nextDocuments.promise;
    });
    expect(result.current.teamRunDocuments).toEqual(["run-b.md"]);
  });

  it("ends loading on detail failure and retries when reloadKey changes", async () => {
    const setScreenError = vi.fn();
    const requestError = new Error("detail failed");
    api.teamRunDetail
      .mockRejectedValueOnce(requestError)
      .mockResolvedValueOnce(detail("run-a"));
    const { result, rerender } = renderHook(
      ({ reloadKey }) => useTeamRunController({
        toast: vi.fn(), confirm: vi.fn(), setScreenError, reloadKey
      }),
      { initialProps: { reloadKey: 0 } }
    );

    act(() => result.current.handleSelectTeamRun("run-a"));
    await waitFor(() => expect(result.current.teamRunDetailLoadError).toBe(true));
    expect(result.current.teamRunDetailLoading).toBe(false);
    expect(setScreenError).toHaveBeenCalledWith(requestError);

    rerender({ reloadKey: 1 });
    await waitFor(() => expect(result.current.teamRunDetail?.run?.id).toBe("run-a"));
    expect(result.current.teamRunDetailLoading).toBe(false);
    expect(result.current.teamRunDetailLoadError).toBe(false);
  });
});

describe("useTeamRunController manual cycle request identity", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.teamRuns.mockResolvedValue([]);
    api.teamRunDetail.mockImplementation(async (id) => detail(id));
    api.teamDocuments.mockImplementation(async (id) => [`${id}.md`]);
    api.teamRunDelivery.mockImplementation(async (id) => ({ source: { path: id } }));
  });

  it("reuses an id after an ambiguous failure and rotates it after success", async () => {
    vi.spyOn(crypto, "randomUUID")
      .mockReturnValueOnce("request-1")
      .mockReturnValueOnce("request-2");
    api.triggerTeamCycle
      .mockRejectedValueOnce(new Error("response lost"))
      .mockResolvedValue({});
    const { result } = renderController();
    await selectRun(result, "run-a");

    await act(async () => {
      expect(await result.current.handleTriggerTeamCycle({
        instruction: "  write docs  ", previous_cycle_id: "cycle-1"
      })).toBe(false);
      expect(await result.current.handleTriggerTeamCycle({
        instruction: "write docs", previous_cycle_id: "cycle-1"
      })).toBe(true);
      expect(await result.current.handleTriggerTeamCycle({
        instruction: "write docs", previous_cycle_id: "cycle-1"
      })).toBe(true);
    });

    expect(api.triggerTeamCycle.mock.calls.map(([, payload]) => payload)).toEqual([
      { instruction: "write docs", previous_cycle_id: "cycle-1", client_request_id: "request-1" },
      { instruction: "write docs", previous_cycle_id: "cycle-1", client_request_id: "request-1" },
      { instruction: "write docs", previous_cycle_id: "cycle-1", client_request_id: "request-2" }
    ]);
  });

  it("rotates the id when instruction, previous cycle, or selected run changes", async () => {
    vi.spyOn(crypto, "randomUUID")
      .mockReturnValueOnce("request-1")
      .mockReturnValueOnce("request-2")
      .mockReturnValueOnce("request-3")
      .mockReturnValueOnce("request-4");
    api.triggerTeamCycle.mockRejectedValue(new Error("response lost"));
    const { result } = renderController();
    await selectRun(result, "run-a");

    await act(async () => {
      await result.current.handleTriggerTeamCycle({ instruction: "one", previous_cycle_id: "cycle-1" });
      await result.current.handleTriggerTeamCycle({ instruction: "two", previous_cycle_id: "cycle-1" });
      await result.current.handleTriggerTeamCycle({ instruction: "two", previous_cycle_id: "cycle-2" });
    });
    await selectRun(result, "run-b");
    await act(async () => {
      await result.current.handleTriggerTeamCycle({ instruction: "two", previous_cycle_id: "cycle-2" });
    });

    expect(api.triggerTeamCycle.mock.calls.map(([runId, payload]) => [
      runId, payload.client_request_id
    ])).toEqual([
      ["run-a", "request-1"],
      ["run-a", "request-2"],
      ["run-a", "request-3"],
      ["run-b", "request-4"]
    ]);
  });
});

describe("useTeamRunController delivery", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.teamRuns.mockResolvedValue([]);
    api.teamRunDetail.mockImplementation(async (id) => detail(id));
    api.teamDocuments.mockResolvedValue([]);
  });

  it("owns delivery state and refreshes it after commit and apply", async () => {
    const initial = {
      available: true,
      can_apply: true,
      pending_commits: [{ sha: "a1" }],
      target: { branch: "main" }
    };
    const committed = { ...initial, pending_commits: [{ sha: "b2" }] };
    const applied = { ...initial, can_apply: false, pending_commits: [] };
    api.teamRunDelivery.mockResolvedValue(initial);
    api.commitTeamRunDelivery.mockResolvedValue(committed);
    api.applyTeamRunDelivery.mockResolvedValue(applied);
    const confirm = vi.fn().mockResolvedValue(true);
    const toast = vi.fn();
    const setScreenError = vi.fn();
    const { result } = renderHook(() => useTeamRunController({
      toast, confirm, setScreenError
    }));
    await selectRun(result, "run-a");
    await waitFor(() => expect(result.current.teamRunDelivery).toEqual(initial));

    await act(async () => {
      expect(await result.current.handleCommitTeamRunDelivery(" feat: dashboard ")).toBe(true);
    });
    expect(api.commitTeamRunDelivery).toHaveBeenCalledWith("run-a", "feat: dashboard");
    expect(result.current.teamRunDelivery).toEqual(committed);

    await act(async () => {
      expect(await result.current.handleApplyTeamRunDelivery()).toBe(true);
    });
    expect(confirm).toHaveBeenCalledWith(expect.objectContaining({
      title: "APPLY TEAM RUN CHANGES"
    }));
    expect(api.applyTeamRunDelivery).toHaveBeenCalledWith("run-a");
    expect(result.current.teamRunDelivery).toEqual(applied);
  });

  it("owns conflict resolution, continuation, and cancellation snapshots", async () => {
    const initial = {
      available: true,
      can_apply: true,
      pending_commits: [{ sha: "a1" }],
      target: { branch: "main" }
    };
    const conflicted = {
      ...initial,
      can_apply: false,
      conflict_session: {
        id: "session-1",
        files: [{ id: "conflict-1", path: "README.md", resolved: false }],
        can_continue: false
      }
    };
    const resolved = {
      ...conflicted,
      conflict_session: {
        ...conflicted.conflict_session,
        files: [{ id: "conflict-1", path: "README.md", resolved: true }],
        can_continue: true
      }
    };
    const applied = { ...initial, can_apply: false, pending_commits: [], conflict_session: null };
    api.teamRunDelivery.mockResolvedValue(initial);
    api.applyTeamRunDelivery.mockResolvedValue(conflicted);
    api.resolveTeamRunDeliveryConflict.mockResolvedValue(resolved);
    api.continueTeamRunDelivery.mockResolvedValue(applied);
    api.cancelTeamRunDeliveryConflicts.mockResolvedValue(initial);
    const confirm = vi.fn().mockResolvedValue(true);
    const toast = vi.fn();
    const setScreenError = vi.fn();
    const { result } = renderHook(() => useTeamRunController({
      toast, confirm, setScreenError
    }));
    await selectRun(result, "run-a");
    await waitFor(() => expect(result.current.teamRunDelivery).toEqual(initial));

    await act(async () => result.current.handleApplyTeamRunDelivery());
    expect(result.current.teamRunDelivery).toEqual(conflicted);
    expect(toast).toHaveBeenCalledWith("Repository conflicts need your resolution", "error");

    await act(async () => result.current.handleResolveTeamRunDeliveryConflict(
      "conflict-1",
      { mode: "manual", content: "merged" }
    ));
    expect(api.resolveTeamRunDeliveryConflict).toHaveBeenCalledWith(
      "run-a",
      "conflict-1",
      { mode: "manual", content: "merged" }
    );
    expect(result.current.teamRunDelivery).toEqual(resolved);

    await act(async () => result.current.handleCancelTeamRunDeliveryConflicts());
    expect(api.cancelTeamRunDeliveryConflicts).toHaveBeenCalledWith("run-a");
    expect(result.current.teamRunDelivery).toEqual(initial);

    await act(async () => result.current.handleApplyTeamRunDelivery());
    await act(async () => result.current.handleResolveTeamRunDeliveryConflict(
      "conflict-1",
      { mode: "manual", content: "merged" }
    ));
    await act(async () => result.current.handleContinueTeamRunDelivery());
    expect(api.continueTeamRunDelivery).toHaveBeenCalledWith("run-a");
    expect(result.current.teamRunDelivery).toEqual(applied);
  });
});
