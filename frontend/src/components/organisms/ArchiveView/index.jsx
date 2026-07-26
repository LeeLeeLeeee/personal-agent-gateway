import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../../../api/client.js";
import { Button } from "../../atoms/Button/index.jsx";

const ENTRY_KINDS = [
  ["procedure", "Procedure"],
  ["search_method", "Search method"],
  ["implementation_pattern", "Implementation pattern"],
  ["reference", "Reference"],
  ["checklist", "Checklist"]
];

const ACTIVE_REQUEST_STATUSES = new Set(["open", "in_progress"]);

const EMPTY_FORM = {
  kind: "procedure",
  title: "",
  summary: "",
  content: "",
  tags: "",
  sourceUrls: "",
  scope: "shared",
  personaIds: [],
  changeSummary: "",
  requestId: null
};

function formFromEntry(entry) {
  return {
    kind: entry.kind,
    title: entry.title,
    summary: entry.summary || "",
    content: entry.content_markdown || "",
    tags: (entry.tags || []).join(", "),
    sourceUrls: (entry.source_urls || []).join("\n"),
    scope: entry.persona_ids?.length ? "personas" : "shared",
    personaIds: entry.persona_ids || [],
    changeSummary: "",
    requestId: entry.origin_request_id || null
  };
}

function formFromRequest(request) {
  const sections = (request.suggested_outline || [])
    .map((item) => `## ${item}\n\n<!-- Add verified guidance here. -->`)
    .join("\n\n");
  const content = `# ${request.title}\n\n${sections}`.trim();
  const personaIds = request.requested_by_persona_id
    ? [request.requested_by_persona_id]
    : [];
  return {
    ...EMPTY_FORM,
    title: request.title,
    summary: request.reason,
    content,
    sourceUrls: (request.source_hints || []).join("\n"),
    scope: personaIds.length ? "personas" : "shared",
    personaIds,
    requestId: request.id
  };
}

function splitValues(value) {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function kindLabel(kind) {
  return ENTRY_KINDS.find(([value]) => value === kind)?.[1] || kind;
}

function statusLabel(status) {
  return String(status || "").replaceAll("_", " ").toUpperCase();
}

function originLabel(sourceType) {
  if (sourceType === "knowledge_request") return "REQUEST";
  if (sourceType === "hook") return "HOOK";
  return statusLabel(sourceType || "team");
}

function errorDetail(error) {
  if (typeof error?.detail === "string") return error.detail;
  if (error instanceof Error) return error.message;
  return "Archive request failed";
}

function formatDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric"
  }).format(new Date(value));
}

function graphLayout(nodes) {
  const groups = {
    source: nodes.filter((node) => (
      node.kind === "scope" || node.kind === "persona" || node.kind === "hook"
    )),
    request: nodes.filter((node) => node.kind === "request"),
    team: nodes.filter((node) => node.kind === "team_run"),
    knowledge: nodes.filter((node) => node.kind === "entry" || node.kind === "draft")
  };
  const positions = new Map();
  const columns = [
    ["source", 40],
    ["request", 350],
    ["team", 660],
    ["knowledge", 970]
  ];
  for (const [group, x] of columns) {
    groups[group].forEach((node, index) => {
      positions.set(node.id, { x, y: 78 + (index * 94) });
    });
  }
  const rowCount = Math.max(
    groups.source.length,
    groups.request.length,
    groups.team.length,
    groups.knowledge.length,
    2
  );
  return {
    groups,
    positions,
    height: 94 + (rowCount * 94)
  };
}

function edgePath(source, target) {
  const sourceX = source.x + 260;
  const sourceY = source.y + 34;
  const targetX = target.x;
  const targetY = target.y + 34;
  const middleX = sourceX + ((targetX - sourceX) / 2);
  return `M ${sourceX} ${sourceY} C ${middleX} ${sourceY}, ${middleX} ${targetY}, ${targetX} ${targetY}`;
}

