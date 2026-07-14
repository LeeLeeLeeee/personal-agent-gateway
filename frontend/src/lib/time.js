export function fmtDateTime(iso, reference = new Date()) {
  if (!iso) return "";
  const date = iso instanceof Date ? iso : new Date(iso);
  const now = reference instanceof Date ? reference : new Date(reference);
  if (Number.isNaN(date.getTime()) || Number.isNaN(now.getTime())) return "";

  const pad = (value) => String(value).padStart(2, "0");
  const time = `${pad(date.getHours())}시 ${pad(date.getMinutes())}분 ${pad(date.getSeconds())}초`;
  const sameYear = date.getFullYear() === now.getFullYear();
  const sameMonth = sameYear && date.getMonth() === now.getMonth();
  const sameDay = sameMonth && date.getDate() === now.getDate();

  if (sameDay) return time;
  if (sameMonth) return `${pad(date.getDate())}일 ${time}`;
  if (sameYear) return `${pad(date.getMonth() + 1)}월 ${pad(date.getDate())}일 ${time}`;
  return `${date.getFullYear()}년 ${pad(date.getMonth() + 1)}월 ${pad(date.getDate())}일 ${time}`;
}

export function nowDateTime() {
  const now = new Date();
  return fmtDateTime(now, now);
}

export function fmtElapsed(seconds) {
  const safe = Math.floor(Math.max(0, seconds));
  return `${String(Math.floor(safe / 60)).padStart(2, "0")}:${String(safe % 60).padStart(2, "0")}`;
}
