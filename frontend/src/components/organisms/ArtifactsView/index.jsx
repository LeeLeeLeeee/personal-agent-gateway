import { useEffect, useState } from "react";
import { api } from "../../../api/client.js";
import { useToast } from "../../providers/UiProvider/index.jsx";

const TYPE_FILTERS = [
  ["all", "All"],
  ["image", "Images"],
  ["video", "Videos"],
  ["audio", "Audio"],
  ["log", "Logs"],
  ["report", "Reports"],
  ["archive", "Archives"]
];

const GLYPH = { image: "▦", video: "▶", audio: "♪", log: "≣", report: "¶", archive: "◫" };

function fmtSize(bytes) {
  if (!bytes) return "0 KB";
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function fmtWhen(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function ArtifactPreview({ artifact }) {
  const contentUrl = api.artifactContentUrl(artifact.id);
  const [text, setText] = useState("");

  useEffect(() => {
    if (artifact.type !== "log" && artifact.type !== "report") return undefined;
    let alive = true;
    api.artifactText(artifact.id).then((value) => {
      if (alive) setText(value);
    });
    return () => {
      alive = false;
    };
  }, [artifact.id, artifact.type]);

  if (artifact.type === "image") {
    return (
      <img
        className="artifact-preview-img"
        src={contentUrl}
        alt={artifact.title}
        onError={(event) => {
          event.currentTarget.onerror = null;
          event.currentTarget.src = api.artifactThumbnailUrl(artifact.id);
        }}
      />
    );
  }
  if (artifact.type === "video") {
    return <video className="artifact-preview-media" controls src={contentUrl} />;
  }
  if (artifact.type === "audio") {
    return <audio className="artifact-preview-media" controls src={contentUrl} />;
  }
  if (artifact.type === "log" || artifact.type === "report") {
    return <pre className="mono artifact-preview-text">{text}</pre>;
  }
  return (
    <div className="artifact-preview-archive">
      <span className="artifact-preview-archive-glyph" aria-hidden="true">{GLYPH.archive}</span>
      <span className="mono">{fmtSize(artifact.size_bytes)}</span>
    </div>
  );
}

function ArtifactDrawer({ artifact, onClose }) {
  const toast = useToast();
  const contentUrl = api.artifactContentUrl(artifact.id);

  async function handleCopyPath() {
    await navigator.clipboard.writeText(artifact.relative_path);
    toast("경로가 복사되었습니다", "success");
  }

  return (
    <aside className="artifact-drawer" aria-label="Artifact viewer">
      <div className="artifact-drawer-head">
        <span className="mono">ARTIFACT · {artifact.type}</span>
        <button type="button" className="artifact-drawer-close" aria-label="Close" onClick={onClose}>✕</button>
      </div>
      <div className="artifact-drawer-body">
        <div className="artifact-drawer-preview">
          <ArtifactPreview artifact={artifact} />
        </div>
        <div className="artifact-drawer-title">{artifact.title}</div>
        <div className="settings-block artifact-drawer-provenance">
          <div className="settings-row">
            <span className="settings-k mono">PATH</span>
            <span className="settings-v mono">{artifact.relative_path}</span>
          </div>
          <div className="settings-row">
            <span className="settings-k mono">SIZE</span>
            <span className="settings-v mono">{fmtSize(artifact.size_bytes)} · {artifact.mime_type}</span>
          </div>
          <div className="settings-row">
            <span className="settings-k mono">JOB</span>
            <span className="settings-v mono">{artifact.source_job_id || "-"}</span>
          </div>
          <div className="settings-row">
            <span className="settings-k mono">SESSION</span>
            <span className="settings-v mono">{artifact.source_session_id || "-"}</span>
          </div>
        </div>
        <div className="artifact-drawer-actions">
          <a className="btn btn-primary btn-sm" href={contentUrl} download>Download</a>
          <button type="button" className="btn btn-sm" aria-label="Copy path" onClick={handleCopyPath}>Copy path</button>
        </div>
      </div>
    </aside>
  );
}

export function ArtifactsView({ artifacts = [] }) {
  const [type, setType] = useState("all");
  const [selectedId, setSelectedId] = useState(null);

  const grid = artifacts.filter((a) => type === "all" || a.type === type);
  const selected = artifacts.find((a) => a.id === selectedId) || null;

  return (
    <div className="artifacts-view">
      <div className="artifacts-main">
        <h1 className="headline">Artifacts</h1>
        <div className="artifacts-sub mono">{grid.length} shown · ./data/artifacts</div>

        <div className="artifacts-filters">
          {TYPE_FILTERS.map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={`chip${type === key ? " chip-active" : ""}`}
              aria-pressed={type === key}
              onClick={() => setType(key)}
            >
              {label}
            </button>
          ))}
        </div>

        {grid.length ? (
          <div className="artifact-grid">
            {grid.map((a) => (
              <button
                key={a.id}
                type="button"
                className="artifact-card"
                aria-label={`Open ${a.title}`}
                onClick={() => setSelectedId(a.id)}
              >
                <div className="artifact-card-thumb">
                  <span className="artifact-card-glyph" aria-hidden="true">{GLYPH[a.type] || "◫"}</span>
                  <span className="artifact-card-type mono">{a.type}</span>
                </div>
                <div className="artifact-card-body">
                  <div className="artifact-card-title">{a.title}</div>
                  <div className="mono artifact-card-meta">{fmtSize(a.size_bytes)} · {fmtWhen(a.created_at)}</div>
                </div>
              </button>
            ))}
          </div>
        ) : (
          <div className="planned">NO ARTIFACTS</div>
        )}
      </div>

      {selected ? <ArtifactDrawer artifact={selected} onClose={() => setSelectedId(null)} /> : null}
    </div>
  );
}
