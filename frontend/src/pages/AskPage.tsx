import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import type { AnswerResult, Citation, FaqItem } from "../lib/api";
import { askQuestion, fetchIngested, runFaqs } from "../lib/api";

function Citations({ citations }: { citations: Citation[] }) {
  if (!citations.length) return null;
  return (
    <ul className="flex flex-wrap gap-2">
      {citations.map((citation) => (
        <li
          key={citation.chunk_id + citation.marker}
          className="rounded border border-rule bg-canvas px-2 py-1 font-mono text-xs text-ink-muted"
        >
          [{citation.marker}] {citation.citation}
        </li>
      ))}
    </ul>
  );
}

/** Unanswerable is shown plainly rather than dressed up: a tender that does not
 *  state something is a fact worth seeing, not a failure to hide. */
function Answer({ result }: { result: AnswerResult | FaqItem }) {
  const answerable = result.is_answerable;
  return (
    <div className="flex flex-col gap-2">
      <p
        className={`whitespace-pre-wrap text-sm ${
          answerable ? "text-ink" : "text-ink-faint italic"
        }`}
      >
        {result.answer}
      </p>
      <Citations citations={result.citations} />
    </div>
  );
}

export function AskPage() {
  const [tenderId, setTenderId] = useState("");
  const [question, setQuestion] = useState("");
  const [category, setCategory] = useState<string>("");

  const ingested = useQuery({ queryKey: ["ingested"], queryFn: fetchIngested });

  // Default to the most recently ingested tender so the page is usable
  // immediately rather than opening on an empty selector.
  const selected = tenderId || ingested.data?.[0]?.tender_id || "";

  const ask = useMutation({
    mutationFn: () => askQuestion(selected, question),
  });
  const faqs = useMutation({
    mutationFn: () => runFaqs(selected, category || undefined),
  });

  const categories = Array.from(new Set((faqs.data?.items ?? []).map((i) => i.category)));

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-2">
        <p className="text-xs uppercase tracking-wider text-ink-faint">Analyse</p>
        <h1 className="text-2xl font-semibold text-ink">Ask</h1>
        <p className="max-w-2xl text-sm text-ink-muted">
          Questions are answered from the tender's own pages, with the passage each
          claim rests on. When the document does not say, it says so.
        </p>
      </header>

      <label className="flex max-w-xl flex-col gap-1 text-sm" htmlFor="tender-select">
        <span className="text-ink-muted">Tender</span>
        <select
          id="tender-select"
          value={selected}
          onChange={(event) => setTenderId(event.target.value)}
          className="rounded border border-rule bg-surface px-3 py-2 text-sm text-ink"
        >
          {ingested.data?.length ? (
            ingested.data.map((row) => (
              <option key={row.tender_id} value={row.tender_id}>
                {row.reference} — {row.title.slice(0, 60)}
              </option>
            ))
          ) : (
            <option value="">No tender ingested yet</option>
          )}
        </select>
      </label>

      <section className="flex flex-col gap-3 rounded border border-rule bg-surface p-5">
        <h2 className="text-sm font-semibold text-ink">Ask a question</h2>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            id="question"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && question.trim() && selected) ask.mutate();
            }}
            placeholder="What is the earnest money deposit, and in what form?"
            className="flex-1 rounded border border-rule bg-canvas px-3 py-2 text-sm text-ink"
          />
          <button
            type="button"
            disabled={!selected || question.trim().length < 3 || ask.isPending}
            onClick={() => ask.mutate()}
            className="rounded bg-accent px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            {ask.isPending ? "Searching…" : "Ask"}
          </button>
        </div>

        {ask.isError && (
          <p className="text-sm text-sev-critical">{(ask.error as Error).message}</p>
        )}

        {ask.data && (
          <div className="flex flex-col gap-2 rounded border border-rule bg-canvas p-4">
            <Answer result={ask.data} />
            <p className="text-xs text-ink-faint">
              {ask.data.elapsed_ms} ms · confidence {ask.data.confidence.toFixed(2)}
            </p>
          </div>
        )}
      </section>

      <section className="flex flex-col gap-3 rounded border border-rule bg-surface p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-sm font-semibold text-ink">The 50 standard questions</h2>
          <div className="flex items-center gap-2">
            <select
              id="faq-category"
              value={category}
              onChange={(event) => setCategory(event.target.value)}
              className="rounded border border-rule bg-canvas px-2 py-1 text-xs text-ink"
            >
              <option value="">Every category</option>
              {["Identification", "Value", "Deadlines", "Eligibility", "Submission", "Evaluation", "Risk"].map(
                (name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ),
              )}
            </select>
            <button
              type="button"
              disabled={!selected || faqs.isPending}
              onClick={() => faqs.mutate()}
              className="rounded border border-accent px-3 py-1 text-xs font-medium text-accent disabled:cursor-not-allowed disabled:opacity-40"
            >
              {faqs.isPending ? "Answering…" : "Run"}
            </button>
          </div>
        </div>

        {faqs.isPending && (
          <p className="text-xs text-ink-muted">
            Each question is retrieved and answered separately; the full set takes a few
            minutes and is cached afterwards.
          </p>
        )}
        {faqs.isError && (
          <p className="text-sm text-sev-critical">{(faqs.error as Error).message}</p>
        )}

        {faqs.data && (
          <>
            <p className="text-xs text-ink-muted">
              {faqs.data.answered} answered · {faqs.data.unanswerable} not stated in this
              tender
            </p>
            <div className="flex flex-col gap-4">
              {categories.map((name) => (
                <div key={name} className="flex flex-col gap-3">
                  <h3 className="text-xs uppercase tracking-wider text-ink-faint">{name}</h3>
                  {faqs.data!.items
                    .filter((item) => item.category === name)
                    .map((item) => (
                      <div
                        key={item.key}
                        className="flex flex-col gap-2 border-l-2 border-rule pl-4"
                      >
                        <p className="text-sm font-medium text-ink">{item.question}</p>
                        <Answer result={item} />
                      </div>
                    ))}
                </div>
              ))}
            </div>
          </>
        )}
      </section>
    </div>
  );
}
