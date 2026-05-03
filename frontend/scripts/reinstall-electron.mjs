/**
 * Remove incomplete Electron install and re-run npm install so download/postinstall runs again.
 * Source: electron/index.js requires node_modules/electron/path.txt after install.js completes.
 *
 * Retries a few times on flaky TLS / CDN resets. Set ELECTRON_MIRROR for restrictive networks
 * (see npm script reinstall-electron:cn).
 */
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const electronDir = path.join(root, "node_modules", "electron");
const npmCmd = process.platform === "win32" ? "npm.cmd" : "npm";
const maxAttempts = Number(process.env.ELECTRON_REINSTALL_ATTEMPTS ?? "4");

function rmElectronDir() {
  try {
    fs.rmSync(electronDir, { recursive: true, force: true });
  } catch {
    /* ignore missing dir */
  }
}

function printNetworkHints() {
  const mirror = process.env.ELECTRON_MIRROR ?? "";
  console.error(`
Electron binary download failed after ${maxAttempts} attempt(s).
Common causes: CDN reset, firewall, or unstable routes to GitHub releases.

${mirror ? `Current ELECTRON_MIRROR=${mirror}` : "No ELECTRON_MIRROR is set (default CDN)." }

Try a mirror (China-friendly npmmirror example):

  npm run reinstall-electron:cn

Or one-shot:

  export ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
  npm run reinstall-electron

Optional persistent mirror in frontend/.npmrc (do not commit secrets):

  electron_mirror=https://npmmirror.com/mirrors/electron/
`);
}

async function main() {
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    rmElectronDir();
    if (attempt > 1) {
      console.error(`Retrying electron install (${attempt}/${maxAttempts})…`);
    }
    const result = spawnSync(npmCmd, ["install", "electron"], {
      cwd: root,
      stdio: "inherit",
      shell: process.platform === "win32",
      env: process.env,
    });
    const code = result.status ?? 1;
    if (code === 0) {
      process.exit(0);
    }
    if (attempt < maxAttempts) {
      await delay(2000 * attempt);
    }
  }
  printNetworkHints();
  process.exit(1);
}

await main();
