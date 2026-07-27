import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ArchiveView } from "./index.jsx";

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
  it("shows managed artifacts and the Library boundary inside Archive", async () => {
    render(<ArchiveView client={makeClient()} artifacts={[artifact]} onArtifactChange={vi.fn()} />);

    await screen.findByRole("heading", { name: "Archive" });
    await userEvent.click(screen.getByRole("tab", { name: /Artifacts/ }));

    expect(screen.getByText("release-report.md")).toBeInTheDocument();
    expect(screen.getByText(/not automatically included in Library or Persona context/i)).toBeInTheDocument();
  });

  it("shows published knowledge and renders an accessible map with distinct gap edges", async () => {
    const client = makeClient();
    const { container } = render(<ArchiveView client={client} />);

    expect(await screen.findByRole("heading", { name: "Archive" })).toBeInTheDocument();
    expect(screen.getByText("Deployment reference")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: /Map/ }));

    const graph = screen.getByRole("group", { name: "Archive knowledge map" });
    expect(within(graph).getByRole("button", { name: "Operator persona node" })).toBeInTheDocument();
    expect(within(graph).getByRole("button", { name: "Rollback checklist request node" })).toBeInTheDocument();
    expect(container.querySelector(".archive-map-edge-needs")).toBeInTheDocument();

    await userEvent.click(within(graph).getByRole("button", {
      name: "Rollback checklist request node"
    }));
    expect(screen.getByText(request.reason)).toBeInTheDocument();
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
