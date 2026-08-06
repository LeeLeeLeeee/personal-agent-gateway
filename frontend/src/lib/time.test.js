import { describe, expect, it } from "vitest";
import { elapsedSeconds, fmtDateTime, nowDateTime } from "./time.js";

describe("fmtDateTime", () => {
  const reference = new Date(2026, 6, 14, 18, 0, 0);

  it.each([
    [new Date(2025, 11, 3, 4, 5, 6), "2025년 12월 03일 04시 05분 06초"],
    [new Date(2026, 5, 3, 4, 5, 6), "06월 03일 04시 05분 06초"],
    [new Date(2026, 6, 3, 4, 5, 6), "03일 04시 05분 06초"],
    [new Date(2026, 6, 14, 4, 5, 6), "04시 05분 06초"]
  ])("omits matching leading date parts", (date, expected) => {
    expect(fmtDateTime(date, reference)).toBe(expected);
  });

  it("returns an empty string for missing or invalid values", () => {
    expect(fmtDateTime(null, reference)).toBe("");
    expect(fmtDateTime("not-a-date", reference)).toBe("");
  });

  it("formats the current time with seconds", () => {
    expect(nowDateTime()).toMatch(/^\d{2}시 \d{2}분 \d{2}초$/);
  });
});

describe("elapsedSeconds", () => {
  it("returns whole seconds between the start and now", () => {
    const start = "2026-08-06T04:07:12.000Z";
    const now = Date.parse("2026-08-06T04:10:24.000Z");
    expect(elapsedSeconds(start, now)).toBe(192);
  });

  it("returns null for a missing or unparseable start", () => {
    expect(elapsedSeconds(null, Date.now())).toBeNull();
    expect(elapsedSeconds("not-a-date", Date.now())).toBeNull();
  });

  it("clamps a start in the future to zero", () => {
    const start = "2026-08-06T04:10:00.000Z";
    const now = Date.parse("2026-08-06T04:07:00.000Z");
    expect(elapsedSeconds(start, now)).toBe(0);
  });
});
