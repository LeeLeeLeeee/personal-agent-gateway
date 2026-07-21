// @vitest-environment node

import { describe, expect, it, vi } from "vitest";
import config from "./vite.config.js";

describe("Vite API proxy", () => {
  it("preserves the public host and forwarded protocol", () => {
    let proxyHandler;
    config.server.proxy["/api"].configure({
      on: vi.fn((event, handler) => {
        if (event === "proxyReq") proxyHandler = handler;
      })
    });
    const setHeader = vi.fn();

    proxyHandler(
      { setHeader },
      {
        headers: {
          host: "mpx-local.younghyun-lee.com",
          "x-forwarded-proto": "https"
        }
      }
    );

    expect(setHeader).toHaveBeenCalledWith(
      "host",
      "mpx-local.younghyun-lee.com"
    );
    expect(setHeader).toHaveBeenCalledWith("x-forwarded-proto", "https");
  });
});
