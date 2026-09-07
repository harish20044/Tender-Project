import { PagePlaceholder } from "../components/PagePlaceholder";

export function WorkspacePage() {
  return (
    <PagePlaceholder
      title="Tender Workspace"
      purpose="The core screen. Extracted findings sit beside the source document, and every finding can be traced back to the text that produced it."
      requirements={[
        "Split view: pdf.js viewer on the left, extracted findings grouped by category on the right.",
        "Clicking a finding scrolls the viewer to its page and draws its bounding box.",
        "Findings grouped as eligibility, cost, deadlines, and scope, matching how the document is read.",
        "Each finding shows an extraction confidence, and low-confidence values are marked for human review rather than presented as settled.",
        "A finding whose value looks wrong can be corrected in place, and the correction is recorded against the original.",
        "Corrigendum versions are selectable, with changed fields marked against the previous version.",
      ]}
    />
  );
}
