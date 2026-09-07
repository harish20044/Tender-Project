import { useMemo, useState } from "react";

import { DecisionChip, IngestLabel } from "../components/Chip";
import { countdownTo, formatDate, formatRupees } from "../lib/format";
import { SAMPLE_TENDERS, type DecisionStatus } from "../lib/sampleTenders";

type Filter = "all" | DecisionStatus;

const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "pending", label: "Pending" },
  { value: "review", label: "Review" },
  { value: "bid", label: "Bid" },
  { value: "no_bid", label: "No-Bid" },
];

/** Urgency is carried by a stripe as well as colour, so it survives greyscale. */
const URGENCY_STRIPE: Record<string, string> = {
  critical: "bg-sev-critical",
  warning: "bg-sev-medium",
  normal: "bg-transparent",
  past: "bg-rule-strong",
};

const URGENCY_TEXT: Record<string, string> = {
  critical: "text-sev-critical font-semibold",
  warning: "text-sev-medium font-semibold",
  normal: "text-ink-muted",
  past: "text-ink-faint",
};

export function DashboardPage() {
  const [filter, setFilter] = useState<Filter>("all");

  const rows = useMemo(
    () =>
      [...SAMPLE_TENDERS]
        .filter((tender) => filter === "all" || tender.decision === filter)
        // Soonest deadline first: the only ordering that matches how these get
        // triaged in practice.
        .sort((a, b) => +new Date(a.closingDate) - +new Date(b.closingDate)),
    [filter],
  );

  const stats = useMemo(() => {
    const live = SAMPLE_TENDERS.filter((t) => countdownTo(t.closingDate).days >= 0);
    return [
      { label: "Live tenders", value: live.length, note: "not yet closed" },
      {
        label: "Closing in 7 days",
        value: live.filter((t) => countdownTo(t.closingDate).days <= 7).length,
        note: "needs a decision now",
      },
      {
        label: "Awaiting decision",
        value: SAMPLE_TENDERS.filter((t) => t.decision === "pending").length,
        note: "still ingesting or unscored",
      },
      {
        label: "Recommended to bid",
        value: SAMPLE_TENDERS.filter((t) => t.decision === "bid").length,
        note: "all gates passed",
      },
    ];
  }, []);

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-2">
        <p className="label">Intake</p>
        <h1 className="text-2xl">Dashboard</h1>
        <p className="max-w-2xl text-ink-muted">
          Every tender in play, soonest deadline first.
        </p>
        <p className="mt-1 inline-flex w-fit items-center rounded border border-rule bg-surface-sunken px-2 py-1 text-xs text-ink-muted">
          Example data. These are illustrative rows, not real tenders, shown until the API is
          connected.
        </p>
      </header>

      {/* Counts first, because the question on opening this screen is what
          needs attention, not what exists. */}
      <section className="grid grid-cols-2 gap-px overflow-hidden rounded border border-rule bg-rule lg:grid-cols-4">
        {stats.map((stat) => (
          <div key={stat.label} className="flex flex-col gap-1 bg-surface px-5 py-4">
            <p className="label">{stat.label}</p>
            <p className="numeric text-3xl leading-none text-ink">{stat.value}</p>
            <p className="text-xs text-ink-faint">{stat.note}</p>
          </div>
        ))}
      </section>

      <section className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="label mr-1">Decision</span>
          {FILTERS.map((option) => {
            const isActive = filter === option.value;
            return (
              <button
                key={option.value}
                type="button"
                onClick={() => setFilter(option.value)}
                aria-pressed={isActive}
                className={[
                  "rounded border px-3 py-1 font-display text-xs font-medium transition-colors",
                  isActive
                    ? "border-accent bg-accent text-on-accent"
                    : "border-rule bg-surface text-ink-muted hover:border-rule-strong hover:text-ink",
                ].join(" ")}
              >
                {option.label}
              </button>
            );
          })}
        </div>

        {/* Seven columns will not fit a narrow window; the table scrolls in its
            own container so the page body never scrolls sideways. */}
        <div className="overflow-x-auto rounded border border-rule bg-surface">
          <table className="w-full min-w-[62rem] border-collapse text-left">
            <thead>
              <tr className="border-b border-rule">
                <th className="label px-4 py-3">Tender</th>
                <th className="label px-4 py-3">Authority</th>
                <th className="label px-4 py-3 text-right">Value</th>
                <th className="label px-4 py-3">Closing</th>
                <th className="label px-4 py-3">Ingest</th>
                <th className="label px-4 py-3">Decision</th>
              </tr>
            </thead>

            <tbody>
              {rows.map((tender) => {
                const countdown = countdownTo(tender.closingDate);

                return (
                  <tr
                    key={tender.id}
                    className="border-b border-rule last:border-b-0 hover:bg-surface-sunken"
                  >
                    <td className="px-4 py-3">
                      <div className="flex items-start gap-3">
                        <span
                          aria-hidden="true"
                          className={`mt-1 h-8 w-0.5 shrink-0 rounded ${URGENCY_STRIPE[countdown.urgency]}`}
                        />
                        <div className="flex flex-col gap-0.5">
                          <span className="numeric text-xs text-ink-faint">
                            {tender.reference}
                          </span>
                          <span className="font-medium leading-snug">{tender.title}</span>
                          <span className="text-xs text-ink-faint">
                            {tender.workCategory}
                            {tender.corrigendumCount > 0 && (
                              <>
                                {" · "}
                                <span className="text-sev-medium">
                                  {tender.corrigendumCount} corrigend
                                  {tender.corrigendumCount === 1 ? "um" : "a"}
                                </span>
                              </>
                            )}
                          </span>
                        </div>
                      </div>
                    </td>

                    <td className="px-4 py-3 align-top text-sm text-ink-muted">
                      {tender.authority}
                    </td>

                    <td className="numeric px-4 py-3 text-right align-top">
                      {formatRupees(tender.value)}
                    </td>

                    <td className="px-4 py-3 align-top">
                      <div className="flex flex-col gap-0.5">
                        <span className="numeric text-sm">{formatDate(tender.closingDate)}</span>
                        <span className={`text-xs ${URGENCY_TEXT[countdown.urgency]}`}>
                          {countdown.label}
                        </span>
                      </div>
                    </td>

                    <td className="px-4 py-3 align-top">
                      <IngestLabel status={tender.ingest} />
                    </td>

                    <td className="px-4 py-3 align-top">
                      <div className="flex flex-col items-start gap-1">
                        <DecisionChip status={tender.decision} />
                        {tender.decidingGate ? (
                          <span className="text-xs text-ink-faint">
                            Failed: {tender.decidingGate}
                          </span>
                        ) : (
                          tender.score !== null && (
                            <span className="numeric text-xs text-ink-faint">
                              Score {tender.score}
                            </span>
                          )
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          {rows.length === 0 && (
            <p className="px-4 py-8 text-center text-sm text-ink-muted">
              No tenders with that decision status.
            </p>
          )}
        </div>
      </section>
    </div>
  );
}
