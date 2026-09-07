import { NavLink, Navigate, Route, Routes } from "react-router-dom";

import { ComparisonPage } from "./pages/ComparisonPage";
import { DashboardPage } from "./pages/DashboardPage";
import { DecisionReportPage } from "./pages/DecisionReportPage";
import { AskPage } from "./pages/AskPage";
import { UploadPage } from "./pages/UploadPage";
import { WorkspacePage } from "./pages/WorkspacePage";

/** Route order matches the analyst's path through a tender, not the alphabet. */
const NAV = [
  { to: "/dashboard", label: "Dashboard" },
  { to: "/upload", label: "Upload" },
  { to: "/workspace", label: "Workspace" },
  { to: "/ask", label: "Ask" },
  { to: "/comparison", label: "Comparison" },
  { to: "/decision", label: "Decision" },
] as const;

export function App() {
  return (
    <div className="min-h-screen bg-ground text-ink">
      <header className="border-b border-rule bg-surface">
        <div className="flex items-center gap-8 px-6 py-3">
          <span className="font-display text-sm font-bold tracking-tight">
            Tender Intelligence
          </span>
          <nav className="flex gap-1">
            {NAV.map(({ to, label }) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  [
                    "label rounded px-3 py-1.5 transition-colors",
                    isActive
                      ? "bg-accent-soft text-accent"
                      : "text-ink-muted hover:text-ink",
                  ].join(" ")
                }
              >
                {label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>

      <main className="px-6 py-8">
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/upload" element={<UploadPage />} />
          <Route path="/workspace" element={<WorkspacePage />} />
          <Route path="/workspace/:tenderId" element={<WorkspacePage />} />
          <Route path="/ask" element={<AskPage />} />
          <Route path="/comparison" element={<ComparisonPage />} />
          <Route path="/decision" element={<DecisionReportPage />} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </main>
    </div>
  );
}
