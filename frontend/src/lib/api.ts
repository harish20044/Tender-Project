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
