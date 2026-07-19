const STORAGE_KEY = "pag.browser-notifications.v1";
const TEAM_RUN_NOTIFICATION_TYPES = new Set([
  "team.run.completed",
  "team.run.failed",
  "team.run.input_requested"
]);

function readPreference() {
  try {
    return globalThis.localStorage?.getItem(STORAGE_KEY) === "enabled";
  } catch (_error) {
    return false;
  }
}

function writePreference(enabled) {
  try {
    if (enabled) globalThis.localStorage?.setItem(STORAGE_KEY, "enabled");
    else globalThis.localStorage?.removeItem(STORAGE_KEY);
  } catch (_error) {
    // A blocked storage API behaves like an off preference.
  }
}

export function getBrowserNotificationState() {
  const BrowserNotification = globalThis.Notification;
  if (typeof BrowserNotification === "undefined") {
    return { supported: false, permission: "unsupported", enabled: false };
  }
  const permission = BrowserNotification.permission || "default";
  return {
    supported: true,
    permission,
    enabled: permission === "granted" && readPreference()
  };
}

export async function enableBrowserNotifications() {
  const BrowserNotification = globalThis.Notification;
  if (typeof BrowserNotification === "undefined") return getBrowserNotificationState();

  try {
    const permission = BrowserNotification.permission === "default"
      ? await BrowserNotification.requestPermission()
      : BrowserNotification.permission;
    writePreference(permission === "granted");
  } catch (_error) {
    writePreference(false);
  }
  return getBrowserNotificationState();
}

export function disableBrowserNotifications() {
  writePreference(false);
  return getBrowserNotificationState();
}

export function showTeamRunNotification(event, onOpen) {
  if (!TEAM_RUN_NOTIFICATION_TYPES.has(event?.type) || !event.team_run_id) return null;
  if (!getBrowserNotificationState().enabled) return null;

  const failed = event.type === "team.run.failed";
  const needsInput = event.type === "team.run.input_requested";
  try {
    const notification = new globalThis.Notification(
      needsInput ? "Team Run needs input" : failed ? "Team Run failed" : "Team Run completed",
      {
        body: needsInput
          ? "Open Agent Gateway to answer pending questions."
          : "Open Agent Gateway to review the run.",
        tag: `team-run:${event.team_run_id}:${event.type}:${
          event.decision_request_id || event.run?.finished_at || "event"
        }`,
        data: { teamRunId: event.team_run_id }
      }
    );
    notification.onclick = () => {
      globalThis.window?.focus?.();
      onOpen?.(event.team_run_id);
      notification.close?.();
    };
    return notification;
  } catch (_error) {
    return null;
  }
}
