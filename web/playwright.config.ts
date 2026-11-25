import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests/browser",
  fullyParallel: false,
  use: {
    baseURL: "http://127.0.0.1:5173",
    headless: true,
    viewport: { width: 1600, height: 1000 },
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH }
      : {},
  },
  webServer: {
    env: { WORKBENCH_MOCK_API: "1" },
    command:
      process.env.WORKBENCH_TEST_PREVIEW === "1"
        ? "npm run preview"
        : "npm run dev",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: !process.env.CI,
  },
});
