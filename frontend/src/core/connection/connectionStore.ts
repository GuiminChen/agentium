import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

export type IdentityMode = "header" | "bearer";

export interface ConnectionState {
  apiBaseUrl: string;
  identityMode: IdentityMode;
  tenantId: string;
  userId: string;
  role: string;
  bearerToken: string;
  setApiBaseUrl: (v: string) => void;
  setIdentityMode: (v: IdentityMode) => void;
  setTenantId: (v: string) => void;
  setUserId: (v: string) => void;
  setRole: (v: string) => void;
  setBearerToken: (v: string) => void;
}

export const useConnectionStore = create<ConnectionState>()(
  persist(
    (set) => ({
      apiBaseUrl: "",
      identityMode: "header",
      tenantId: "tenant-a",
      userId: "user-1",
      role: "admin",
      bearerToken: "",
      setApiBaseUrl: (apiBaseUrl) => set({ apiBaseUrl }),
      setIdentityMode: (identityMode) => set({ identityMode }),
      setTenantId: (tenantId) => set({ tenantId }),
      setUserId: (userId) => set({ userId }),
      setRole: (role) => set({ role }),
      setBearerToken: (bearerToken) => set({ bearerToken }),
    }),
    {
      name: "agentium-connection",
      storage: createJSONStorage(() => sessionStorage),
    }
  )
);

export function canQueryProfile(): boolean {
  const s = useConnectionStore.getState();
  if (s.identityMode === "bearer") {
    return s.bearerToken.trim().length > 0;
  }
  return s.tenantId.trim().length > 0 && s.userId.trim().length > 0;
}
