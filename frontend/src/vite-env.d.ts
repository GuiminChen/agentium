/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string;
  readonly VITE_IDENTITY_MODE: string;
  readonly VITE_GIT_SHA?: string;
}

declare global {
  interface Window {
    agentium?: {
      getRuntimeTarget: () => "web" | "desktop";
      exportDiagnostics?: () => Promise<{ json: string; filename: string }>;
    };
  }
}

export {};
