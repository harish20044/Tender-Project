import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import type { DecisionResult, Gate } from "../lib/api";
import { computeDecision, extractFacts, fetchFacts, fetchIngested } from "../lib/api";

/** Verdict colour is reinforced by the label itself, so the meaning survives
 *  greyscale printing — which is how a decision report usually gets circulated. */
const VERDICT: Record<DecisionResult["recommendation"], { label: string; className: string }> = {
  bid: { label: "BID", className: "bg-sev-low/10 text-sev-low border-sev-low/40" },
  no_bid: {
    label: "NO BID",
    className: "bg-sev-critical/10 text-sev-critical border-sev-critical/40",
  },
  review: {
    label: "NEEDS REVIEW",
    className: "bg-sev-medium/10 text-sev-medium border-sev-medium/40",
  },
};

const OUTCOME: Record<Gate["outcome"], string> = {
  pass: "text-sev-low",
  fail: "text-sev-critical",
  unknown: "text-sev-medium",
};

const SEVERITY: Record<string, string> = {
  low: "text-ink-muted",
  medium: "text-sev-medium",
  high: "text-sev-critical",
  critical: "text-sev-critical font-semibold",
};

function GateRow({ gate }: { gate: Gate }) {
  return (
    <tr className="border-b border-rule last:border-0">
      <td className="px-4 py-2">
        <span className={`font-mono text-xs uppercase ${OUTCOME[gate.outcome]}`}>
          {gate.outcome}
        </span>
      </td>
      <td className="px-4 py-2 text-ink">
        {gate.label}
        {gate.mandatory && (
          <span className="ml-2 rounded bg-rule px-1 text-[10px] uppercase text-ink-faint">
            mandatory
          </span>
        )}
      </td>
      <td className="px-4 py-2 text-sm text-ink-muted">{gate.detail}</td>
      <td className="px-4 py-2 text-right font-mono text-xs text-ink-faint">
        {gate.page ? `p.${gate.page}` : "—"}
      </td>
    </tr>
  );
}

