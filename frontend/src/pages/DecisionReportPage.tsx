import { PagePlaceholder } from "../components/PagePlaceholder";

export function DecisionReportPage() {
  return (
    <PagePlaceholder
      title="Decision Report"
      purpose="The recommendation, with every input that produced it laid open for checking."
      requirements={[
        "Hard eligibility gates listed with pass or fail, the threshold, our value, and the page each was read from.",
        "A single failed mandatory gate produces No-Bid regardless of any other score, and the report says which gate decided it.",
        "Risk register across the contract taxonomy: liquidated damages, defect liability, price escalation, retention, arbitration, site handover, bond percentages, unbalanced items, timeline feasibility.",
        "Written rationale that narrates the scorecard and introduces no fact absent from it.",
        "Counterfactuals where a gate failed narrowly, for example the turnover shortfall that a joint venture would close.",
        "Export to PDF, since the recommendation is circulated to people who will not open this application.",
        "Recomputing the same tender with unchanged inputs must give the same result, and the report carries the timestamp and input version that produced it.",
      ]}
    />
  );
}
