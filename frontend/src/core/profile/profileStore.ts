import { create } from "zustand";
import type { MeResponse } from "./profileTypes";

interface ProfileStore {
  profile: MeResponse | null;
  setProfile: (p: MeResponse | null) => void;
}

export const useProfileStore = create<ProfileStore>((set) => ({
  profile: null,
  setProfile: (profile) => set({ profile }),
}));
