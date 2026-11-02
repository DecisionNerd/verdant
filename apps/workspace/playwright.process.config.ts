import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e", testMatch: "process.spec.ts", workers: 1, fullyParallel: false,
  timeout: 420_000, expect: { timeout: 30_000 }, reporter: "line",
  use: { baseURL: "http://127.0.0.1:19185", actionTimeout: 20_000, navigationTimeout: 45_000, trace: "off", screenshot: "only-on-failure" },
});
