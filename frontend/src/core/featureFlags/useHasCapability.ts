import { useProfileStore } from "../profile/profileStore";

/**
 * Capability check derived as a boolean so Zustand's snapshot stays stable.
 * (Returning `profile?.capabilities ?? []` created a new [] each render when profile was null → infinite loop with React 18 + useSyncExternalStore.)
 */
export function useHasCapability(cap: string): boolean {
  return useProfileStore((s) => Boolean(s.profile?.capabilities?.includes(cap)));
}
