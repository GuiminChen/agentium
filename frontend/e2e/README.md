# Playwright E2E

## Prerequisites

- Node 18+ and dev dependencies (`npm install` in `frontend/`).
- Python environment with Agentium installed from repo root (`pip install -e .` or `PYTHONPATH=src`).
- **Windows**: set the Python used for the backend fixture, for example:

  `set AGENTIUM_PYTHON_EXE=C:\Users\you\anaconda3\envs\tradeagent\python.exe`

## What runs

`playwright.config.ts` starts, unless `SKIP_E2E_STACK=1`:

1. `scripts/e2e_control_plane_server.py` on `http://127.0.0.1:8765` (matches Vite proxy `AGENTIUM_CONTROL_PLANE_URL`).
2. Vite dev server on `http://127.0.0.1:5173`.

`PLAYWRIGHT_BASE_URL` overrides the UI base URL (default `http://127.0.0.1:5173`).

`AGENTIUM_CONTROL_PLANE_URL` overrides the URL used by tests for direct HTTP calls (default `http://127.0.0.1:8765`).

## CI

- Set `AGENTIUM_PYTHON_EXE` to the job\'s Python.
- Omit `SKIP_E2E_STACK` to run the full stack; set `SKIP_E2E_STACK=1` only if you provide both servers externally.

Run: `npm run test:e2e` from `frontend/`.

## Windows / 企业环境

若 **Vite / Rollup 本地原生模块**（`rollup.win32-x64-msvc.node`）被 Application Control 拦截，`npm run dev` / `npm run build:web` / Playwright 的 Vite webServer 会失败。可选：`SKIP_E2E_STACK=1` 并自行在外部启动未受拦截的构建环境；或使用已在 CI 中验证的 Node 版本与策略例外。`tsc --noEmit` 仍可单独用于类型检查。
