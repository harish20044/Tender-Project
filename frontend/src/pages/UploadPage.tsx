import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { fetchIngested, uploadDocument } from "../lib/api";

export function UploadPage() {
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);

  const [file, setFile] = useState<File | null>(null);
  const [reference, setReference] = useState("");
  const [title, setTitle] = useState("");
  const [dragging, setDragging] = useState(false);

  const ingested = useQuery({ queryKey: ["ingested"], queryFn: fetchIngested });

  const upload = useMutation({
    mutationFn: () => uploadDocument(file as File, reference, title),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["ingested"] });
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
    },
  });

  /** Only PDFs are accepted, so reject anything else before it reaches the API. */
  function accept(candidate: File | undefined) {
    if (!candidate) return;
    if (!candidate.name.toLowerCase().endsWith(".pdf")) {
      upload.reset();
      setFile(null);
      window.alert("Only PDF files can be ingested at the moment.");
      return;
    }
    setFile(candidate);
    if (!title) setTitle(candidate.name.replace(/\.pdf$/i, ""));
  }

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-2">
        <p className="text-xs uppercase tracking-wider text-ink-faint">Intake</p>
        <h1 className="text-2xl font-semibold text-ink">Upload a tender pack</h1>
        <p className="max-w-2xl text-sm text-ink-muted">
          The document is parsed page by page, split into passages that keep their page
          numbers, and embedded so it can be questioned and scored. Nothing is answered
          from outside the document.
        </p>
      </header>

      <section className="flex flex-col gap-4 rounded border border-rule bg-surface p-5">
        <div
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            accept(event.dataTransfer.files[0]);
          }}
          onClick={() => inputRef.current?.click()}
          className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded border-2 border-dashed p-10 text-center transition ${
            dragging ? "border-accent bg-accent/5" : "border-rule"
          }`}
        >
          <p className="text-sm font-medium text-ink">
            {file ? file.name : "Drop a tender PDF here, or click to choose one"}
          </p>
          <p className="text-xs text-ink-faint">
            {file
              ? `${(file.size / 1024 / 1024).toFixed(1)} MB`
              : "PDF only. Scanned packs without a text layer are reported, not silently skipped."}
          </p>
          <input
            id="tender-file"
            ref={inputRef}
            type="file"
            accept="application/pdf,.pdf"
            className="hidden"
            onChange={(event) => accept(event.target.files?.[0])}
          />
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <label className="flex flex-col gap-1 text-sm" htmlFor="tender-reference">
            <span className="text-ink-muted">Tender reference</span>
            <input
              id="tender-reference"
              value={reference}
              onChange={(event) => setReference(event.target.value)}
              placeholder="NHAI/RO-BPL/2026-27/EPC-14"
              className="rounded border border-rule bg-canvas px-3 py-2 font-mono text-xs text-ink"
            />
            <span className="text-xs text-ink-faint">
              Optional. One is generated if you leave this blank.
            </span>
          </label>

          <label className="flex flex-col gap-1 text-sm" htmlFor="tender-title">
            <span className="text-ink-muted">Title</span>
            <input
              id="tender-title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Four-lane bypass with major bridge"
              className="rounded border border-rule bg-canvas px-3 py-2 text-sm text-ink"
            />
          </label>
        </div>

        <div className="flex items-center gap-3">
          <button
            type="button"
            disabled={!file || upload.isPending}
            onClick={() => upload.mutate()}
            className="rounded bg-accent px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            {upload.isPending ? "Ingesting…" : "Ingest document"}
          </button>
          {upload.isPending && (
            <span className="text-xs text-ink-muted">
              Parsing, chunking and embedding. A large pack takes a minute.
            </span>
          )}
        </div>

        {upload.isError && (
          <p className="rounded border border-sev-critical/40 bg-sev-critical/5 px-3 py-2 text-sm text-sev-critical">
            {(upload.error as Error).message}
          </p>
        )}

        {upload.isSuccess && upload.data && (
          <div className="flex flex-col gap-2 rounded border border-rule bg-canvas p-4">
            <p className="text-sm font-medium text-ink">
              Ingested {upload.data.filename}
            </p>
            <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-ink-muted sm:grid-cols-4">
              <div>
                <dt className="text-ink-faint">Pages</dt>
                <dd className="tabular-nums text-ink">{upload.data.pages}</dd>
              </div>
              <div>
                <dt className="text-ink-faint">Passages</dt>
                <dd className="tabular-nums text-ink">{upload.data.chunks}</dd>
              </div>
              <div>
                <dt className="text-ink-faint">Embedded</dt>
                <dd className="tabular-nums text-ink">{upload.data.embedded}</dd>
              </div>
              <div>
                <dt className="text-ink-faint">Reused</dt>
                <dd className="tabular-nums text-ink">{upload.data.reused_embeddings}</dd>
              </div>
            </dl>
            <p className="font-mono text-xs text-ink-faint">
              tender id {upload.data.tender_id}
            </p>
            {upload.data.needs_ocr && (
              <p className="text-xs text-sev-medium">
                Some pages have no text layer and will need OCR before they can be read.
              </p>
            )}
          </div>
        )}
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-sm font-semibold text-ink">Ingested tenders</h2>
        {ingested.isLoading && <p className="text-sm text-ink-muted">Loading…</p>}
        {ingested.data?.length === 0 && (
          <p className="text-sm text-ink-muted">
            Nothing ingested yet. Upload a pack above to ask questions about it.
          </p>
        )}
        {!!ingested.data?.length && (
          <div className="overflow-x-auto rounded border border-rule">
            <table className="w-full min-w-[40rem] border-collapse bg-surface text-sm">
              <thead>
                <tr className="border-b border-rule text-left text-xs uppercase tracking-wider text-ink-faint">
                  <th className="px-4 py-2 font-medium">Reference</th>
                  <th className="px-4 py-2 font-medium">Title</th>
                  <th className="px-4 py-2 text-right font-medium">Docs</th>
                  <th className="px-4 py-2 text-right font-medium">Passages</th>
                  <th className="px-4 py-2 font-medium">Tender id</th>
                </tr>
              </thead>
              <tbody>
                {ingested.data.map((row) => (
                  <tr key={row.tender_id} className="border-b border-rule last:border-0">
                    <td className="px-4 py-2 font-mono text-xs text-ink">{row.reference}</td>
                    <td className="px-4 py-2 text-ink-muted">{row.title}</td>
                    <td className="px-4 py-2 text-right tabular-nums text-ink-muted">
                      {row.documents}
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums text-ink-muted">
                      {row.chunks}
                    </td>
                    <td className="px-4 py-2 font-mono text-xs text-ink-faint">
                      {row.tender_id}
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
