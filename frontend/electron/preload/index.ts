import { contextBridge } from "electron";

contextBridge.exposeInMainWorld("agentium", {
  getRuntimeTarget: (): "desktop" => "desktop",
  exportDiagnostics: async (): Promise<{ json: string; filename: string }> => {
    const payload = {
      target: "desktop",
      exportedAt: new Date().toISOString(),
      gitSha: typeof process !== "undefined" ? process.env.AGENTIUM_GIT_SHA || "unknown" : "unknown",
      redacted: true,
      note: "Attach backend /v1/version and /v1/me from a configured session for full ops bundle.",
    };
    return {
      json: JSON.stringify(payload, null, 2),
      filename: "agentium-desktop-diagnostics.json",
    };
  },
});
