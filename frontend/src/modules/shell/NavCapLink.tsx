import type { ReactElement } from "react";
import { NavLink } from "react-router-dom";
import { useHasCapability } from "../../core/featureFlags/useHasCapability";

export function NavCapLink({
  to,
  end,
  need,
  children,
}: {
  to: string;
  end?: boolean;
  need?: string;
  children: string;
}): ReactElement {
  const granted = useHasCapability(need ?? "me.read");
  const ok = need ? granted : true;
  return (
    <NavLink
      to={to}
      end={end}
      aria-disabled={!ok}
      className={({ isActive }) =>
        !ok
          ? "cursor-not-allowed text-slate-400 line-through outline-none"
          : isActive
            ? "font-medium text-blue-700"
            : "text-slate-700 hover:underline"
      }
      onClick={(e) => {
        if (!ok) {
          e.preventDefault();
        }
      }}
    >
      {children}
    </NavLink>
  );
}
