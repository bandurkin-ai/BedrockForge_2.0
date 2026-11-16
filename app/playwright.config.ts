import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  timeout: 180_000,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  outputDir: "../artifacts/e2e-results",
});