function ArchiveMap({
  graph,
  entries,
  drafts,
  requests,
  selectedNodeId,
  onSelect,
  onEditEntry,
  onWriteRequest
}) {
  const nodes = graph?.nodes || [];
  const edges = graph?.edges || [];
  const layout = useMemo(() => graphLayout(nodes), [nodes]);
  const selected = nodes.find((node) => node.id === selectedNodeId) || null;
  const selectedEntry = ["entry", "draft"].includes(selected?.kind)
    ? [...entries, ...drafts].find((entry) => entry.id === selected.entity_id)
    : null;
  const selectedRequest = selected?.kind === "request"
    ? requests.find((request) => request.id === selected.entity_id)
    : null;
  const publishedCount = nodes.filter((node) => node.kind === "entry").length;
  const draftCount = nodes.filter((node) => node.kind === "draft").length;
  const knowledgeNodeCount = publishedCount + draftCount + layout.groups.request.length;

  return (
    <div className="archive-map-layout">
      <div className="archive-map-toolbar">
        <div className="archive-map-legend" aria-label="Map legend">
          <span><i className="archive-legend-line" />USES PUBLISHED KNOWLEDGE</span>
          <span><i className="archive-legend-line archive-legend-line-needs" />NEEDS USER DOCUMENT</span>
          <span><i className="archive-legend-line archive-legend-line-produced" />PRODUCES PRIVATE DRAFT</span>
        </div>
        <span className="mono archive-map-count">
          {publishedCount} DOCS · {draftCount} DRAFTS · {layout.groups.request.length} GAPS
        </span>
      </div>

      <div className="archive-map-scroll">
        {knowledgeNodeCount ? (
          <svg
            className="archive-map-canvas"
            viewBox={`0 0 1270 ${layout.height}`}
            role="group"
            aria-label="Archive knowledge map"
            aria-describedby="archive-map-description"
          >
            <title id="archive-map-title">Archive knowledge map</title>
            <desc id="archive-map-description">
              Personas and hooks lead to knowledge requests, documentation teams, private drafts,
              and user-published Library entries.
            </desc>
            <defs>
              <marker
                id="archive-map-arrow"
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="7"
                markerHeight="7"
                orient="auto-start-reverse"
              >
                <path d="M 0 0 L 10 5 L 0 10 z" />
              </marker>
            </defs>
            <text className="archive-map-column-label" x="40" y="38">SOURCE</text>
            <text className="archive-map-column-label" x="350" y="38">KNOWLEDGE REQUEST</text>
            <text className="archive-map-column-label" x="660" y="38">DOCUMENTATION TEAM</text>
            <text className="archive-map-column-label" x="970" y="38">LIBRARY</text>
            {edges.map((edge) => {
              const source = layout.positions.get(edge.source);
              const target = layout.positions.get(edge.target);
              if (!source || !target) return null;
              return (
                <path
                  key={edge.id}
                  d={edgePath(source, target)}
                  className={`archive-map-edge archive-map-edge-${edge.kind}`}
                  markerEnd="url(#archive-map-arrow)"
                />
              );
            })}
            {nodes.map((node) => {
              const position = layout.positions.get(node.id);
              if (!position) return null;
              const selectedClass = selectedNodeId === node.id ? " is-selected" : "";
              return (
                <foreignObject
                  key={node.id}
                  x={position.x}
                  y={position.y}
                  width="260"
                  height="68"
                >
                  <div xmlns="http://www.w3.org/1999/xhtml" className="archive-map-node-wrap">
                    <button
                      type="button"
                      className={`archive-map-node archive-map-node-${node.kind}${selectedClass}`}
                      aria-label={`${node.label} ${node.kind} node`}
                      aria-pressed={selectedNodeId === node.id}
                      onClick={() => onSelect(node.id)}
                    >
                      <span className="archive-map-node-kind">{node.kind}</span>
                      <span className="archive-map-node-title">{node.label}</span>
                      <span className="archive-map-node-meta">{node.meta || "—"}</span>
                    </button>
                  </div>
                </foreignObject>
              );
            })}
          </svg>
        ) : (
          <div className="archive-empty archive-map-empty">
            Publish a Library entry or wait for a persona knowledge request to build the map.
          </div>
        )}
      </div>

      <aside className="archive-map-detail" aria-live="polite">
        {selected ? (
          <>
            <div className="archive-map-detail-k mono">{selected.kind.toUpperCase()} · {selected.meta}</div>
            <h2>{selected.label}</h2>
            <p>{selected.summary || "Select a connected document or gap to inspect it."}</p>
            {selectedEntry ? (
              <Button size="btn-sm" onClick={() => onEditEntry(selectedEntry)}>
                {selectedEntry.status === "draft" ? "Review draft" : "Open in Library"}
              </Button>
            ) : null}
            {selectedRequest ? (
              <Button
                size="btn-sm"
                variant="primary"
                onClick={() => onWriteRequest(selectedRequest)}
              >
                Write in Library
              </Button>
            ) : null}
          </>
        ) : (
          <>
            <div className="archive-map-detail-k mono">MAP INSPECTOR</div>
            <p>Select a node to inspect its scope, document, or unresolved knowledge gap.</p>
          </>
        )}
      </aside>
    </div>
  );
}

