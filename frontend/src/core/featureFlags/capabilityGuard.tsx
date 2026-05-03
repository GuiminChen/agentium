import type { ReactElement, ReactNode } from "react";
import { useHasCapability } from "./useHasCapability";

export function CapabilityGuard({
  need,
  children,
  label = "Action",
}: {
  need: string;
  children: ReactNode;
  label?: string;
}): ReactElement {
  const ok = useHasCapability(need);
  if (ok) {
    return <>{children}</>;
  }
  const title = `${label} requires capability: ${need}`;
  return (
    <span title={title} className="inline-flex cursor-not-allowed opacity-50">
      {children}
    </span>
  );
}
