import { describe, expect, it } from "vitest";
import {
  rememberSseEvent,
  SSE_DEDUP_LIMIT
} from "./useSessionController.js";

describe("rememberSseEvent", () => {
  it("deduplicates within one stream and accepts reused ids from a new stream", () => {
    const seen = new Map();

    expect(rememberSseEvent(seen, { stream_id: "boot-a", id: 1 })).toBe(true);
    expect(rememberSseEvent(seen, { stream_id: "boot-a", id: 1 })).toBe(false);
    expect(rememberSseEvent(seen, { stream_id: "boot-b", id: 1 })).toBe(true);
  });

  it("bounds retained composite event ids", () => {
    const seen = new Map();

    for (let id = 1; id <= SSE_DEDUP_LIMIT + 1; id += 1) {
      expect(rememberSseEvent(seen, { stream_id: "boot-a", id })).toBe(true);
    }

    expect(seen.size).toBe(SSE_DEDUP_LIMIT);
    expect(seen.has("boot-a:1")).toBe(false);
  });
});
