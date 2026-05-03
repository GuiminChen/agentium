import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const controlPlaneTarget = process.env.AGENTIUM_CONTROL_PLANE_URL ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/v1": {
        target: controlPlaneTarget,
        changeOrigin: true,
      },
      "/healthz": { target: controlPlaneTarget, changeOrigin: true },
      "/readyz": { target: controlPlaneTarget, changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
