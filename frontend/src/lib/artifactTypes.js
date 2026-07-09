export const REGISTRABLE_EXTENSIONS = [
  "png", "jpg", "jpeg", "webp", "gif", "svg", "bmp",
  "mp4", "mov", "webm", "mkv", "avi",
  "mp3", "m4a", "wav", "ogg", "flac",
  "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "md", "csv", "hwp", "hwpx", "html", "htm"
];

const EXT_ALT = REGISTRABLE_EXTENSIONS.join("|");

// A path-like token: word chars, dots, slashes, backslashes, colon, hyphen, ending in a whitelisted extension.
// Paths containing spaces are intentionally not matched.
export function makePathRe() {
  return new RegExp(`[\\w./\\\\:-]*\\.(?:${EXT_ALT})\\b`, "gi");
}

export function isRegistrablePath(text) {
  const trimmed = String(text || "").trim();
  if (trimmed.includes("://")) return false; // URLs are not local workspace paths
  return new RegExp(`^[\\w./\\\\:-]+\\.(?:${EXT_ALT})$`, "i").test(trimmed);
}
