import { describe, expect, it } from "vitest";
import { buildCron } from "./cron.js";

describe("buildCron", () => {
  it("daily", () => expect(buildCron({ mode: "daily", time: "09:00" })).toBe("0 9 * * *"));
  it("weekly", () => expect(buildCron({ mode: "weekly", time: "18:00", weekday: 5 })).toBe("0 18 * * 5"));
  it("interval", () => expect(buildCron({ mode: "interval", everyMinutes: 30 })).toBe("*/30 * * * *"));
});
