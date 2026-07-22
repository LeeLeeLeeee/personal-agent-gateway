// @vitest-environment node

import { describe, expect, it, vi } from "vitest";
import { createViteConfig } from "./vite.config.js";

describe("Vite API proxy", () => {
  it("preserves the public host and forwarded protocol", () => {
    const config = createViteConfig("tunnel.example.com");
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
          host: "tunnel.example.com",
          "x-forwarded-proto": "https"
        }
      }
    );

    expect(setHeader).toHaveBeenCalledWith(
      "host",
      "tunnel.example.com"
    );
    expect(setHeader).toHaveBeenCalledWith("x-forwarded-proto", "https");
  });

  it("does not publish an allowed host when the local setting is blank", () => {
    expect(createViteConfig().server.allowedHosts).toEqual([]);
  });
});
