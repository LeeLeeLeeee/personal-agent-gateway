import { useState } from "react";
import { api } from "../../../api/client.js";
import { ArtifactModal } from "../ArtifactModal/index.jsx";

const TYPE_FILTERS = [
  ["all", "All"],
  ["image", "Images"],
  ["video", "Videos"],
  ["audio", "Audio"],
  ["document", "Documents"],
  ["log", "Logs"],
  ["report", "Reports"],
  ["archive", "Archives"]
];

const GLYPH = { image: "▦", video: "▶", audio: "♪", document: "▤", log: "≣", report: "¶", archive: "◫" };

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

export function ArtifactsView({ artifacts = [], onChange }) {
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
                  {a.type === "image" ? (
                    <img
                      className="artifact-card-img"
                      src={api.artifactContentUrl(a.id)}
                      alt={a.title}
                      onError={(e) => { e.currentTarget.style.display = "none"; }}
                    />
                  ) : (
                    <span className="artifact-card-glyph" aria-hidden="true">{GLYPH[a.type] || "◫"}</span>
                  )}
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

      {selected ? (
        <ArtifactModal
          artifact={selected}
          onClose={() => setSelectedId(null)}
          onDeleted={() => { setSelectedId(null); onChange?.(); }}
        />
      ) : null}
    </div>
  );
}