export function ArchiveView({ client = api }) {
  const [tab, setTab] = useState("library");
  const [entries, setEntries] = useState([]);
  const [drafts, setDrafts] = useState([]);
  const [personas, setPersonas] = useState([]);
  const [requests, setRequests] = useState([]);
  const [teamRuns, setTeamRuns] = useState([]);
  const [selectedTeams, setSelectedTeams] = useState({});
  const [graph, setGraph] = useState({ nodes: [], edges: [] });
  const [loading, setLoading] = useState(true);
  const [listLoading, setListLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [requestBusyId, setRequestBusyId] = useState(null);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState("");
  const [query, setQuery] = useState("");
  const [kindFilter, setKindFilter] = useState("");
  const [requestFilter, setRequestFilter] = useState("active");
  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [revisions, setRevisions] = useState([]);
  const [selectedNodeId, setSelectedNodeId] = useState(null);

  const loadData = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true);
    setError(null);
    try {
      const [
        nextEntries,
        nextDrafts,
        nextPersonas,
        nextRequests,
        nextTeamRuns,
        nextGraph
      ] = await Promise.all([
        client.archiveEntries(),
        client.archiveEntries({ status: "draft" }),
        client.personas(),
        client.knowledgeRequests(),
        client.teamRuns(),
        client.archiveMap()
      ]);
      setEntries(nextEntries);
      setDrafts(nextDrafts);
      setPersonas(nextPersonas);
      setRequests(nextRequests);
      setTeamRuns(nextTeamRuns);
      setGraph(nextGraph || { nodes: [], edges: [] });
    } catch (nextError) {
      setError(nextError);
    } finally {
      if (showLoading) setLoading(false);
    }
  }, [client]);

  useEffect(() => {
    loadData(true);
  }, [loadData]);

  const activeRequestCount = requests.filter((item) => (
    ACTIVE_REQUEST_STATUSES.has(item.status)
  )).length;
  const visibleRequests = requestFilter === "active"
    ? requests.filter((item) => ACTIVE_REQUEST_STATUSES.has(item.status))
    : requests;
  const documentationTeams = useMemo(() => teamRuns.filter((run) => (
    run.lifecycle_mode === "continuous"
      && run.run_mode === "plan_and_execute"
      && run.execution_policy === "triggered"
      && run.status !== "canceled"
  )), [teamRuns]);
  const editingDraft = drafts.find((entry) => entry.id === editingId) || null;
  const visibleEntries = tab === "drafts" ? drafts : entries;

  function updateForm(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  function startNewEntry() {
    setEditingId(null);
    setForm(EMPTY_FORM);
    setRevisions([]);
    setError(null);
    setNotice("");
    setTab("library");
  }

  async function editEntry(entry) {
    setEditingId(entry.id);
    setForm(formFromEntry(entry));
    setRevisions([]);
    setError(null);
    setNotice("");
    setTab(entry.status === "draft" ? "drafts" : "library");
    try {
      setRevisions(await client.archiveEntryRevisions(entry.id));
    } catch (nextError) {
      setError(nextError);
    }
  }

  async function beginRequestDraft(item) {
    setEditingId(null);
    setForm(formFromRequest(item));
    setRevisions([]);
    setError(null);
    setNotice("This is an unpublished outline. Personas cannot use it until you publish.");
    setTab("library");
    if (item.status === "in_progress") return;
    try {
      const updated = await client.setKnowledgeRequestStatus(item.id, "in_progress");
      if (!updated) return;
      setRequests((current) => current.map((request) => (
        request.id === updated.id ? updated : request
      )));
      setGraph((current) => ({
        ...current,
        nodes: current.nodes.map((node) => (
          node.id === `request:${updated.id}` ? { ...node, meta: updated.status } : node
        ))
      }));
    } catch (nextError) {
      setError(nextError);
    }
  }

  async function searchLibrary(event) {
    event.preventDefault();
    setListLoading(true);
    setError(null);
    try {
      setEntries(await client.archiveEntries({ query, kind: kindFilter }));
    } catch (nextError) {
      setError(nextError);
    } finally {
      setListLoading(false);
    }
  }

  async function publishEntry(event) {
    event.preventDefault();
    if (form.scope === "personas" && !form.personaIds.length) {
      setError(new Error("Select at least one persona, or use Shared Library."));
      return;
    }
    setSaving(true);
    setError(null);
    const payload = {
      kind: form.kind,
      title: form.title.trim(),
      summary: form.summary.trim(),
      content_markdown: form.content.trim(),
      tags: splitValues(form.tags),
      source_urls: splitValues(form.sourceUrls),
      persona_ids: form.scope === "shared" ? [] : form.personaIds,
      change_summary: form.changeSummary.trim(),
      request_id: form.requestId
    };
    try {
      const published = editingId
        ? await client.reviseArchiveEntry(editingId, payload)
        : await client.publishArchiveEntry(payload);
      if (!published) throw new Error("The Library entry was not returned after publishing.");
      setEditingId(published.id);
      setForm(formFromEntry(published));
      setNotice(`Published revision ${published.current_revision}. Personas can now use this entry.`);
      setRevisions(await client.archiveEntryRevisions(published.id));
      await loadData();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setSaving(false);
    }
  }

  function togglePersona(personaId) {
    setForm((current) => {
      const selected = current.personaIds.includes(personaId);
      return {
        ...current,
        personaIds: selected
          ? current.personaIds.filter((id) => id !== personaId)
          : [...current.personaIds, personaId]
      };
    });
  }

  async function changeRequestStatus(item, status) {
    setRequestBusyId(item.id);
    setError(null);
    try {
      const updated = await client.setKnowledgeRequestStatus(item.id, status);
      if (!updated) return;
      setRequests((current) => current.map((request) => (
        request.id === updated.id ? updated : request
      )));
      await loadData();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setRequestBusyId(null);
    }
  }

  async function delegateRequest(item) {
    const teamRunId = selectedTeams[item.id]
      || item.assigned_team_run_id
      || documentationTeams[0]?.id;
    if (!teamRunId) {
      setError(new Error("Create a continuous TRIGGERED Team Run before delegating."));
      return;
    }
    setRequestBusyId(item.id);
    setError(null);
    try {
      const result = await client.delegateKnowledgeRequest(item.id, teamRunId);
      if (!result?.request) {
        throw new Error("The delegated Knowledge Request was not returned.");
      }
      setNotice(
        `"${item.title}" was sent to the documentation team. `
        + "Its output will appear in Drafts for your review."
      );
      await loadData();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setRequestBusyId(null);
    }
  }

  function openFulfilledEntry(item) {
    const fulfilled = entries.find((entry) => entry.id === item.fulfilled_by_entry_id);
    if (!fulfilled) {
      setError(new Error("The fulfilled Library entry is not in the published result."));
      return;
    }
    editEntry(fulfilled);
  }

  return (
    <section className="archive-view" aria-label="Archive">
      <header className="archive-head">
        <div>
          <div className="archive-eyebrow mono">PERSONA KNOWLEDGE SYSTEM</div>
          <h1 className="headline">Archive</h1>
          <p>
            User-published procedures, search methods, patterns, and references.
            Personas request missing knowledge; documentation teams prepare private drafts;
            only you can publish them.
          </p>
        </div>
        <div className="archive-head-counts mono" aria-label="Archive totals">
          <span>{entries.length}<small>PUBLISHED</small></span>
          <span>{drafts.length}<small>TO REVIEW</small></span>
          <span>{activeRequestCount}<small>OPEN GAPS</small></span>
        </div>
      </header>

      <div className="archive-tabs" role="tablist" aria-label="Archive sections">
        <button
          type="button"
          role="tab"
          aria-label="Library"
          aria-selected={tab === "library"}
          className={tab === "library" ? "active" : ""}
          onClick={() => setTab("library")}
        >
          LIBRARY <span>{entries.length}</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-label="Drafts"
          aria-selected={tab === "drafts"}
          className={tab === "drafts" ? "active" : ""}
          onClick={() => setTab("drafts")}
        >
          DRAFTS <span>{drafts.length}</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-label="Map"
          aria-selected={tab === "map"}
          className={tab === "map" ? "active" : ""}
          onClick={() => setTab("map")}
        >
          MAP <span>{graph.nodes?.length || 0}</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-label="Requests"
          aria-selected={tab === "requests"}
          className={tab === "requests" ? "active" : ""}
          onClick={() => setTab("requests")}
        >
          REQUESTS <span>{activeRequestCount}</span>
        </button>
      </div>

      {error ? <div className="archive-alert" role="alert">{errorDetail(error)}</div> : null}
      {notice ? <div className="archive-notice" role="status">{notice}</div> : null}

      {loading ? (
        <div className="archive-loading mono" role="status">LOADING ARCHIVE…</div>
      ) : null}

      {!loading && ["library", "drafts"].includes(tab) ? (
        <div className="archive-library" role="tabpanel">
          <aside className="archive-library-list">
            {tab === "library" ? (
              <form className="archive-search" onSubmit={searchLibrary}>
                <label>
                  <span className="archive-field-label mono">SEARCH PUBLISHED LIBRARY</span>
                  <input
                    className="input-field"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="Title, content, or tag"
                  />
                </label>
                <div className="archive-search-row">
                  <select
                    className="input-field"
                    aria-label="Filter by kind"
                    value={kindFilter}
                    onChange={(event) => setKindFilter(event.target.value)}
                  >
                    <option value="">All kinds</option>
                    {ENTRY_KINDS.map(([value, label]) => (
                      <option key={value} value={value}>{label}</option>
                    ))}
                  </select>
                  <Button type="submit" size="btn-sm" disabled={listLoading}>
                    {listLoading ? "Searching…" : "Search"}
                  </Button>
                </div>
              </form>
            ) : (
              <div className="archive-draft-note">
                <strong className="mono">PRIVATE REVIEW QUEUE</strong>
                <p>Team output stays invisible to personas until you review and publish it.</p>
              </div>
            )}
            <div className="archive-list-head">
              <span className="mono">
                {tab === "drafts" ? "TEAM DRAFTS" : "PUBLISHED ENTRIES"}
              </span>
              {tab === "library" ? (
                <Button size="btn-sm" onClick={startNewEntry}>New entry</Button>
              ) : null}
            </div>
            <div
              className="archive-entry-list"
              aria-label={tab === "drafts"
                ? "Private Library review drafts"
                : "Published Library entries"}
            >
              {visibleEntries.length ? visibleEntries.map((entry) => (
                <button
                  key={entry.id}
                  type="button"
                  className={`archive-entry-row${editingId === entry.id ? " active" : ""}`}
                  aria-pressed={editingId === entry.id}
                  aria-label={entry.status === "draft"
                    ? `Review ${entry.title} draft`
                    : undefined}
                  onClick={() => editEntry(entry)}
                >
                  <span className="archive-entry-kind mono">{kindLabel(entry.kind)}</span>
                  <strong>{entry.title}</strong>
                  <span>{entry.summary || "No summary"}</span>
                  <small className="mono">
                    {entry.status === "draft"
                      ? `TEAM DRAFT · ${originLabel(entry.origin_source_type)}`
                      : `REV ${entry.current_revision} · ${entry.persona_ids?.length
                        ? `${entry.persona_ids.length} PERSONA`
                        : "SHARED"}`}
                  </small>
                </button>
              )) : (
                <div className="archive-empty">
                  {tab === "drafts"
                    ? "No team drafts are waiting for review."
                    : "No published entries match this search. Start a new entry to create the shared memory."}
                </div>
              )}
            </div>
          </aside>

          {tab === "drafts" && !editingDraft ? (
            <aside className="archive-draft-empty">
              <div className="archive-editor-k mono">REVIEW REQUIRED</div>
              <h2>Select a team draft</h2>
              <p>
                Check claims, sources, scope, and wording before publishing.
                Personas cannot access anything in this queue.
              </p>
            </aside>
          ) : (
          <form className="archive-editor" onSubmit={publishEntry}>
            <div className="archive-editor-head">
              <div>
                <div className="archive-editor-k mono">
                  {editingDraft
                    ? "PRIVATE TEAM DRAFT"
                    : editingId
                      ? "PUBLISHED ENTRY"
                      : "UNPUBLISHED DRAFT"}
                </div>
                <h2>
                  {editingDraft
                    ? "Review team draft"
                    : editingId
                    ? "Edit Library entry"
                    : form.requestId
                      ? "Requested Library entry"
                      : "New Library entry"}
                </h2>
              </div>
              {editingDraft?.origin_source_type ? (
                <span className="archive-request-link mono">
                  FROM {originLabel(editingDraft.origin_source_type)}
                </span>
              ) : form.requestId ? (
                <span className="archive-request-link mono">FROM REQUEST</span>
              ) : null}
            </div>

            <div className="archive-form-grid">
              <label className="archive-field archive-field-title">
                <span className="archive-field-label mono">Title</span>
                <input
                  className="input-field"
                  value={form.title}
                  onChange={(event) => updateForm("title", event.target.value)}
                  required
                />
              </label>
              <label className="archive-field">
                <span className="archive-field-label mono">Kind</span>
                <select
                  className="input-field"
                  value={form.kind}
                  onChange={(event) => updateForm("kind", event.target.value)}
                >
                  {ENTRY_KINDS.map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
              </label>
            </div>

            <label className="archive-field">
              <span className="archive-field-label mono">Summary</span>
              <textarea
                className="input-field archive-summary-input"
                value={form.summary}
                onChange={(event) => updateForm("summary", event.target.value)}
                rows="2"
              />
            </label>

            <label className="archive-field">
              <span className="archive-field-label mono">Content</span>
              <textarea
                className="input-field archive-content-input"
                value={form.content}
                onChange={(event) => updateForm("content", event.target.value)}
                placeholder="Write verified, reusable guidance in Markdown."
                rows="14"
                required
              />
            </label>

            <div className="archive-form-grid">
              <label className="archive-field">
                <span className="archive-field-label mono">Tags</span>
                <input
                  className="input-field"
                  value={form.tags}
                  onChange={(event) => updateForm("tags", event.target.value)}
                  placeholder="release, api, verification"
                />
              </label>
              <label className="archive-field">
                <span className="archive-field-label mono">Source URLs or hints</span>
                <textarea
                  className="input-field"
                  value={form.sourceUrls}
                  onChange={(event) => updateForm("sourceUrls", event.target.value)}
                  placeholder="One source per line"
                  rows="3"
                />
              </label>
            </div>

            <fieldset className="archive-scope">
              <legend className="archive-field-label mono">Available to</legend>
              <div className="archive-scope-modes">
                <label>
                  <input
                    type="radio"
                    name="archive-scope"
                    checked={form.scope === "shared"}
                    onChange={() => updateForm("scope", "shared")}
                  />
                  <span><strong>Shared Library</strong><small>All personas can reference it</small></span>
                </label>
                <label>
                  <input
                    type="radio"
                    name="archive-scope"
                    checked={form.scope === "personas"}
                    onChange={() => updateForm("scope", "personas")}
                    disabled={!personas.length}
                  />
                  <span><strong>Selected personas</strong><small>Scoped reusable knowledge</small></span>
                </label>
              </div>
              {form.scope === "personas" ? (
                <div className="archive-persona-options">
                  {personas.map((persona) => (
                    <label key={persona.id}>
                      <input
                        type="checkbox"
                        checked={form.personaIds.includes(persona.id)}
                        onChange={() => togglePersona(persona.id)}
                      />
                      <span>{persona.name}<small>{persona.role || "No role"}</small></span>
                    </label>
                  ))}
                </div>
              ) : null}
            </fieldset>

            {editingId ? (
              <label className="archive-field">
                <span className="archive-field-label mono">Change summary</span>
                <input
                  className="input-field"
                  value={form.changeSummary}
                  onChange={(event) => updateForm("changeSummary", event.target.value)}
                  placeholder="What changed in this revision?"
                />
              </label>
            ) : null}

            {editingId ? (
              <details className="archive-revisions">
                <summary>Revision history ({revisions.length})</summary>
                {revisions.length ? (
                  <ol>
                    {revisions.map((revision) => (
                      <li key={revision.id}>
                        <strong>Revision {revision.revision}</strong>
                        <span>{revision.change_summary || "No change summary"}</span>
                        <time dateTime={revision.created_at}>{formatDate(revision.created_at)}</time>
                      </li>
                    ))}
                  </ol>
                ) : <p>No revision history returned.</p>}
              </details>
            ) : null}

            <div className="archive-editor-foot">
              <p>
                {editingDraft
                  ? "Publishing accepts this team draft as a canonical user-approved revision. Until then, it stays outside persona context."
                  : "Saving publishes a canonical revision immediately. Draft outlines are never injected into persona context."}
              </p>
              <Button type="submit" variant="primary" disabled={saving}>
                {saving
                  ? "Publishing…"
                  : editingDraft
                    ? "Review & publish"
                    : editingId
                    ? "Publish revision"
                    : "Publish to Library"}
              </Button>
            </div>
          </form>
          )}
        </div>
      ) : null}

      {!loading && tab === "map" ? (
        <div role="tabpanel">
          <ArchiveMap
            graph={graph}
            entries={entries}
            drafts={drafts}
            requests={requests}
            selectedNodeId={selectedNodeId}
            onSelect={setSelectedNodeId}
            onEditEntry={editEntry}
            onWriteRequest={beginRequestDraft}
          />
        </div>
      ) : null}

      {!loading && tab === "requests" ? (
        <div className="archive-requests" role="tabpanel">
          <div className="archive-requests-head">
            <div>
              <h2>Knowledge requests</h2>
              <p>
                Write it yourself or send it to a documentation team.
                Team output returns as a private review draft.
              </p>
            </div>
            <label>
              <span className="archive-field-label mono">SHOW</span>
              <select
                className="input-field"
                value={requestFilter}
                onChange={(event) => setRequestFilter(event.target.value)}
              >
                <option value="active">Active gaps</option>
                <option value="all">All requests</option>
              </select>
            </label>
          </div>

          <div className="archive-request-list">
            {visibleRequests.length ? visibleRequests.map((item) => {
              const active = ACTIVE_REQUEST_STATUSES.has(item.status);
              const busy = requestBusyId === item.id;
              const assignedTeam = documentationTeams.find(
                (run) => run.id === item.assigned_team_run_id
              );
              const selectedTeamId = selectedTeams[item.id]
                || item.assigned_team_run_id
                || documentationTeams[0]?.id
                || "";
              const delegated = (
                item.status === "in_progress" && Boolean(item.assigned_team_run_id)
              );
              return (
                <article className="archive-request" key={item.id}>
                  <header>
                    <div>
                      <span className={`archive-request-status archive-request-status-${item.status}`}>
                        {statusLabel(item.status)}
                      </span>
                      <h3>{item.title}</h3>
                    </div>
                    <div className="archive-request-by mono">
                      REQUESTED BY {item.requested_by_persona_name || "SYSTEM"}
                    </div>
                  </header>
                  <p>{item.reason}</p>
                  <div className="archive-request-context">
                    <div>
                      <strong className="mono">SUGGESTED OUTLINE</strong>
                      {item.suggested_outline?.length ? (
                        <ol>
                          {item.suggested_outline.map((line) => <li key={line}>{line}</li>)}
                        </ol>
                      ) : <span>No outline supplied.</span>}
                    </div>
                    <div>
                      <strong className="mono">SOURCES TO CHECK</strong>
                      {item.source_hints?.length ? (
                        <ul>
                          {item.source_hints.map((line) => <li key={line}>{line}</li>)}
                        </ul>
                      ) : <span>No source hints supplied.</span>}
                    </div>
                  </div>
                  <footer>
                    <span className="mono archive-request-origin">
                      {item.team_run_id
                        ? `REQUESTED IN TEAM RUN · ${item.team_run_id}`
                        : item.session_id
                          ? "REQUESTED IN CHAT SESSION"
                          : "REQUESTED BY PERSONA"}
                    </span>
                    <div className="archive-request-actions">
                      {active && !delegated ? (
                        <label className="archive-request-team">
                          <span className="archive-field-label mono">DOCUMENTATION TEAM</span>
                          <select
                            className="input-field"
                            aria-label={`Documentation team for ${item.title}`}
                            value={selectedTeamId}
                            disabled={busy || !documentationTeams.length}
                            onChange={(event) => setSelectedTeams((current) => ({
                              ...current,
                              [item.id]: event.target.value
                            }))}
                          >
                            {!documentationTeams.length ? (
                              <option value="">No triggered team available</option>
                            ) : documentationTeams.map((run) => (
                              <option key={run.id} value={run.id}>
                                {run.team_name || run.goal}
                              </option>
                            ))}
                          </select>
                        </label>
                      ) : null}
                      {active && !delegated ? (
                        <Button
                          size="btn-sm"
                          disabled={busy || !selectedTeamId}
                          aria-label={`Send ${item.title} to documentation team`}
                          onClick={() => delegateRequest(item)}
                        >
                          Send to team
                        </Button>
                      ) : null}
                      {delegated ? (
                        <span className="archive-request-delegated mono">
                          TEAM DRAFT IN PROGRESS · {assignedTeam?.team_name
                            || assignedTeam?.goal
                            || item.assigned_team_run_id}
                        </span>
                      ) : null}
                      {active ? (
                        <Button
                          size="btn-sm"
                          variant="primary"
                          disabled={busy}
                          aria-label={`Write ${item.title} in Library`}
                          onClick={() => beginRequestDraft(item)}
                        >
                          Write in Library
                        </Button>
                      ) : null}
                      {active ? (
                        <Button
                          size="btn-sm"
                          disabled={busy}
                          onClick={() => changeRequestStatus(item, "deferred")}
                        >
                          Later
                        </Button>
                      ) : null}
                      {active ? (
                        <Button
                          size="btn-sm"
                          disabled={busy}
                          onClick={() => changeRequestStatus(item, "dismissed")}
                        >
                          Dismiss
                        </Button>
                      ) : null}
                      {["deferred", "dismissed"].includes(item.status) ? (
                        <Button
                          size="btn-sm"
                          disabled={busy}
                          onClick={() => changeRequestStatus(item, "open")}
                        >
                          Reopen
                        </Button>
                      ) : null}
                      {item.status === "fulfilled" && item.fulfilled_by_entry_id ? (
                        <Button size="btn-sm" onClick={() => openFulfilledEntry(item)}>
                          Open Library entry
                        </Button>
                      ) : null}
                    </div>
                  </footer>
                </article>
              );
            }) : (
              <div className="archive-empty">
                No active knowledge requests. Personas already have the reusable context they asked for.
              </div>
            )}
          </div>
        </div>
      ) : null}
    </section>
  );
}
