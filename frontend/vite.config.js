import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.js"
  },
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8787",
        changeOrigin: false,
        configure(proxy) {
          proxy.on("proxyReq", (proxyRequest, request) => {
            if (request.headers.host) {
              proxyRequest.setHeader("host", request.headers.host);
            }
            if (request.headers["x-forwarded-proto"]) {
              proxyRequest.setHeader(
                "x-forwarded-proto",
                request.headers["x-forwarded-proto"]
              );
            }
          });
        }
      },
      "/static/vendor": "http://127.0.0.1:8787",
      "/static/avatars": "http://127.0.0.1:8787"
    }
  },
  build: {
    outDir: "../src/personal_agent_gateway/frontend_dist",
    emptyOutDir: true
  }
});
