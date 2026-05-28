import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["test/**/*.test.ts"],
    environment: "node",
    // 15s accommodates the transport.test.ts terminate() case which spawns
    // a real subprocess and waits for SIGTERM exit on CI runners — the 5s
    // vitest default is tight on busy GitHub Actions hosts (see
    // publish-wrapper.yml run 26551192854 for the timeout symptom).
    testTimeout: 15000,
  },
});
