import { PagePlaceholder } from "../components/PagePlaceholder";

export function ComparisonPage() {
  return (
    <PagePlaceholder
      title="Comparison"
      purpose="How this tender differs from past ones that resemble it."
      requirements={[
        "Ranked list of similar past tenders, each showing why it matched rather than a bare score.",
        "Field-by-field comparison of value, duration, eligibility thresholds and key contract terms.",
        "Similarity is a weighted blend of structured features, scope embeddings and bill-of-quantities overlap. Near-duplicate detection is reported separately, as it means a reissue rather than a resemblance.",
        "Corpus size is stated on the screen, and thin results are reported as low confidence rather than presented as strong matches.",
        "Outcome of each past tender shown where known, since a comparison without the result is of limited use.",
      ]}
    />
  );
}
