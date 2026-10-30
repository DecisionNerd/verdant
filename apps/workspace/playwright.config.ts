import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e", workers: 1, fullyParallel: false,
  timeout: 300_000, expect: { timeout: 20_000 },
  reporter: "line", use: { baseURL: "http://127.0.0.1:19085", actionTimeout: 15_000, navigationTimeout: 30_000, trace: "off", screenshot: "only-on-failure" },
});
