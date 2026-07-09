const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

function parseTime(time) {
  const [hh, mm] = String(time || "09:00").split(":").map(Number);
  return { hh: Number.isNaN(hh) ? 9 : hh, mm: Number.isNaN(mm) ? 0 : mm };
}

export function buildCron({ mode, time, weekday, everyMinutes } = {}) {
  if (mode === "interval") return `*/${everyMinutes} * * * *`;
  const { hh, mm } = parseTime(time);
  if (mode === "weekly") return `${mm} ${hh} * * ${weekday}`;
  return `${mm} ${hh} * * *`;
}

export function describeCron({ mode, time, weekday, everyMinutes } = {}) {
  if (mode === "interval") return `Runs every ${everyMinutes} minutes`;
  const { hh, mm } = parseTime(time);
  const hhmm = `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
  if (mode === "weekly") return `Runs weekly on ${WEEKDAYS[weekday] || WEEKDAYS[0]} at ${hhmm}`;
  return `Runs daily at ${hhmm}`;
}
