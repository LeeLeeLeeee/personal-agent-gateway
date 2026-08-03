import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { api } from "../../../api/client.js";
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

const artifact = {
  id: "artifact-1",
  type: "report",
  title: "release-report.md",
  relative_path: "reports/release-report.md",
  mime_type: "text/markdown",
  size_bytes: 512,
  created_at: "2026-07-27T00:00:00Z"
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
    archiveMap: vi.fn().mockResolvedValue({
      nodes: [
        { id: "scope:global", kind: "scope", entity_id: "", label: "Shared Library", meta: "All personas" },
        { id: "persona:persona-1", kind: "persona", entity_id: "persona-1", label: "Operator", meta: "Production operations" },
        { id: "entry:entry-1", kind: "entry", entity_id: "entry-1", label: entry.title, meta: entry.kind, summary: entry.summary },
        { id: "request:request-1", kind: "request", entity_id: "request-1", label: request.title, meta: request.status, summary: request.reason },
        { id: "team_run:team-1", kind: "team_run", entity_id: "team-1", label: "Documentation team", meta: "waiting" },
        { id: "draft:draft-1", kind: "draft", entity_id: "draft-1", label: draft.title, meta: draft.kind, summary: draft.summary }
      ],
      edges: [
        { id: "persona-entry", source: "persona:persona-1", target: "entry:entry-1", kind: "uses" },
        { id: "persona-request", source: "persona:persona-1", target: "request:request-1", kind: "needs" },
        { id: "request-team", source: "request:request-1", target: "team_run:team-1", kind: "delegates" },
        { id: "team-draft", source: "team_run:team-1", target: "draft:draft-1", kind: "produced" }
      ]
    }),
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
    })
  };
}

