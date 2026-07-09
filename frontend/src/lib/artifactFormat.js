export const GLYPH = { image: "▦", video: "▶", audio: "♪", document: "▤", log: "≣", report: "¶", archive: "◫" };

export function fmtSize(bytes) {
  if (!bytes) return "0 KB";
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}
