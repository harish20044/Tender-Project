import { PagePlaceholder } from "../components/PagePlaceholder";

export function DashboardPage() {
  return (
    <PagePlaceholder
      title="Dashboard"
      purpose="Every tender currently in play, ordered by how soon it closes."
      requirements={[
        "One row per tender: reference number, issuing authority, estimated value, closing date.",
        "Decision status shown as a chip, so state reads at a glance rather than being parsed from text.",
        "Deadline countdown that shifts to a warning treatment inside seven days.",
        "Ingestion state visible for documents still being processed, with a link to the progress view.",
        "Sortable by closing date and estimated value, filterable by decision status.",
      ]}
    />
  );
}