describe("ArchiveView", () => {
  it("adds inner spacing around embedded artifact contents", () => {
    expect(styles).toMatch(
      /\.archive-artifacts\s*>\s*\.artifacts-view\s*\{[^}]*padding:\s*20px;/
    );
  });

  it("keeps artifact metadata on one line so cards stay equal height", () => {
    expect(styles).toMatch(
      /\.artifact-card-meta\s*\{[^}]*white-space:\s*nowrap;[^}]*overflow:\s*hidden;[^}]*text-overflow:\s*ellipsis;/
    );
  });

  it("makes the draft failure banner conspicuous with a warning border", () => {
    expect(styles).toMatch(
      /\.archive-request-failure\s*\{[^}]*border:\s*2px solid var\(--c-warn\);/
    );
  });

  it("explains the knowledge lifecycle separately from work artifacts", async () => {
    render(<ArchiveView client={makeClient()} artifacts={[artifact]} />);

    await screen.findByRole("heading", { name: "Archive" });

    expect(screen.getByRole("region", { name: "Knowledge lifecycle" }))
      .toHaveTextContent(/Requests.*Drafts.*Library/i);
    expect(screen.getByRole("region", { name: "Work outputs" }))
      .toHaveTextContent(/Artifacts.*separate/i);
  });

  it("shows managed artifacts and the Library boundary inside Archive", async () => {
    render(<ArchiveView client={makeClient()} artifacts={[artifact]} onArtifactChange={vi.fn()} />);

    await screen.findByRole("heading", { name: "Archive" });
    await userEvent.click(screen.getByRole("tab", { name: /Artifacts/ }));

    expect(screen.getByText("release-report.md")).toBeInTheDocument();
    expect(screen.getByText(/not automatically included in Library or Persona context/i)).toBeInTheDocument();
  });

  it("refreshes Archive artifacts after an embedded artifact is deleted", async () => {
    const onArtifactChange = vi.fn();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const deleteSpy = vi.spyOn(api, "deleteArtifact").mockResolvedValue(true);
    const deletedArtifact = {
      ...artifact,
      type: "document",
      mime_type: "application/zip"
    };

    render(
      <ArchiveView
        client={makeClient()}
        artifacts={[deletedArtifact]}
        onArtifactChange={onArtifactChange}
      />
    );

    await screen.findByRole("heading", { name: "Archive" });
    await userEvent.click(screen.getByRole("tab", { name: /Artifacts/ }));
    await userEvent.click(screen.getByRole("button", { name: "Open release-report.md" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(deleteSpy).toHaveBeenCalledWith("artifact-1"));
    await waitFor(() => expect(onArtifactChange).toHaveBeenCalledOnce());

    confirmSpy.mockRestore();
    deleteSpy.mockRestore();
  });

  it("shows published knowledge and renders an accessible map with distinct gap edges", async () => {
    const client = makeClient();
    const { container } = render(<ArchiveView client={client} />);

    expect(await screen.findByRole("heading", { name: "Archive" })).toBeInTheDocument();
    expect(screen.getByText("Deployment reference")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: /Map/ }));

    const graph = screen.getByRole("group", { name: "Archive knowledge map" });
    expect(within(graph).getAllByRole("button", { name: "Operator persona source" }))
      .not.toHaveLength(0);
    expect(within(graph).getByRole("button", { name: "Rollback checklist request node" })).toBeInTheDocument();
    expect(within(graph).getByText("DRAFT / LIBRARY")).toBeInTheDocument();
    expect(container.querySelector(".archive-map-edge-needs")).toBeInTheDocument();

    await userEvent.click(within(graph).getByRole("button", {
      name: "Rollback checklist request node"
    }));
    expect(screen.getByText(request.reason)).toBeInTheDocument();
  });

  it("does not count source-only personas as knowledge map items", async () => {
    const client = makeClient();
    client.archiveEntries.mockResolvedValue([]);
    client.knowledgeRequests.mockResolvedValue([]);
    client.archiveMap.mockResolvedValue({
      nodes: [
        { id: "scope:global", kind: "scope", entity_id: "", label: "Shared Library", meta: "All personas" },
        ...Array.from({ length: 19 }, (_, index) => ({
          id: `persona:${index}`,
          kind: "persona",
          entity_id: String(index),
          label: `Persona ${index}`,
          meta: "Agent persona"
        }))
      ],
      edges: []
    });

    render(<ArchiveView client={client} />);

    await screen.findByRole("heading", { name: "Archive" });
    const mapTab = screen.getByRole("tab", { name: "Map" });
    expect(mapTab).toHaveTextContent("MAP 0");

    await userEvent.click(mapTab);
    expect(screen.getByText(/Publish a Library entry or wait for a persona knowledge request/i))
      .toBeInTheDocument();
  });

  it("renders persona-specific knowledge lanes without unrelated personas", async () => {
    const client = makeClient();
    client.archiveMap.mockResolvedValue({
      nodes: [
        { id: "persona:connected", kind: "persona", entity_id: "connected", label: "Operator", meta: "Operations" },
        { id: "persona:unused", kind: "persona", entity_id: "unused", label: "Unused persona", meta: "Unrelated" },
        { id: "request:request-1", kind: "request", entity_id: request.id, label: request.title, meta: "open" },
        { id: "team_run:team-1", kind: "team_run", entity_id: documentationTeam.id, label: "Documentation team", meta: "waiting" },
        { id: "draft:draft-1", kind: "draft", entity_id: draft.id, label: draft.title, meta: draft.kind }
      ],
      edges: [
        { id: "gap", source: "persona:connected", target: "request:request-1", kind: "needs" },
        { id: "delegated", source: "request:request-1", target: "team_run:team-1", kind: "delegates" },
        { id: "draft", source: "team_run:team-1", target: "draft:draft-1", kind: "produced" }
      ]
    });

    const { container } = render(<ArchiveView client={client} />);

    await screen.findByRole("heading", { name: "Archive" });
    await userEvent.click(screen.getByRole("tab", { name: "Map" }));

    const graph = screen.getByRole("group", { name: "Archive knowledge map" });
    const section = within(graph).getByRole("group", {
      name: "Persona-specific knowledge"
    });
    const lane = within(section).getByRole("group", {
      name: "Rollback checklist knowledge lane"
    });

    expect(within(lane).getByRole("button", { name: "Operator persona source" }))
      .toBeInTheDocument();
    expect(within(graph).queryByText("Unused persona")).not.toBeInTheDocument();
    expect(within(lane).getByText("GAP")).toBeInTheDocument();
    expect(within(lane).getByText("DELEGATED")).toBeInTheDocument();
    expect(within(lane).getByText("DRAFT")).toBeInTheDocument();
    expect(container.querySelector("[marker-end]")).not.toBeInTheDocument();
  });

  it("separates shared and automation knowledge lanes", async () => {
    const client = makeClient();
    client.archiveMap.mockResolvedValue({
      nodes: [
        { id: "scope:global", kind: "scope", entity_id: "", label: "Shared Library", meta: "All personas" },
        { id: "hook:release", kind: "hook", entity_id: "release", label: "Release hook", meta: "Automation hook" },
        { id: "entry:shared", kind: "entry", entity_id: "shared", label: "Shared runbook", meta: "procedure" },
        { id: "draft:release", kind: "draft", entity_id: "release", label: "Release notes", meta: "reference" }
      ],
      edges: [
        { id: "published", source: "scope:global", target: "entry:shared", kind: "uses" },
        { id: "automated", source: "hook:release", target: "draft:release", kind: "produced" }
      ]
    });

    render(<ArchiveView client={client} />);

    await screen.findByRole("heading", { name: "Archive" });
    await userEvent.click(screen.getByRole("tab", { name: "Map" }));

    const graph = screen.getByRole("group", { name: "Archive knowledge map" });
    const shared = within(graph).getByRole("group", { name: "Shared knowledge" });
    const automation = within(graph).getByRole("group", { name: "Automation knowledge" });

    expect(within(shared).getByText("PUBLISHED")).toBeInTheDocument();
    expect(within(automation).getByText("DRAFT")).toBeInTheDocument();
    expect(within(graph).queryByRole("group", { name: "Persona-specific knowledge" }))
      .not.toBeInTheDocument();
  });

  it("zooms, pans, and fits the knowledge map viewport", async () => {
    const { container } = render(<ArchiveView client={makeClient()} />);

    await screen.findByRole("heading", { name: "Archive" });
    await userEvent.click(screen.getByRole("tab", { name: /Map/ }));

    const graph = screen.getByRole("group", { name: "Archive knowledge map" });
    expect(graph).toHaveAttribute("viewBox", "0 0 1270 680");

    const viewport = container.querySelector(".archive-map-viewport");
    expect(viewport).toHaveAttribute("transform", "translate(0 0) scale(1)");

    await userEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(viewport).toHaveAttribute("transform", "translate(0 0) scale(1.2)");

    fireEvent.pointerDown(
      graph,
      { pointerId: 1, clientX: 100, clientY: 100 }
    );
    fireEvent.pointerMove(
      graph,
      { pointerId: 1, clientX: 140, clientY: 125 }
    );
    fireEvent.pointerUp(
      graph,
      { pointerId: 1, clientX: 140, clientY: 125 }
    );
    expect(viewport).not.toHaveAttribute("transform", "translate(0 0) scale(1.2)");

    await userEvent.click(screen.getByRole("button", { name: "Fit map" }));
    expect(viewport).toHaveAttribute("data-fitted", "true");
  });

  it("zooms the knowledge map with the mouse wheel", async () => {
    const { container } = render(<ArchiveView client={makeClient()} />);

    await screen.findByRole("heading", { name: "Archive" });
    await userEvent.click(screen.getByRole("tab", { name: /Map/ }));

    const graph = screen.getByRole("group", { name: "Archive knowledge map" });
    const viewport = container.querySelector(".archive-map-viewport");

    fireEvent.wheel(graph, { deltaY: -100, clientX: 300, clientY: 200 });
    expect(viewport).toHaveAttribute("transform", expect.stringContaining("scale(1.2)"));

    fireEvent.wheel(graph, { deltaY: 100, clientX: 300, clientY: 200 });
    expect(viewport).toHaveAttribute("transform", expect.stringContaining("scale(1)"));
  });

  it("keeps team output private until the user reviews and publishes the draft", async () => {
    const client = makeClient();
    render(<ArchiveView client={client} />);

    await screen.findByRole("heading", { name: "Archive" });
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

  it("delegates an active knowledge request to a triggered documentation team", async () => {
    const client = makeClient();
    render(<ArchiveView client={client} />);

    await screen.findByRole("heading", { name: "Archive" });
    await userEvent.click(screen.getByRole("tab", { name: /Requests/ }));
    await userEvent.click(screen.getByRole("button", {
      name: "Send Rollback checklist to documentation team"
    }));

    await waitFor(() => expect(client.delegateKnowledgeRequest).toHaveBeenCalledWith(
      "request-1",
      "team-1"
    ));
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

    render(<ArchiveView client={client} artifacts={[]} />);

    await screen.findByRole("heading", { name: "Archive" });
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
    render(<ArchiveView client={makeClient()} artifacts={[]} />);

    await screen.findByRole("heading", { name: "Archive" });
    await userEvent.click(screen.getByRole("tab", { name: /Requests/ }));

    expect(screen.queryByText(/DRAFT FAILED/)).not.toBeInTheDocument();
  });

  it("turns a persona request into a user-authored Library draft and fulfills it only on publish", async () => {
    const client = makeClient();
    render(<ArchiveView client={client} />);

    await screen.findByRole("heading", { name: "Archive" });
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
});