export function DecisionReportPage() {
  const [tenderId, setTenderId] = useState("");
  const ingested = useQuery({ queryKey: ["ingested"], queryFn: fetchIngested });
  const selected = tenderId || ingested.data?.[0]?.tender_id || "";

  const facts = useQuery({
    queryKey: ["facts", selected],
    queryFn: () => fetchFacts(selected),
    enabled: Boolean(selected),
  });

  const extract = useMutation({
    mutationFn: () => extractFacts(selected),
    onSuccess: () => void facts.refetch(),
  });

  /** Scoring needs facts, so a tender that has none gets extracted first rather
   *  than returning an error that tells the user to press the other button. */
  const decide = useMutation({
    mutationFn: async () => {
      if (!facts.data?.extracted) {
        await extractFacts(selected);
        await facts.refetch();
      }
      return computeDecision(selected);
    },
  });

  const decision = decide.data;
  const verdict = decision ? VERDICT[decision.recommendation] : null;

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-2">
        <p className="text-xs uppercase tracking-wider text-ink-faint">Decide</p>
        <h1 className="text-2xl font-semibold text-ink">Decision report</h1>
        <p className="max-w-2xl text-sm text-ink-muted">
          The recommendation is computed by rules from the extracted figures, not by a
          model. The model only writes the explanation, after the verdict is settled.
        </p>
      </header>

      <div className="flex flex-wrap items-end gap-3">
        <label className="flex min-w-[20rem] flex-1 flex-col gap-1 text-sm" htmlFor="decision-tender">
          <span className="text-ink-muted">Tender</span>
          <select
            id="decision-tender"
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
          disabled={!selected || extract.isPending}
          onClick={() => extract.mutate()}
          className="rounded border border-accent px-4 py-2 text-sm font-medium text-accent disabled:opacity-40"
        >
          {extract.isPending ? "Reading…" : "1. Extract facts"}
        </button>
        <button
          type="button"
          disabled={!selected || decide.isPending}
          onClick={() => decide.mutate()}
          className="rounded bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
        >
          {decide.isPending
            ? facts.data?.extracted
              ? "Scoring…"
              : "Reading, then scoring…"
            : "2. Score the bid"}
        </button>
      </div>

      {(extract.isError || decide.isError) && (
        <p className="rounded border border-sev-critical/40 bg-sev-critical/5 px-3 py-2 text-sm text-sev-critical">
          {((extract.error ?? decide.error) as Error).message}
        </p>
      )}

      {decision && verdict && (
        <section className="flex flex-col gap-5">
          <div className="flex flex-wrap items-center gap-5 rounded border border-rule bg-surface p-5">
            <div
              className={`rounded border px-5 py-3 text-xl font-semibold tracking-wide ${verdict.className}`}
            >
              {verdict.label}
            </div>
            <div>
              <p className="text-3xl font-semibold tabular-nums text-ink">{decision.score}</p>
              <p className="text-xs text-ink-faint">score of 100</p>
            </div>
            {decision.deciding_gate && (
              <div>
                <p className="text-sm font-medium text-ink">{decision.deciding_gate}</p>
                <p className="text-xs text-ink-faint">deciding gate</p>
              </div>
            )}
            <div className="ml-auto text-right">
              <p className="font-mono text-xs text-ink-faint">
                ruleset {decision.ruleset_version}
              </p>
              <p className="text-xs text-ink-faint">{decision.profile}</p>
            </div>
          </div>

          {decision.rationale && (
            <p className="max-w-3xl whitespace-pre-wrap border-l-2 border-accent pl-4 text-sm text-ink">
              {decision.rationale}
            </p>
          )}

          <div className="overflow-x-auto rounded border border-rule">
            <table className="w-full min-w-[46rem] border-collapse bg-surface text-sm">
              <thead>
                <tr className="border-b border-rule text-left text-xs uppercase tracking-wider text-ink-faint">
                  <th className="px-4 py-2 font-medium">Outcome</th>
                  <th className="px-4 py-2 font-medium">Gate</th>
                  <th className="px-4 py-2 font-medium">Why</th>
                  <th className="px-4 py-2 text-right font-medium">Source</th>
                </tr>
              </thead>
              <tbody>
                {decision.gates.map((gate) => (
                  <GateRow key={gate.key} gate={gate} />
                ))}
              </tbody>
            </table>
          </div>

          {!!decision.counterfactuals.length && (
            <div className="flex flex-col gap-2">
              <h2 className="text-sm font-semibold text-ink">What would have to change</h2>
              <ul className="flex flex-col gap-1 text-sm text-ink-muted">
                {decision.counterfactuals.map((item) => (
                  <li key={item.gate}>• {item.statement}</li>
                ))}
              </ul>
            </div>
          )}

          {!!decision.risks.length && (
            <div className="flex flex-col gap-2">
              <h2 className="text-sm font-semibold text-ink">Risk register</h2>
              <ul className="flex flex-col gap-2">
                {decision.risks.map((risk, index) => (
                  <li
                    key={`${risk.category}-${index}`}
                    className="flex flex-wrap items-baseline gap-2 border-l-2 border-rule pl-3 text-sm"
                  >
                    <span
                      className={`font-mono text-xs uppercase ${SEVERITY[risk.severity] ?? ""}`}
                    >
                      {risk.severity}
                    </span>
                    <span className="text-ink">{risk.summary}</span>
                    {risk.page && (
                      <span className="font-mono text-xs text-ink-faint">p.{risk.page}</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}

      <section className="flex flex-col gap-3">
        <h2 className="text-sm font-semibold text-ink">
          Extracted facts{" "}
          <span className="font-normal text-ink-faint">
            {facts.data ? `(${facts.data.extracted})` : ""}
          </span>
        </h2>
        {!facts.data?.extracted && (
          <p className="text-sm text-ink-muted">
            No facts extracted yet. Run step 1 to read the figures out of the document.
          </p>
        )}
        {!!facts.data?.extracted && (
          <div className="overflow-x-auto rounded border border-rule">
            <table className="w-full min-w-[40rem] border-collapse bg-surface text-sm">
              <thead>
                <tr className="border-b border-rule text-left text-xs uppercase tracking-wider text-ink-faint">
                  <th className="px-4 py-2 font-medium">Fact</th>
                  <th className="px-4 py-2 text-right font-medium">Value</th>
                  <th className="px-4 py-2 font-medium">Unit</th>
                  <th className="px-4 py-2 text-right font-medium">Page</th>
                </tr>
              </thead>
              <tbody>
                {facts.data.facts.map((fact) => (
                  <tr key={fact.key} className="border-b border-rule last:border-0">
                    <td className="px-4 py-2 font-mono text-xs text-ink">{fact.key}</td>
                    <td className="px-4 py-2 text-right tabular-nums text-ink">
                      {typeof fact.value === "number"
                        ? fact.value.toLocaleString("en-IN")
                        : String(fact.value)}
                    </td>
                    <td className="px-4 py-2 text-xs text-ink-faint">{fact.unit ?? "—"}</td>
                    <td className="px-4 py-2 text-right font-mono text-xs text-ink-faint">
                      {fact.page ? `p.${fact.page}` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
