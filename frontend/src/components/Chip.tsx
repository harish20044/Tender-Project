import type { DecisionStatus, IngestStatus } from "../lib/sampleTenders";

/**
 * State shown as a shape and a word, not colour alone.
 *
 * A dashboard is scanned rather than read, so decision state has to register
 * at a glance; but colour on its own fails for anyone who cannot separate
 * these hues, so each chip carries its label too.
 */

const DECISION_STYLES: Record<DecisionStatus, { label: string; className: string }> = {
  bid: { label: "Bid", className: "bg-sev-low-soft text-sev-low" },
  no_bid: { label: "No-Bid", className: "bg-sev-critical-soft text-sev-critical" },
  review: { label: "Review", className: "bg-sev-medium-soft text-sev-medium" },
  pending: { label: "Pending", className: "bg-surface-sunken text-ink-muted" },
};

const INGEST_STYLES: Record<IngestStatus, { label: string; className: string }> = {
  complete: { label: "Ready", className: "text-ink-faint" },
  extracting: { label: "Extracting", className: "text-accent" },
  ocr: { label: "OCR", className: "text-accent" },
  failed: { label: "Failed", className: "text-sev-critical" },
};

export function DecisionChip({ status }: { status: DecisionStatus }) {
  const { label, className } = DECISION_STYLES[status];

  return (
    <span
      className={`inline-flex items-center rounded px-2 py-0.5 font-display text-xs font-semibold ${className}`}
    >
      {label}
    </span>
  );
}

export function IngestLabel({ status }: { status: IngestStatus }) {
  const { label, className } = INGEST_STYLES[status];
  const inProgress = status === "extracting" || status === "ocr";

  return (
    <span className={`inline-flex items-center gap-1.5 text-xs ${className}`}>
      {inProgress && (
        <span
          aria-hidden="true"
          className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent"
        />
      )}
      {label}
    </span>
  );
}
