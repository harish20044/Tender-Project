import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { fetchCategories, fetchTenders } from "../lib/api";
import { countdownTo, formatDate, formatDateTime } from "../lib/format";

/** Urgency shows as a stripe as well as colour, so it survives greyscale. */
const URGENCY_STRIPE: Record<string, string> = {
  critical: "bg-sev-critical",
  warning: "bg-sev-medium",
  normal: "bg-rule",
  past: "bg-rule-strong",
};

const URGENCY_TEXT: Record<string, string> = {
  critical: "text-sev-critical font-semibold",
  warning: "text-sev-medium font-semibold",
  normal: "text-ink-muted",
  past: "text-ink-faint",
};

export function DashboardPage() {
  const [constructionOnly, setConstructionOnly] = useState(true);
  const [category, setCategory] = useState<string | null>(null);

  const tendersQuery = useQuery({
    queryKey: ["tenders", constructionOnly, category],
    queryFn: () => fetchTenders({ constructionOnly, category }),
  });

  const categoriesQuery = useQuery({
    queryKey: ["categories"],
    queryFn: fetchCategories,
  });

  const tenders = tendersQuery.data?.tenders ?? [];
  const closingSoon = tenders.filter((tender) => {
    if (!tender.closing_at) return false;
    const { days } = countdownTo(tender.closing_at);
    return days >= 0 && days <= 7;
  }).length;
  const amended = tenders.filter((tender) => tender.corrigendum_count > 0).length;

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-2">
        <p className="label">Intake</p>
        <h1 className="text-2xl">Dashboard</h1>
        <p className="max-w-2xl text-ink-muted">
          Live tender notices from the Central Public Procurement Portal, soonest deadline
          first.
        </p>

        {tendersQuery.data?.scraped_at && (
          <p className="mt-1 text-xs text-ink-faint">
            {tendersQuery.data.source} · read{" "}
            {formatDateTime(tendersQuery.data.scraped_at)} ·{" "}
            {tendersQuery.data.total_before_filter} notices retrieved. Refresh with{" "}
            <code className="numeric">python scripts/scrape_tenders.py</code>.
          </p>
        )}
      </header>

      {tendersQuery.isError && (
        <div className="rounded border border-sev-critical bg-sev-critical-soft px-4 py-3">
          <p className="font-display font-semibold text-sev-critical">
            Could not load tenders
          </p>
          <p className="mt-1 text-sm text-ink-muted">
            {(tendersQuery.error as Error).message}
          </p>
        </div>
      )}

      {tendersQuery.isPending && <p className="text-ink-muted">Loading tenders…</p>}

      {tendersQuery.isSuccess && (
        <>
          <section className="grid grid-cols-2 gap-px overflow-hidden rounded border border-rule bg-rule lg:grid-cols-4">
            {[
              { label: "Showing", value: tenders.length, note: "after filters" },
              { label: "Retrieved", value: tendersQuery.data.total_before_filter, note: "from the portal" },
              { label: "Closing in 7 days", value: closingSoon, note: "too late to start after this" },
              { label: "Amended", value: amended, note: "carry a corrigendum" },
            ].map((stat) => (
              <div key={stat.label} className="flex flex-col gap-1 bg-surface px-5 py-4">
                <p className="label">{stat.label}</p>
                <p className="numeric text-3xl leading-none">{stat.value}</p>
                <p className="text-xs text-ink-faint">{stat.note}</p>
              </div>
            ))}
          </section>

          <section className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => setConstructionOnly((value) => !value)}
                aria-pressed={constructionOnly}
                className={[
                  "rounded border px-3 py-1 font-display text-xs font-medium transition-colors",
                  constructionOnly
                    ? "border-accent bg-accent text-on-accent"
                    : "border-rule bg-surface text-ink-muted hover:border-rule-strong hover:text-ink",
                ].join(" ")}
              >
                Construction work only
              </button>

              <span className="mx-1 h-4 w-px bg-rule" aria-hidden="true" />

              <button
                type="button"
                onClick={() => setCategory(null)}
                aria-pressed={category === null}
                className={[
                  "rounded border px-3 py-1 font-display text-xs font-medium transition-colors",
                  category === null
                    ? "border-accent bg-accent text-on-accent"
                    : "border-rule bg-surface text-ink-muted hover:border-rule-strong hover:text-ink",
                ].join(" ")}
              >
                All categories
              </button>

              {(categoriesQuery.data ?? []).map((entry) => (
                <button
                  key={entry.category}
                  type="button"
                  onClick={() => setCategory(entry.category)}
                  aria-pressed={category === entry.category}
                  className={[
                    "rounded border px-3 py-1 font-display text-xs font-medium transition-colors",
                    category === entry.category
                      ? "border-accent bg-accent text-on-accent"
                      : "border-rule bg-surface text-ink-muted hover:border-rule-strong hover:text-ink",
                  ].join(" ")}
                >
                  {entry.category}{" "}
                  <span className="numeric opacity-70">{entry.count}</span>
                </button>
              ))}
            </div>

            <div className="overflow-x-auto rounded border border-rule bg-surface">
              <table className="w-full min-w-[58rem] border-collapse text-left">
                <thead>
                  <tr className="border-b border-rule">
                    <th className="label px-4 py-3">Tender</th>
                    <th className="label px-4 py-3">Organisation</th>
                    <th className="label px-4 py-3">Published</th>
                    <th className="label px-4 py-3">Closing</th>
                  </tr>
                </thead>

                <tbody>
                  {tenders.map((tender) => {
                    const countdown = tender.closing_at
                      ? countdownTo(tender.closing_at)
                      : null;

                    return (
                      <tr
                        key={`${tender.reference}-${tender.tender_id ?? ""}`}
                        className="border-b border-rule last:border-b-0 hover:bg-surface-sunken"
                      >
                        <td className="px-4 py-3">
                          <div className="flex items-start gap-3">
                            <span
                              aria-hidden="true"
                              className={`mt-1 h-8 w-0.5 shrink-0 rounded ${
                                URGENCY_STRIPE[countdown?.urgency ?? "normal"]
                              }`}
                            />
                            <div className="flex max-w-xl flex-col gap-0.5">
                              <span className="numeric text-xs text-ink-faint">
                                {tender.reference}
                              </span>
                              <span className="font-medium leading-snug">
                                {tender.title}
                              </span>
                              <span className="text-xs text-ink-faint">
                                {tender.work_category ?? "Uncategorised"}
                                {tender.corrigendum_count > 0 && (
                                  <>
                                    {" · "}
                                    <span className="text-sev-medium">
                                      {tender.corrigendum_count} corrigend
                                      {tender.corrigendum_count === 1 ? "um" : "a"}
                                    </span>
                                  </>
                                )}
                              </span>
                            </div>
                          </div>
                        </td>

                        <td className="px-4 py-3 align-top text-sm text-ink-muted">
                          {tender.organisation}
                        </td>

                        <td className="numeric px-4 py-3 align-top text-sm text-ink-muted">
                          {tender.published_at ? formatDate(tender.published_at) : "—"}
                        </td>

                        <td className="px-4 py-3 align-top">
                          {tender.closing_at && countdown ? (
                            <div className="flex flex-col gap-0.5">
                              <span className="numeric text-sm">
                                {formatDate(tender.closing_at)}
                              </span>
                              <span className={`text-xs ${URGENCY_TEXT[countdown.urgency]}`}>
                                {countdown.label}
                              </span>
                            </div>
                          ) : (
                            <span className="text-ink-faint">—</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>

              {tenders.length === 0 && (
                <p className="px-4 py-8 text-center text-sm text-ink-muted">
                  Nothing matches those filters. The portal listing changes through the
                  day, so a refresh may bring different work.
                </p>
              )}
            </div>
          </section>
        </>
      )}
    </div>
  );
}
