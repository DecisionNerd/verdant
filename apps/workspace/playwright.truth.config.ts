import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e", testMatch: "truth.spec.ts", workers: 1, fullyParallel: false,
  timeout: 900_000, expect: { timeout: 20_000 },
  reporter: "line", use: { baseURL: "https://workspace.localhost:19243", actionTimeout: 15_000, navigationTimeout: 30_000, trace: "off", screenshot: "only-on-failure" },
});
