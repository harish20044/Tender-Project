import { useQuery } from "@tanstack/react-query";
import { Navigate, Route, Routes } from "react-router-dom";

import { Sidebar } from "./components/Sidebar";
import { useSession } from "./hooks/useSession";
import { fetchAuthConfig } from "./lib/api";
import { AskPage } from "./pages/AskPage";
import { ComparisonPage } from "./pages/ComparisonPage";
import { DashboardPage } from "./pages/DashboardPage";
import { DecisionReportPage } from "./pages/DecisionReportPage";
import { SignInPage } from "./pages/SignInPage";
import { UploadPage } from "./pages/UploadPage";
import { WorkspacePage } from "./pages/WorkspacePage";

export function App() {
  const session = useSession();

  // Whether sign-in is mandatory is the server's decision, not a second
  // setting kept here that could drift out of step with it.
  const config = useQuery({
    queryKey: ["auth-config"],
    queryFn: fetchAuthConfig,
    staleTime: Infinity,
    retry: 1,
  });

  // While the answer is unknown, show nothing rather than flashing the
  // dashboard at someone who is about to be sent to a sign-in screen.
  if (config.isLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-ground text-sm text-ink-muted">
        Loading…
      </div>
    );
  }

  // If the question itself cannot be answered the API is unreachable, and the
  // dashboard's own error states explain that far better than a sign-in form.
  const mustSignIn = config.data?.auth_required === true && session === null;
  if (mustSignIn) {
    return <SignInPage />;
  }

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
