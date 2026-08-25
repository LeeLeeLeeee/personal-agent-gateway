import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

const repositoryRoot = fileURLToPath(new URL("../", import.meta.url));

export function createViteConfig(allowedHost = "") {
  const normalizedAllowedHost = allowedHost.trim();
  return {
    plugins: [react()],
    test: {
      environment: "jsdom",
      setupFiles: "./src/test/setup.js",
      // vitest 기본값은 5초다. ArchiveView 의 미리보기 테스트 두 개가 전체
      // 스위트를 함께 돌릴 때 그 선을 5.1초쯤에서 넘겨 실패했다 -- 단독으로
      // 돌리면 통과하므로 코드가 느린 것이 아니라 42개 파일이 동시에 도는
      // 부하에 눌린 것이다. 늘리는 대신 각 테스트를 쪼개는 방법도 있지만,
      // 그것은 부하에 따라 다시 넘어갈 선을 옮길 뿐이다.
      //
      // 15초는 진짜로 멈춘 테스트를 여전히 잡을 만큼 짧다. 이 값을 더
      // 올려야 할 상황이 오면 그때는 테스트가 아니라 그 테스트가 재는
      // 코드를 봐야 한다.
      testTimeout: 15000
    },
    server: {
      allowedHosts: normalizedAllowedHost ? [normalizedAllowedHost] : [],
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
  };
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, repositoryRoot, "PAG_");
  return createViteConfig(env.PAG_DEV_ALLOWED_HOST);
});
