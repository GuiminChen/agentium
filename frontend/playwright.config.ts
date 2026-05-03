import { defineConfig } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");
const pythonExe = process.env.AGENTIUM_PYTHON_EXE ?? "python";

const scriptPath = path.join(repoRoot, "scripts", "e2e_control_plane_server.py");
const backend = {
  command: `"${pythonExe}" "${scriptPath}" --port 8765`,
  cwd: repoRoot,
  url: "http://127.0.0.1:8765/v1/version",
  reuseExistingServer: !process.env.CI,
  timeout: 120_000,
};

const vite = {
  command: "npm run dev",
  cwd: __dirname,
  url: "http://127.0.0.1:5173",
  reuseExistingServer: !process.env.CI,
  timeout: 120_000,
};

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:5173",
    trace: "on-first-retry",
  },
  webServer: process.env.SKIP_E2E_STACK === "1" ? undefined : [backend, vite],
});
