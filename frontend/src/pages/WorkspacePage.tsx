import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import type { Fact } from "../lib/api";
import { correctFact, fetchFacts, fetchIngested } from "../lib/api";

/** Groups mirror how a tender is actually read, not how the keys are spelled. */
const GROUPS: { label: string; prefixes: string[] }[] = [
  { label: "Eligibility", prefixes: ["eligibility."] },
  { label: "Cost and security", prefixes: ["value.", "emd.", "fee.", "security.", "advance."] },
  { label: "Deadlines", prefixes: ["date.", "duration."] },
  { label: "Risk", prefixes: ["risk."] },
];

/** Below this, a value is marked for a human to confirm rather than shown as
 *  settled — presenting a shaky extraction as fact is the failure that matters. */
const REVIEW_BELOW = 0.75;

function formatValue(fact: Fact): string {
  if (typeof fact.value === "number") {
    return fact.value.toLocaleString("en-IN");
  }
  if (typeof fact.value === "boolean") {
    return fact.value ? "Yes" : "No";
  }
  return String(fact.value ?? "—");
}

function FindingRow({
  fact,
  tenderId,
  documentUrl,
}: {
  fact: Fact;
  tenderId: string;
  documentUrl: string | null;
}) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  const correct = useMutation({
    mutationFn: () => {
      const parsed = Number(draft);
      return correctFact(tenderId, fact.key, Number.isFinite(parsed) && draft.trim() ? parsed : draft);
    },
    onSuccess: () => {
      setEditing(false);
      void queryClient.invalidateQueries({ queryKey: ["facts", tenderId] });
    },
  });

  const uncertain = fact.confidence < REVIEW_BELOW;

  return (
    <div className="flex flex-col gap-2 border-l-2 border-rule py-3 pl-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-mono text-xs text-ink-muted">{fact.key}</span>
        <div className="flex items-center gap-2">
          {uncertain && (
            <span className="rounded bg-sev-medium/10 px-2 py-0.5 text-xs text-sev-medium">
              confirm ({(fact.confidence * 100).toFixed(0)}%)
            </span>
          )}
          <button
            type="button"
            onClick={() => {
              setDraft(String(fact.value ?? ""));
              setEditing((open) => !open);
            }}
            className="text-xs text-accent underline-offset-2 hover:underline"
          >
            {editing ? "Cancel" : "Correct"}
          </button>
        </div>
      </div>

      {editing ? (
        <div className="flex flex-wrap items-center gap-2">
          <input
            id={`correct-${fact.key}`}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            className="w-56 rounded border border-rule bg-canvas px-2 py-1 font-mono text-sm text-ink"
          />
          <button
            type="button"
            disabled={correct.isPending}
            onClick={() => correct.mutate()}
            className="rounded bg-accent px-3 py-1 text-xs font-medium text-white disabled:opacity-40"
          >
            {correct.isPending ? "Saving…" : "Save correction"}
          </button>
          <span className="text-xs text-ink-faint">
            The original is kept; the decision re-scores from the corrected value.
          </span>
        </div>
      ) : (
        <p className="text-lg font-semibold tabular-nums text-ink">
          {formatValue(fact)}
          {fact.unit && <span className="ml-2 text-xs font-normal text-ink-faint">{fact.unit}</span>}
        </p>
      )}

      {correct.isError && (
        <p className="text-xs text-sev-critical">{(correct.error as Error).message}</p>
      )}

      {fact.quote && (
        <blockquote className="border-l border-rule pl-3 text-xs italic text-ink-muted">
          “{fact.quote}”
        </blockquote>
      )}

      {fact.page && (
        <p className="text-xs text-ink-faint">
          {documentUrl ? (
            <a
              href={`${documentUrl}#page=${fact.page}`}
              target="_blank"
              rel="noreferrer"
              className="text-accent underline-offset-2 hover:underline"
            >
              Open page {fact.page} in the source
            </a>
          ) : (
            <>page {fact.page}</>
          )}
        </p>
      )}
    </div>
  );
}

export function WorkspacePage() {
  const [tenderId, setTenderId] = useState("");
  const ingested = useQuery({ queryKey: ["ingested"], queryFn: fetchIngested });
  const selected = tenderId || ingested.data?.[0]?.tender_id || "";

  const facts = useQuery({
    queryKey: ["facts", selected],
    queryFn: () => fetchFacts(selected),
    enabled: Boolean(selected),
  });

  const all = facts.data?.facts ?? [];
  const grouped = GROUPS.map((group) => ({
    label: group.label,
    facts: all.filter((fact) => group.prefixes.some((prefix) => fact.key.startsWith(prefix))),
  })).filter((group) => group.facts.length);

  const ungrouped = all.filter(
    (fact) => !GROUPS.some((g) => g.prefixes.some((p) => fact.key.startsWith(p))),
  );
  const needsReview = all.filter((fact) => fact.confidence < REVIEW_BELOW).length;

  // Taken from the API rather than rebuilt from the reference: the storage
  // key is the backend's business, and guessing it here would break the
  // moment that naming changes.
  const documentUrl =
    ingested.data?.find((row) => row.tender_id === selected)?.document_url ?? null;

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-2">
        <p className="text-xs uppercase tracking-wider text-ink-faint">Analyse</p>
        <h1 className="text-2xl font-semibold text-ink">Workspace</h1>
        <p className="max-w-2xl text-sm text-ink-muted">
          Every finding sits beside the sentence it was read from and the page it came
          from. A value that looks wrong can be corrected here; the original is kept, and
          the decision re-scores from the correction.
        </p>
      </header>

      <div className="flex flex-wrap items-end gap-3">
        <label className="flex min-w-[20rem] flex-1 flex-col gap-1 text-sm" htmlFor="workspace-tender">
          <span className="text-ink-muted">Tender</span>
          <select
            id="workspace-tender"
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
      </div>

      {!all.length && (
        <p className="text-sm text-ink-muted">
          No findings yet. Extract this tender's facts on the Decision Report screen and
          they will appear here beside their source text.
        </p>
      )}

      {!!all.length && (
        <>
          <div className="flex flex-wrap gap-6 rounded border border-rule bg-surface p-4">
            <div>
              <p className="text-2xl font-semibold tabular-nums text-ink">{all.length}</p>
              <p className="text-xs text-ink-faint">findings</p>
            </div>
            <div>
              <p
                className={`text-2xl font-semibold tabular-nums ${
                  needsReview ? "text-sev-medium" : "text-ink"
                }`}
              >
                {needsReview}
              </p>
              <p className="text-xs text-ink-faint">need confirming</p>
            </div>
            <div>
              <p className="text-2xl font-semibold tabular-nums text-ink">
                {all.filter((f) => f.page).length}
              </p>
              <p className="text-xs text-ink-faint">traced to a page</p>
            </div>
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            {[...grouped, ...(ungrouped.length ? [{ label: "Other", facts: ungrouped }] : [])].map(
              (group) => (
                <section key={group.label} className="flex flex-col gap-1">
                  <h2 className="text-xs uppercase tracking-wider text-ink-faint">
                    {group.label}
                  </h2>
                  <div className="rounded border border-rule bg-surface px-4">
                    {group.facts.map((fact) => (
                      <FindingRow
                        key={fact.key}
                        fact={fact}
                        tenderId={selected}
                        documentUrl={documentUrl}
                      />
                    ))}
                  </div>
                </section>
              ),
            )}
          </div>
        </>
      )}
    </div>
  );
}
