# Agentium frontend (Web + Electron)

## Dev (browser)

Control plane must listen where Vite proxies `/v1` (default `http://127.0.0.1:8765`). Override:

`set AGENTIUM_CONTROL_PLANE_URL=http://host:port` before `npm run dev` (see `vite.config.ts`).

```bash
npm ci
npm run dev
```

Open Settings if profile fails; use header identity or bearer to match the server `AGENTIUM_IDENTITY_MODE`.

## Dev (Electron)

```bash
npm run build:electron
npm run dev:desktop
```

Uses `VITE_DEV_SERVER_URL` to load the Vite app. Production packaging (signed installers) is out of scope for this MVP.

### `Error: spawn electron ENOENT`

Usually **`node_modules/electron/` is missing or incomplete** (only a stale `.bin/electron` symlink left), or the Chromium zip never finished downloading—same recovery as below: **`npm run reinstall-electron`** (or **`reinstall-electron:cn`**). Confirm **`frontend/node_modules/electron/path.txt`** exists afterward.

The **`dev:desktop`** script invokes Electron via **`node ./node_modules/electron/cli.js`** so it does not depend on your shell finding `electron` on `PATH`.

### Electron binary missing (`failed to install correctly`)

If `electron .` throws *Electron failed to install correctly*, the packaged Chromium binary never downloaded (network interrupt, proxy, or cache). From `frontend/`:

```bash
npm run reinstall-electron
```

Then retry `npm run dev:desktop`.

If npm prints **`ReadError`**, **`socket hang up`**, or **`The server aborted pending request`**, the default Electron CDN is flaky from your network — use the mirror shortcut:

```bash
npm run reinstall-electron:cn
```

Or manually:

```bash
export ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
npm run reinstall-electron
```

Optional: bump retries with `ELECTRON_REINSTALL_ATTEMPTS=6`.

Persistent mirror (example): add `electron_mirror=https://npmmirror.com/mirrors/electron/` under `frontend/.npmrc` (keep repo-private / undocumented secrets out).

On Windows (cmd): `set ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/` then run the same reinstall command.

The cascading `vite` *write EPIPE* lines come from `concurrently -k` shutting down Vite after Electron exits; fixing Electron resolves both.

## Build

```bash
npm run build:web
```

Static assets in `dist/`; serve with same origin as API or configure `VITE_API_BASE_URL` / Settings.
