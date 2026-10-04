import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import type { SimilarMatch } from "../lib/api";
import { fetchIngested, fetchSimilar, seedCorpus } from "../lib/api";

/** A bar rather than a bare number: the comparison is a ranking, and relative
 *  length is read faster than four decimal places. */
function Strength({ value }: { value: number }) {
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-24 overflow-hidden rounded bg-rule">
        <div
          className="h-full bg-accent"
          style={{ width: `${Math.max(2, Math.round(value * 100))}%` }}
        />
      </div>
      <span className="tabular-nums text-xs text-ink-muted">{(value * 100).toFixed(0)}%</span>
    </div>
  );
}

function MatchRow({ match }: { match: SimilarMatch }) {
  return (
    <div
      className={`flex flex-col gap-2 rounded border p-4 ${
        match.is_probable_reissue
          ? "border-sev-medium/50 bg-sev-medium/5"
          : "border-rule bg-surface"
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <p className="text-sm font-medium text-ink">{match.title}</p>
          <p className="text-xs text-ink-faint">
            {match.authority ?? "Unknown authority"}
            {match.category ? ` · ${match.category}` : ""}
            {match.published ? ` · published ${match.published}` : ""}
          </p>
        </div>
        {match.is_probable_reissue && (
          <span className="rounded border border-sev-medium/50 px-2 py-0.5 text-xs font-medium uppercase text-sev-medium">
            Probable reissue
          </span>
        )}
      </div>

      <p className="text-sm text-ink-muted">{match.why}</p>

      <div className="flex flex-wrap gap-x-8 gap-y-2">
        <div className="flex flex-col gap-1">
          <span className="text-xs uppercase tracking-wider text-ink-faint">Scope</span>
          <Strength value={match.scope_similarity} />
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-xs uppercase tracking-wider text-ink-faint">
            Same wording
          </span>
          <Strength value={match.reissue_likelihood} />
        </div>
        <div className="flex items-end gap-2 text-xs text-ink-muted">
          {match.same_authority && (
            <span className="rounded bg-rule px-2 py-0.5">Same authority</span>
          )}
          {match.same_category && (
            <span className="rounded bg-rule px-2 py-0.5">Same category</span>
          )}
        </div>
      </div>

      {match.reference && (
        <p className="font-mono text-xs text-ink-faint">{match.reference}</p>
      )}
    </div>
  );
}

export function ComparisonPage() {
  const [tenderId, setTenderId] = useState("");
  const ingested = useQuery({ queryKey: ["ingested"], queryFn: fetchIngested });
  const selected = tenderId || ingested.data?.[0]?.tender_id || "";

  const similar = useQuery({
    queryKey: ["similar", selected],
    queryFn: () => fetchSimilar(selected, 10),
    enabled: Boolean(selected),
  });

  const seed = useMutation({
    mutationFn: seedCorpus,
    onSuccess: () => void similar.refetch(),
  });

  const reissues = similar.data?.matches.filter((m) => m.is_probable_reissue) ?? [];

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-2">
        <p className="text-xs uppercase tracking-wider text-ink-faint">Analyse</p>
        <h1 className="text-2xl font-semibold text-ink">Against past tenders</h1>
        <p className="max-w-2xl text-sm text-ink-muted">
          Two different questions, kept separate: is this the same kind of work we have
          done before, and is this literally the same tender re-advertised? The second
          matters more, because the earlier round's outcome is informative.
        </p>
      </header>

      <div className="flex flex-wrap items-end gap-3">
        <label className="flex min-w-[20rem] flex-1 flex-col gap-1 text-sm" htmlFor="compare-tender">
          <span className="text-ink-muted">Tender</span>
          <select
            id="compare-tender"
            value={selected}
            onChange={(event) => setTenderId(event.target.value)}
            className="rounded border border-rule bg-surface px-3 py-2 text-sm text-ink"
          >
            {ingested.data?.length ? (
              ingested.data.map((row) => (
                <option key={row.tender_id} value={row.tender_id}>
                  {row.reference} — {row.title.slice(0, 55)}
                </option>
              ))
            ) : (
              <option value="">No tender ingested yet</option>
            )}
          </select>
        </label>
        <button
          type="button"
          disabled={seed.isPending}
          onClick={() => seed.mutate()}
          className="rounded border border-accent px-4 py-2 text-sm font-medium text-accent disabled:opacity-40"
        >
          {seed.isPending ? "Indexing…" : "Rebuild corpus"}
        </button>
      </div>

      {seed.data && (
        <p className="text-xs text-ink-muted">
          Corpus rebuilt: {seed.data.created} added, {seed.data.updated} refreshed,{" "}
          {seed.data.total} past tenders indexed.
        </p>
      )}

      {similar.isLoading && <p className="text-sm text-ink-muted">Comparing…</p>}
      {similar.isError && (
        <p className="text-sm text-sev-critical">{(similar.error as Error).message}</p>
      )}

      {similar.data && (
        <>
          <div className="flex flex-wrap gap-6 rounded border border-rule bg-surface p-4">
            <div>
              <p className="text-2xl font-semibold tabular-nums text-ink">
                {similar.data.corpus_size}
              </p>
              <p className="text-xs text-ink-faint">past tenders indexed</p>
            </div>
            <div>
              <p className="text-2xl font-semibold tabular-nums text-ink">
                {similar.data.matches.length}
              </p>
              <p className="text-xs text-ink-faint">comparable</p>
            </div>
            <div>
              <p
                className={`text-2xl font-semibold tabular-nums ${
                  reissues.length ? "text-sev-medium" : "text-ink"
                }`}
              >
                {reissues.length}
              </p>
              <p className="text-xs text-ink-faint">probable reissues</p>
            </div>
          </div>

          {similar.data.corpus_size === 0 && (
            <p className="text-sm text-ink-muted">
              The corpus is empty. Rebuild it to index the tenders already scraped from
              the portal.
            </p>
          )}

          <div className="flex flex-col gap-3">
            {similar.data.matches.map((match, index) => (
              <MatchRow key={`${match.reference ?? "match"}-${index}`} match={match} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
