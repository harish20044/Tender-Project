/** Client for the tender API. */

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

export interface Tender {
  reference: string;
  tender_id: string | null;
  title: string;
  organisation: string;
  published_at: string | null;
  closing_at: string | null;
  opening_at: string | null;
  corrigendum_count: number;
  work_category: string | null;
  is_construction: boolean;
  source_listing: string;
  scraped_at: string;
}

export interface TenderList {
  tenders: Tender[];
  count: number;
  total_before_filter: number;
  /** When the portal was last read. Shown so nobody mistakes stale data for live. */
  scraped_at: string | null;
  source: string | null;
}

export interface CategoryCount {
  category: string;
  count: number;
}

async function request<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`);

  if (!response.ok) {
    throw new Error(
      `The API returned ${response.status}. Check that it is running on port 8001.`,
    );
  }

  return (await response.json()) as T;
}

export function fetchTenders(params: {
  constructionOnly?: boolean;
  category?: string | null;
  sort?: "closing" | "published";
}): Promise<TenderList> {
  const query = new URLSearchParams();

  if (params.constructionOnly) query.set("construction_only", "true");
  if (params.category) query.set("category", params.category);
  query.set("sort", params.sort ?? "closing");

  return request<TenderList>(`/api/tenders?${query.toString()}`);
}

export function fetchCategories(): Promise<CategoryCount[]> {
  return request<CategoryCount[]>("/api/tenders/categories");
}

// --- Documents, questions and decisions ------------------------------------

export interface UploadResult {
  tender_id: string;
  document_id: string;
  filename: string;
  reference: string;
  pages: number;
  chunks: number;
  embedded: number;
  reused_embeddings: number;
  needs_ocr: boolean;
}

export interface Citation {
  marker: number;
  chunk_id: string;
  filename: string;
  page_from: number;
  page_to: number;
  citation: string;
}

export interface AnswerResult {
  question: string;
  answer: string;
  is_answerable: boolean;
  confidence: number;
  citations: Citation[];
  elapsed_ms: number;
}

export interface FaqItem {
  key: string;
  question: string;
  category: string;
  answer: string;
  is_answerable: boolean;
  confidence: number;
  citations: Citation[];
}

export interface FaqRun {
  tender_id: string;
  answered: number;
  unanswerable: number;
  items: FaqItem[];
}

export interface IngestedTender {
  tender_id: string;
  reference: string;
  title: string;
  documents: number;
  chunks: number;
  pages: number;
}

export interface Fact {
  key: string;
  value: unknown;
  unit: string | null;
  confidence: number;
  page: number | null;
  quote: string | null;
}

export interface Gate {
  key: string;
  label: string;
  outcome: "pass" | "fail" | "unknown";
  mandatory: boolean;
  detail: string;
  tender_value: unknown;
  threshold: unknown;
  fact_key: string | null;
  page: number | null;
}

export interface Risk {
  category: string;
  severity: "low" | "medium" | "high" | "critical";
  summary: string;
  detail: string | null;
  page: number | null;
}

export interface DecisionResult {
  tender_id: string;
  recommendation: "bid" | "no_bid" | "review";
  score: number;
  deciding_gate: string | null;
  rationale: string | null;
  ruleset_version: string;
  profile: string;
  gates: Gate[];
  risks: Risk[];
  counterfactuals: { gate: string; label: string; shortfall: number; statement: string }[];
}

async function post<T>(path: string, body?: FormData): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, { method: "POST", body });

  if (!response.ok) {
    let detail = `The API returned ${response.status}.`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) detail = payload.detail;
    } catch {
      // Body was not JSON; the status line is all we have to report.
    }
    throw new Error(detail);
  }

  return (await response.json()) as T;
}

export function uploadDocument(
  file: File,
  reference: string,
  title: string,
): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  if (reference.trim()) form.append("reference", reference.trim());
  if (title.trim()) form.append("title", title.trim());
  return post<UploadResult>("/api/documents/upload", form);
}

export function fetchIngested(): Promise<IngestedTender[]> {
  return request<IngestedTender[]>("/api/documents");
}

export function askQuestion(tenderId: string, question: string): Promise<AnswerResult> {
  const query = new URLSearchParams({ question });
  return post<AnswerResult>(`/api/documents/${tenderId}/ask?${query.toString()}`);
}

export function runFaqs(tenderId: string, category?: string): Promise<FaqRun> {
  const query = new URLSearchParams();
  if (category) query.set("category", category);
  return post<FaqRun>(`/api/documents/${tenderId}/faqs?${query.toString()}`);
}

export function extractFacts(tenderId: string): Promise<{ extracted: number; facts: Fact[] }> {
  return post<{ extracted: number; facts: Fact[] }>(`/api/decisions/${tenderId}/extract`);
}

export function fetchFacts(tenderId: string): Promise<{ extracted: number; facts: Fact[] }> {
  return request<{ extracted: number; facts: Fact[] }>(`/api/decisions/${tenderId}/facts`);
}

export function computeDecision(tenderId: string): Promise<DecisionResult> {
  return post<DecisionResult>(`/api/decisions/${tenderId}`);
}

export function fetchDecision(tenderId: string): Promise<DecisionResult | null> {
  return request<DecisionResult | null>(`/api/decisions/${tenderId}`);
}

export interface SimilarMatch {
  reference: string | null;
  title: string;
  authority: string | null;
  category: string | null;
  published: string | null;
  estimated_value: number | null;
  scope_similarity: number;
  reissue_likelihood: number;
  same_authority: boolean;
  same_category: boolean;
  is_probable_reissue: boolean;
  why: string;
}

export interface SimilarResult {
  tender_id: string;
  corpus_size: number;
  matches: SimilarMatch[];
}

export function fetchSimilar(tenderId: string, limit = 10): Promise<SimilarResult> {
  return request<SimilarResult>(`/api/similarity/${tenderId}?limit=${limit}`);
}

export function seedCorpus(): Promise<{
  created: number;
  updated: number;
  embedded: number;
  total: number;
}> {
  return post<{ created: number; updated: number; embedded: number; total: number }>(
    "/api/similarity/seed",
  );
}
