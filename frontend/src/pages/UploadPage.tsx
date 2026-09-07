import { PagePlaceholder } from "../components/PagePlaceholder";

export function UploadPage() {
  return (
    <PagePlaceholder
      title="Upload & Ingest"
      purpose="Accept a tender pack and show its progress through the pipeline honestly."
      requirements={[
        "Drag and drop for PDF, Word, Excel and ZIP archives, since tender packs arrive as mixed bundles.",
        "Per-stage progress over server-sent events: parse, OCR, chunk, embed, extract, score.",
        "Page count and an estimated token spend shown before ingest starts, because both providers are metered.",
        "Explicit notice when a document is routed to OCR, as scanned packs take substantially longer.",
        "A failed stage reports which stage failed and offers a retry of that stage alone, not the whole job.",
      ]}
    />
  );
}
