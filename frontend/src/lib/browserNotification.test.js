import { afterEach, describe, expect, it, vi } from "vitest";
import {
  disableBrowserNotifications,
  enableBrowserNotifications,
  getBrowserNotificationState,
  showTeamRunTerminalNotification
} from "./browserNotification.js";

const STORAGE_KEY = "pag.browser-notifications.v1";

function installNotification(permission = "default", requestedPermission = permission) {
  const instances = [];
  class FakeNotification {
    static permission = permission;

    static requestPermission = vi.fn(async () => {
      FakeNotification.permission = requestedPermission;
      return requestedPermission;
    });

    constructor(title, options) {
      this.title = title;
      this.options = options;
      this.close = vi.fn();
      instances.push(this);
    }
  }
  vi.stubGlobal("Notification", FakeNotification);
  return { FakeNotification, instances };
}

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("browserNotification", () => {
  it("reports unsupported without requesting permission", () => {
    vi.stubGlobal("Notification", undefined);

    expect(getBrowserNotificationState()).toEqual({
      supported: false,
      permission: "unsupported",
      enabled: false
    });
  });

  it("requests permission only when enabled and persists a granted opt-in", async () => {
    const { FakeNotification } = installNotification("default", "granted");

    expect(getBrowserNotificationState()).toMatchObject({ permission: "default", enabled: false });
    expect(FakeNotification.requestPermission).not.toHaveBeenCalled();

    await expect(enableBrowserNotifications()).resolves.toMatchObject({ permission: "granted", enabled: true });
    expect(FakeNotification.requestPermission).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(STORAGE_KEY)).toBe("enabled");

    expect(disableBrowserNotifications()).toMatchObject({ permission: "granted", enabled: false });
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("does not send when blocked or not opted in", () => {
    const denied = installNotification("denied");
    expect(showTeamRunTerminalNotification({ type: "team.run.failed", team_run_id: "run-1" }, vi.fn())).toBeNull();
    expect(denied.instances).toHaveLength(0);
  });

  it("uses a generic terminal payload and opens the opaque run target on click", () => {
    const { instances } = installNotification("granted");
    localStorage.setItem(STORAGE_KEY, "enabled");
    const onOpen = vi.fn();
    const focus = vi.spyOn(window, "focus").mockImplementation(() => {});

    const notification = showTeamRunTerminalNotification({
      type: "team.run.failed",
      team_run_id: "run-private",
      run: {
        finished_at: "2026-07-16T00:00:00Z",
        summary: "secret summary",
        error_message: "C:/secret/path"
      },
      prompt: "private prompt"
    }, onOpen);

    expect(instances).toHaveLength(1);
    expect(notification.title).toBe("Team Run failed");
    expect(notification.options.body).toBe("Open Agent Gateway to review the run.");
    expect(JSON.stringify([notification.title, notification.options.body])).not.toContain("secret");
    expect(JSON.stringify([notification.title, notification.options.body])).not.toContain("run-private");

    notification.onclick();
    expect(focus).toHaveBeenCalledTimes(1);
    expect(onOpen).toHaveBeenCalledWith("run-private");
    expect(notification.close).toHaveBeenCalledTimes(1);
  });
});
