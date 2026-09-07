import { Navigate, Route, Routes } from "react-router-dom";

import { Sidebar } from "./components/Sidebar";
import { AskPage } from "./pages/AskPage";
import { ComparisonPage } from "./pages/ComparisonPage";
import { DashboardPage } from "./pages/DashboardPage";
import { DecisionReportPage } from "./pages/DecisionReportPage";
import { UploadPage } from "./pages/UploadPage";
import { WorkspacePage } from "./pages/WorkspacePage";

export function App() {
  return (
    // The sidebar holds its own height; only the main column scrolls, so the
    // navigation stays put on the long screens.
    <div className="flex h-screen overflow-hidden bg-ground text-ink">
      <Sidebar />

      <main className="flex-1 overflow-y-auto px-10 py-8">
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
