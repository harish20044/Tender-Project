import { PagePlaceholder } from "../components/PagePlaceholder";

export function AskPage() {
  return (
    <PagePlaceholder
      title="Ask"
      purpose="Free-text questions against the tender, plus the fifty standard questions answered in advance."
      requirements={[
        "Free-text search over the tender, answered from retrieved passages only.",
        "The fifty standard questions answered at ingest and cached, so opening this screen costs nothing.",
        "Every answer cites the pages it came from, and each citation opens that page in the workspace viewer.",
        "An answer the retrieved passages do not support says so, rather than filling the gap.",
        "Questions grouped by theme: eligibility, financial, technical, contractual, timeline.",
      ]}
    />
  );
}
