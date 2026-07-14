import { describe, expect, it } from "vitest";
import { fmtDateTime, nowDateTime } from "./time.js";

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
