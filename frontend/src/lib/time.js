export function fmtTime(iso, withSeconds = false) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (value) => String(value).padStart(2, "0");
  return withSeconds
    ? `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
    : `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function nowHMS() {
  return fmtTime(new Date().toISOString(), true);
}

export function nowHM() {
  return fmtTime(new Date().toISOString(), false);
}

export function fmtElapsed(seconds) {
  const safe = Math.floor(Math.max(0, seconds));
  return `${String(Math.floor(safe / 60)).padStart(2, "0")}:${String(safe % 60).padStart(2, "0")}`;
}
