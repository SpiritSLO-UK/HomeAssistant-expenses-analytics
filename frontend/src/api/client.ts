// Lightweight API client.
//
// Under Home Assistant ingress the app is served from a path like
// `/api/hassio_ingress/<token>/`. We capture that base path once at load and
// prefix all API calls with it, so requests resolve correctly both under
// ingress and in local development (where the base is just `/`). The app uses
// a HashRouter, so `window.location.pathname` stays at this base for the whole
// session.

const INGRESS_BASE: string = (() => {
  let path = window.location.pathname;
  if (!path.endsWith("/")) path += "/";
  return path;
})();

export function apiUrl(endpoint: string): string {
  const clean = endpoint.replace(/^\//, "");
  return INGRESS_BASE + clean;
}

export async function fetchJson<T>(endpoint: string, init?: RequestInit): Promise<T> {
  const res = await fetch(apiUrl(endpoint), {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    throw new Error(`API ${endpoint} failed: ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

export interface HealthResponse {
  status: string;
  version: string;
  privacy_mode: string;
  database: string;
}

export function getHealth(): Promise<HealthResponse> {
  return fetchJson<HealthResponse>("api/health");
}

// --- Imports (spec §24.3) ---

export interface ParserInfo {
  parser_id: string;
  institution: string;
}

export interface ImportReport {
  rows_detected: number;
  new: number;
  duplicates: number;
  errors: number;
}

export interface PreviewRow {
  transaction_date: string;
  description_raw: string;
  merchant_raw: string | null;
  amount: string;
  currency: string;
  direction: string;
  category_hint: string | null;
  is_duplicate: boolean;
}

export interface UploadResponse {
  import_id: number;
  detected_parser: string;
  institution: string;
  account_id: number;
  rows_detected: number;
  report: ImportReport;
  preview: PreviewRow[];
  warnings: string[];
}

export interface ConfirmResponse {
  import_id: number;
  status: string;
  report: ImportReport;
}

export function listParsers(): Promise<ParserInfo[]> {
  return fetchJson<ParserInfo[]>("api/imports/parsers");
}

export async function uploadImport(file: File, parserId?: string): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  if (parserId) form.append("parser_id", parserId);
  // No Content-Type header: the browser sets the multipart boundary.
  const res = await fetch(apiUrl("api/imports/upload"), { method: "POST", body: form });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `Upload failed: ${res.status}`);
  }
  return (await res.json()) as UploadResponse;
}

export function confirmImport(importId: number): Promise<ConfirmResponse> {
  return fetchJson<ConfirmResponse>(`api/imports/${importId}/confirm`, { method: "POST" });
}

// --- Transactions (spec §24.4) ---

export interface Transaction {
  id: number;
  account_id: number | null;
  transaction_date: string;
  posted_date: string | null;
  description_raw: string;
  merchant_raw: string | null;
  amount: string;
  currency: string;
  direction: string;
  category_id: number | null;
  project_id: number | null;
  is_split: boolean;
  is_transfer: boolean;
  is_income: boolean;
  is_duplicate: boolean;
  needs_review: boolean;
}

export interface TransactionListResponse {
  items: Transaction[];
  total: number;
  limit: number;
  offset: number;
}

export interface TransactionFilters {
  search?: string;
  date_from?: string;
  date_to?: string;
  amount_min?: string;
  amount_max?: string;
  needs_review?: boolean;
  limit?: number;
  offset?: number;
}

export function listTransactions(filters: TransactionFilters = {}): Promise<TransactionListResponse> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== "" && value !== false) {
      params.append(key, String(value));
    }
  }
  const qs = params.toString();
  return fetchJson<TransactionListResponse>(`api/transactions${qs ? `?${qs}` : ""}`);
}
