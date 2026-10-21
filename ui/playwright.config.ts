import { defineConfig, devices } from "@playwright/test";

// Runs the built panel against the scripted fake hub (`?fake=<scenario>`), so no Python,
// Live or API key is needed. Screenshots land in e2e/screenshots/ and CI uploads them.
const PORT = 4173;

export default defineConfig({
  testDir: "e2e",
  outputDir: "e2e/results",
  fullyParallel: true,
  reporter: process.env.CI ? [["list"], ["html", { open: "never", outputFolder: "e2e/report" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    ...devices["Desktop Chrome"],
    viewport: { width: 400, height: 860 },
    launchOptions: process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {},
  },
  webServer: {
    command: `npm run build && npx vite preview --port ${PORT} --strictPort --host 127.0.0.1`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
