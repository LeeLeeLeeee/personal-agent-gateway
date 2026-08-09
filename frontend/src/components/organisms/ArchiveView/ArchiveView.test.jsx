import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ArchiveView } from "./index.jsx";

const styles = readFileSync(
  resolve(process.cwd(), "../src/personal_agent_gateway/static/styles.css"),
  "utf8"
);

const entry = {
  id: "entry-1",
  kind: "reference",
  title: "Deployment reference",
  summary: "Sources verified for production deployments.",
  content_markdown: "# Deployment reference\n\nUse the release checklist.",
  tags: ["deployment"],
  source_urls: ["https://example.com/releases"],
  status: "published",
  current_revision: 1,
  persona_ids: ["persona-1"],
  created_at: "2026-07-25T00:00:00Z",
  updated_at: "2026-07-25T00:00:00Z"
};

const request = {
  id: "request-1",
  title: "Rollback checklist",
  reason: "The operator persona has no verified rollback procedure.",
  suggested_outline: ["Signals", "Rollback steps", "Verification"],
  source_hints: ["Runbook and deployment provider docs"],
  requested_by_persona_id: "persona-1",
  requested_by_persona_name: "Operator",
  session_id: "session-1",
  team_run_id: null,
  assigned_team_run_id: null,
  status: "open",
  fulfilled_by_entry_id: null,
  created_at: "2026-07-26T00:00:00Z",
  updated_at: "2026-07-26T00:00:00Z"
};

const draft = {
  ...entry,
  id: "draft-1",
  kind: "checklist",
  title: request.title,
  summary: "Team-researched rollback guidance for user review.",
  content_markdown: "# Rollback checklist\n\nVerify the release marker before rollback.",
  tags: ["rollback"],
  source_urls: ["https://example.com/runbook"],
  status: "draft",
  persona_ids: ["persona-1"],
  origin_source_type: "knowledge_request",
  origin_source_id: request.id,
  origin_team_run_id: "team-1",
  origin_request_id: request.id
};

const documentationTeam = {
  id: "team-1",
  goal: "Research and prepare Library drafts",
  team_name: "Documentation team",
  status: "waiting",
  lifecycle_mode: "continuous",
  run_mode: "plan_and_execute",
  execution_policy: "triggered"
};

function makeClient() {
  return {
    archiveEntries: vi.fn().mockImplementation(({ status = "published" } = {}) => (
      Promise.resolve(status === "draft" ? [draft] : [entry])
    )),
    personas: vi.fn().mockResolvedValue([
      { id: "persona-1", name: "Operator", role: "Production operations" }
    ]),
    teamRuns: vi.fn().mockResolvedValue([documentationTeam]),
    knowledgeRequests: vi.fn().mockResolvedValue([request]),
    archiveEntryRevisions: vi.fn().mockResolvedValue([
      { id: "revision-1", revision: 1, change_summary: "Initial publication", created_at: entry.created_at }
    ]),
    publishArchiveEntry: vi.fn().mockResolvedValue({
      ...entry,
      id: "entry-2",
      title: request.title
    }),
    reviseArchiveEntry: vi.fn().mockResolvedValue({
      ...draft,
      status: "published",
      current_revision: 2
    }),
    delegateKnowledgeRequest: vi.fn().mockResolvedValue({
      request: {
        ...request,
        status: "in_progress",
        assigned_team_run_id: documentationTeam.id
      },
      cycle_request: {
        id: "cycle-request-1",
        source_type: "knowledge_request",
        source_id: request.id
      }
    }),
    setKnowledgeRequestStatus: vi.fn().mockResolvedValue({
      ...request,
      status: "in_progress"
    }),
    deleteArchiveEntry: vi.fn().mockResolvedValue(true)
  };
}

