import { useMutation } from "@tanstack/react-query";
import { NavLink } from "react-router-dom";

import { useSession } from "../hooks/useSession";
import { signOut } from "../lib/api";

/**
 * Navigation, grouped by the stage of work rather than alphabetically.
 *
 * The three groups are the actual sequence an analyst moves through: get the
 * tender in, understand it, then decide on it. Grouping encodes that order,
 * which is why it is worth the extra structure over a flat list.
 */
const SECTIONS = [
  {
    label: "Intake",
    items: [
      { to: "/dashboard", label: "Dashboard", hint: "Everything in play" },
      { to: "/upload", label: "Upload", hint: "Add a tender pack" },
    ],
  },
  {
    label: "Analyse",
    items: [
      { to: "/workspace", label: "Workspace", hint: "Findings beside the source" },
      { to: "/ask", label: "Ask", hint: "Questions and standard FAQs" },
      { to: "/comparison", label: "Comparison", hint: "Against past tenders" },
    ],
  },
  {
    label: "Decide",
    items: [{ to: "/decision", label: "Decision Report", hint: "Scorecard and rationale" }],
  },
] as const;

/** Who is signed in, and the way out.
 *
 * Absent a session this says so rather than disappearing: when sign-in is
 * optional, "nobody is signed in" explains why work is not being attributed
 * to anyone, which an empty corner would not.
 */
function SessionPanel() {
  const session = useSession();
  const out = useMutation({ mutationFn: signOut });

  if (!session) {
    return (
      <div className="border-t border-rule px-5 py-3">
        <p className="label">Session</p>
        <p className="mt-1 text-xs text-ink-faint">
          Not signed in. Work is not attributed to anyone.
        </p>
      </div>
    );
  }

  return (
    <div className="border-t border-rule px-5 py-3">
      <p className="label">Signed in</p>
      <p className="mt-1 truncate text-xs text-ink" title={session.user.email}>
        {session.user.email}
      </p>
      <div className="mt-1 flex items-center justify-between gap-2">
        <span className="rounded bg-rule px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-ink-muted">
          {session.user.role}
        </span>
        <button
          type="button"
          disabled={out.isPending}
          onClick={() => out.mutate()}
          className="text-xs text-accent underline-offset-2 hover:underline disabled:opacity-40"
        >
          {out.isPending ? "Signing out…" : "Sign out"}
        </button>
      </div>
    </div>
  );
}

export function Sidebar() {
  return (
    <aside className="flex h-full w-60 shrink-0 flex-col border-r border-rule bg-surface">
      {/* Title block, in the manner of a drawing sheet. */}
      <div className="border-b border-rule px-5 py-4">
        <p className="label">Bid Support</p>
        <h1 className="mt-1 font-display text-base font-bold leading-tight">
          Tender Intelligence
        </h1>
      </div>

      <nav className="flex flex-1 flex-col gap-6 overflow-y-auto px-3 py-5">
        {SECTIONS.map((section) => (
          <div key={section.label} className="flex flex-col gap-1">
            <p className="label px-2 pb-1">{section.label}</p>

            {section.items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  [
                    "group flex flex-col gap-0.5 rounded px-2 py-2 transition-colors",
                    isActive
                      ? "bg-accent-soft text-accent"
                      : "text-ink hover:bg-surface-sunken",
                  ].join(" ")
                }
              >
                {({ isActive }) => (
                  <>
                    <span className="font-display text-sm font-medium leading-none">
                      {item.label}
                    </span>
                    <span
                      className={[
                        "text-xs leading-tight",
                        isActive ? "text-accent" : "text-ink-faint",
                      ].join(" ")}
                    >
                      {item.hint}
                    </span>
                  </>
                )}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>

      <SessionPanel />
    </aside>
  );
}
