import { NavLink } from "react-router-dom";

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

      <div className="border-t border-rule px-5 py-3">
        <p className="label">Status</p>
        <p className="mt-1 text-xs text-ink-faint">
          Scaffold. Screens are routed and specified, not yet wired to the API.
        </p>
      </div>
    </aside>
  );
}