describe("ArchiveView", () => {
  it("makes the draft failure banner conspicuous with a warning border", () => {
    expect(styles).toMatch(
      /\.archive-request-failure\s*\{[^}]*border:\s*2px solid var\(--c-warn\);/
    );
  });

  it("presents the knowledge lifecycle as Published, Drafts, and Requests", async () => {
    render(<ArchiveView client={makeClient()} />);

    expect(screen.getByRole("region", { name: "Library" })).toBeInTheDocument();
    expect(screen.getByLabelText("Library totals")).toBeInTheDocument();
    expect(screen.getByLabelText("How Library works")).toBeInTheDocument();
    expect(screen.getByRole("tablist", { name: "Library sections" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("LOADING LIBRARY…");
    expect(await screen.findByRole("heading", { name: "Library" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Published/ })).toHaveAttribute(
      "aria-selected",
      "true"
    );
    expect(screen.getByRole("tab", { name: /Drafts/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Requests/ })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /Artifacts/ })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Knowledge lifecycle" }))
      .toHaveTextContent(/Requests.*Drafts.*Library/i);
    expect(screen.queryByRole("region", { name: "Work outputs" })).not.toBeInTheDocument();
  });

  it("removes Map and loads documentation teams only once on Requests", async () => {
    const client = makeClient();
    render(<ArchiveView client={client} />);

    await screen.findByRole("heading", { name: "Library" });

    expect(screen.queryByRole("tab", { name: "Map" })).not.toBeInTheDocument();
    expect(client.teamRuns).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("tab", { name: /Requests/ }));
    await waitFor(() => expect(client.teamRuns).toHaveBeenCalledOnce());

    await userEvent.click(screen.getByRole("tab", { name: "Published" }));
    await userEvent.click(screen.getByRole("tab", { name: /Requests/ }));
    expect(client.teamRuns).toHaveBeenCalledOnce();
  });

  it("keeps a base Library load error after Requests team loading succeeds", async () => {
    const client = makeClient();
    client.archiveEntries.mockImplementation(({ status = "published" } = {}) => (
      status === "draft"
        ? Promise.reject(new Error("Draft Library unavailable"))
        : Promise.resolve([entry])
    ));

    render(<ArchiveView client={client} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Draft Library unavailable");
    await userEvent.click(screen.getByRole("tab", { name: /Requests/ }));
    await waitFor(() => expect(client.teamRuns).toHaveBeenCalledOnce());

    expect(screen.getByRole("alert")).toHaveTextContent("Draft Library unavailable");
  });

  it("keeps direct Request actions available and retries Team Run loading", async () => {
    const client = makeClient();
    client.teamRuns
      .mockReset()
      .mockRejectedValueOnce(new Error("Team Runs unavailable"))
      .mockResolvedValueOnce([documentationTeam]);

    render(<ArchiveView client={client} />);

    await screen.findByRole("heading", { name: "Library" });
    await userEvent.click(screen.getByRole("tab", { name: /Requests/ }));

    const retry = await screen.findByRole("button", { name: "Retry team loading" });
    expect(client.archiveEntries).toHaveBeenCalledTimes(2);
    expect(client.personas).toHaveBeenCalledOnce();
    expect(client.knowledgeRequests).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", {
      name: `Write ${request.title} in Library`
    })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Later" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Dismiss" })).toBeEnabled();
    expect(screen.getByRole("button", {
      name: `Send ${request.title} to documentation team`
    })).toBeDisabled();

    await userEvent.click(retry);

    await waitFor(() => expect(client.teamRuns).toHaveBeenCalledTimes(2));
    expect(client.archiveEntries).toHaveBeenCalledTimes(2);
    expect(client.personas).toHaveBeenCalledOnce();
    expect(client.knowledgeRequests).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", {
      name: `Send ${request.title} to documentation team`
    })).toBeEnabled();
  });

  it("ignores a stale Team Run response after the client changes", async () => {
    let resolveStaleTeams;
    const staleTeams = new Promise((resolve) => {
      resolveStaleTeams = resolve;
    });
    const nextDocumentationTeam = {
      ...documentationTeam,
      id: "team-2",
      team_name: "Replacement documentation team"
    };
    const firstClient = makeClient();
    const nextClient = makeClient();
    firstClient.teamRuns.mockReturnValue(staleTeams);
    nextClient.teamRuns.mockResolvedValue([nextDocumentationTeam]);

    const { rerender } = render(<ArchiveView client={firstClient} />);

    await screen.findByRole("heading", { name: "Library" });
    await userEvent.click(screen.getByRole("tab", { name: /Requests/ }));
    await waitFor(() => expect(firstClient.teamRuns).toHaveBeenCalledOnce());

    rerender(<ArchiveView client={nextClient} />);

    expect(await screen.findByRole("option", {
      name: "Replacement documentation team"
    })).toBeInTheDocument();

    await act(async () => {
      resolveStaleTeams([documentationTeam]);
      await staleTeams;
    });

    expect(screen.queryByRole("option", {
      name: "Documentation team"
    })).not.toBeInTheDocument();
    expect(screen.getByRole("option", {
      name: "Replacement documentation team"
    })).toBeInTheDocument();
  });

  it("keeps team output private until the user reviews and publishes the draft", async () => {
    const client = makeClient();
    render(<ArchiveView client={client} />);

    await screen.findByRole("heading", { name: "Library" });
    await userEvent.click(screen.getByRole("tab", { name: /Drafts/ }));
    await userEvent.click(screen.getByRole("button", {
      name: "Review Rollback checklist draft"
    }));

    expect(await screen.findByRole("heading", { name: "Review team draft" }))
      .toBeInTheDocument();
    expect(screen.getByText("FROM REQUEST")).toBeInTheDocument();
    expect(client.reviseArchiveEntry).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Review & publish" }));

    await waitFor(() => expect(client.reviseArchiveEntry).toHaveBeenCalledWith(
      "draft-1",
      expect.objectContaining({
        request_id: "request-1",
        content_markdown: draft.content_markdown
      })
    ));
  });

  it("previews the current private draft in a modal before publishing", async () => {
    render(<ArchiveView client={makeClient()} />);

    await screen.findByRole("heading", { name: "Library" });
    await userEvent.click(screen.getByRole("tab", { name: /Drafts/ }));
    await userEvent.click(screen.getByRole("button", {
      name: "Review Rollback checklist draft"
    }));
    await userEvent.clear(screen.getByLabelText("Content"));
    await userEvent.type(
      screen.getByLabelText("Content"),
      "# Rollback checklist\n\nPreview the revised marker before publishing."
    );
    await userEvent.click(screen.getByRole("button", { name: "Preview draft" }));

    const dialog = screen.getByRole("dialog", { name: "Preview: Rollback checklist" });
    expect(within(dialog).getByText("Preview the revised marker before publishing."))
      .toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole("button", {
      name: "Close draft preview"
    }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("lets the user delete a private Team draft after confirmation", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const client = makeClient();
    render(<ArchiveView client={client} />);

    await screen.findByRole("heading", { name: "Library" });
    await userEvent.click(screen.getByRole("tab", { name: /Drafts/ }));
    await userEvent.click(screen.getByRole("button", { name: "Review Rollback checklist draft" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete draft" }));

    await waitFor(() => expect(client.deleteArchiveEntry).toHaveBeenCalledWith("draft-1"));
    expect(screen.getByRole("heading", { name: "Select a team draft" })).toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  it("delegates an active knowledge request to a triggered documentation team", async () => {
    const client = makeClient();
    render(<ArchiveView client={client} />);

    await screen.findByRole("heading", { name: "Library" });
    await userEvent.click(screen.getByRole("tab", { name: /Requests/ }));
    const send = screen.getByRole("button", {
      name: "Send Rollback checklist to documentation team"
    });
    await waitFor(() => expect(send).toBeEnabled());
    await userEvent.click(send);

    await waitFor(() => expect(client.delegateKnowledgeRequest).toHaveBeenCalledWith(
      "request-1",
      "team-1"
    ));
    await waitFor(() => expect(client.knowledgeRequests).toHaveBeenCalledTimes(2));
    expect(client.teamRuns).toHaveBeenCalledOnce();
  });

  it("shows why a delegated Team Run produced no draft", async () => {
    const lastDraftFailedAt = "2026-08-03T00:42:36.123456+00:00";
    const formattedDraftFailedAt = new Intl.DateTimeFormat(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric"
    }).format(new Date(lastDraftFailedAt));
    const client = makeClient();
    client.knowledgeRequests = vi.fn().mockResolvedValue([
      {
        ...request,
        status: "open",
        assigned_team_run_id: documentationTeam.id,
        last_draft_error_code: "draft_contract_violation",
        last_draft_error_message:
          "Team response must contain exactly one Library Draft marker",
        last_draft_failed_at: lastDraftFailedAt,
        last_draft_cycle_id: "cycle-1"
      }
    ]);

    render(<ArchiveView client={client} />);

    await screen.findByRole("heading", { name: "Library" });
    await userEvent.click(screen.getByRole("tab", { name: /Requests/ }));

    expect(screen.getByText(/DRAFT FAILED/)).toHaveTextContent(
      "draft_contract_violation"
    );
    expect(screen.getByText(/DRAFT FAILED/)).toHaveTextContent(
      formattedDraftFailedAt
    );
    expect(screen.getByText(/DRAFT FAILED/)).not.toHaveTextContent(
      lastDraftFailedAt
    );
    expect(
      screen.getByText(/exactly one Library Draft marker/)
    ).toBeInTheDocument();
    expect(screen.getByText(/CYCLE cycle-1/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: `Send ${request.title} to documentation team` })
    ).toBeInTheDocument();
  });

  it("shows no failure banner when the request has never failed", async () => {
    render(<ArchiveView client={makeClient()} />);

    await screen.findByRole("heading", { name: "Library" });
    await userEvent.click(screen.getByRole("tab", { name: /Requests/ }));

    expect(screen.queryByText(/DRAFT FAILED/)).not.toBeInTheDocument();
  });

  it("turns a persona request into a user-authored Library draft and fulfills it only on publish", async () => {
    const client = makeClient();
    render(<ArchiveView client={client} />);

    await screen.findByRole("heading", { name: "Library" });
    await userEvent.click(screen.getByRole("tab", { name: /Requests/ }));
    await userEvent.click(screen.getByRole("button", {
      name: "Write Rollback checklist in Library"
    }));

    expect(await screen.findByRole("heading", { name: "Requested Library entry" })).toBeInTheDocument();
    expect(screen.getByLabelText("Title")).toHaveValue("Rollback checklist");
    expect(screen.getByLabelText("Content").value).toContain("## Signals");
    expect(client.publishArchiveEntry).not.toHaveBeenCalled();
    await waitFor(() => expect(client.setKnowledgeRequestStatus).toHaveBeenCalledWith(
      "request-1",
      "in_progress"
    ));

    await userEvent.clear(screen.getByLabelText("Content"));
    await userEvent.type(
      screen.getByLabelText("Content"),
      "# Rollback checklist\n\nVerify the release marker before rollback."
    );
    await userEvent.click(screen.getByRole("button", { name: "Publish to Library" }));

    await waitFor(() => expect(client.publishArchiveEntry).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "Rollback checklist",
        content_markdown: "# Rollback checklist\n\nVerify the release marker before rollback.",
        persona_ids: ["persona-1"],
        request_id: "request-1"
      })
    ));
  });

  it("lets the user delete a published Library document after confirmation", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const client = makeClient();
    render(<ArchiveView client={client} />);

    await screen.findByRole("heading", { name: "Library" });
    await userEvent.click(screen.getByRole("button", { name: /Deployment reference/ }));
    await userEvent.click(screen.getByRole("button", { name: "Delete document" }));

    await waitFor(() => expect(client.deleteArchiveEntry).toHaveBeenCalledWith("entry-1"));
    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining("cannot be undone"));
    confirmSpy.mockRestore();
  });

  it("leaves a published Library document alone when the confirmation is declined", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const client = makeClient();
    render(<ArchiveView client={client} />);

    await screen.findByRole("heading", { name: "Library" });
    await userEvent.click(screen.getByRole("button", { name: /Deployment reference/ }));
    await userEvent.click(screen.getByRole("button", { name: "Delete document" }));

    expect(client.deleteArchiveEntry).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

});
