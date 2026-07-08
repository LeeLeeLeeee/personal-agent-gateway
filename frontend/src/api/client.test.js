import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./client.js";

function jsonResponse(body, ok = true) {
  return Promise.resolve({ ok, json: () => Promise.resolve(body) });
}

describe("api client", () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });

  it("keeps existing auth and chat endpoints", async () => {
    fetch
      .mockResolvedValueOnce({ ok: true })
      .mockResolvedValueOnce(jsonResponse({ messages: [], pending_approval: null }));

    expect(await api.login("123456")).toBe(true);
    await api.sendChat("hello");

    expect(fetch).toHaveBeenNthCalledWith(1, "/api/auth/login", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ otp: "123456" })
    }));
    expect(fetch).toHaveBeenNthCalledWith(2, "/api/chat", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ message: "hello" })
    }));
  });

  it("normalizes list responses used by the UI", async () => {
    fetch
      .mockResolvedValueOnce(jsonResponse({ sessions: [{ id: "s1" }] }))
      .mockResolvedValueOnce(jsonResponse({ events: [{ kind: "user" }] }))
      .mockResolvedValueOnce(jsonResponse({ artifacts: [{ id: "a1" }] }));

    await expect(api.sessions()).resolves.toEqual([{ id: "s1" }]);
    await expect(api.history()).resolves.toEqual([{ kind: "user" }]);
    await expect(api.artifacts()).resolves.toEqual([{ id: "a1" }]);
  });
});
