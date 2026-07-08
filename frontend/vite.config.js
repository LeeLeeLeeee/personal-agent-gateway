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
      "/api": "http://127.0.0.1:8787",
      "/static/vendor": "http://127.0.0.1:8787"
    }
  },
  build: {
    outDir: "../src/personal_agent_gateway/frontend_dist",
    emptyOutDir: true
  }
});
