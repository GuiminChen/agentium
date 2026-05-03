import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");

/** Resolve esbuild when nested under frontend/ or hoisted to repo root node_modules. */
function loadEsbuild() {
  const tryRoots = [root, path.resolve(root, "..")];
  for (const base of tryRoots) {
    const marker = path.join(base, "package.json");
    if (!fs.existsSync(marker)) {
      continue;
    }
    try {
      const req = createRequire(marker);
      return req("esbuild");
    } catch {
      /* try next base */
    }
  }
  console.error(
    [
      "Cannot find package \"esbuild\".",
      "Install devDependencies from the frontend folder:",
      `  cd ${root}`,
      "  npm install",
      "If you used production-only install, run: npm install --include=dev",
    ].join("\n"),
  );
  process.exit(1);
}

const esbuild = loadEsbuild();

await esbuild.build({
  entryPoints: [path.join(root, "electron/main.ts")],
  bundle: true,
  platform: "node",
  outfile: path.join(root, "dist-electron/main.cjs"),
  external: ["electron"],
  format: "cjs",
  target: "node22",
});

await esbuild.build({
  entryPoints: [path.join(root, "electron/preload/index.ts")],
  bundle: true,
  platform: "node",
  outfile: path.join(root, "dist-electron/preload.cjs"),
  external: ["electron"],
  format: "cjs",
  target: "node22",
});

console.log("electron bundle: dist-electron/main.cjs, dist-electron/preload.cjs");
