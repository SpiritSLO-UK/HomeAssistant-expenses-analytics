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

export function categoriseTransaction(
  id: number,
  categoryId: number | null,
  learnVendor = false,
): Promise<Transaction> {
  return fetchJson<Transaction>(`api/transactions/${id}/categorise`, {
    method: "POST",
    body: JSON.stringify({ category_id: categoryId, learn_vendor: learnVendor }),
  });
}

export function recategorise(onlyUncategorised = true): Promise<{ recategorised: number }> {
  return fetchJson(`api/transactions/recategorise`, {
    method: "POST",
    body: JSON.stringify({ only_uncategorised: onlyUncategorised }),
  });
}

// --- Categories (spec §24.5) ---

export interface Category {
  id: number;
  parent_id: number | null;
  library_id: string | null;
  name: string;
  icon: string | null;
  colour: string | null;
  is_system: boolean;
  is_active: boolean;
  is_budgetable: boolean;
  privacy_sensitivity: string;
}

export function listCategories(): Promise<Category[]> {
  return fetchJson<Category[]>("api/categories");
}

export function createCategory(data: Partial<Category>): Promise<Category> {
  return fetchJson<Category>("api/categories", { method: "POST", body: JSON.stringify(data) });
}

export function updateCategory(id: number, data: Partial<Category>): Promise<Category> {
  return fetchJson<Category>(`api/categories/${id}`, { method: "PATCH", body: JSON.stringify(data) });
}

export async function deleteCategory(id: number): Promise<void> {
  const res = await fetch(apiUrl(`api/categories/${id}`), { method: "DELETE" });
  if (!res.ok) throw new Error(`Delete failed: ${res.status}`);
}

// --- Vendors (spec §24.6) ---

export interface VendorAlias {
  id: number;
  alias: string;
  match_type: string;
  source: string | null;
}

export interface Vendor {
  id: number;
  canonical_name: string;
  display_name: string | null;
  default_category_id: number | null;
  service_type: string | null;
  website: string | null;
  notes: string | null;
  last_seen_at: string | null;
  aliases: VendorAlias[];
  transaction_count: number;
  total_amount: string;
}

export function listVendors(): Promise<Vendor[]> {
  return fetchJson<Vendor[]>("api/vendors");
}

export function createVendor(data: Record<string, unknown>): Promise<Vendor> {
  return fetchJson<Vendor>("api/vendors", { method: "POST", body: JSON.stringify(data) });
}

export function addVendorAlias(id: number, alias: string, matchType = "contains"): Promise<VendorAlias> {
  return fetchJson<VendorAlias>(`api/vendors/${id}/aliases`, {
    method: "POST",
    body: JSON.stringify({ alias, match_type: matchType }),
  });
}

export function setVendorDefaultCategory(id: number, categoryId: number | null): Promise<Vendor> {
  return fetchJson<Vendor>(`api/vendors/${id}/set-default-category`, {
    method: "POST",
    body: JSON.stringify({ category_id: categoryId }),
  });
}

export async function deleteVendor(id: number): Promise<void> {
  const res = await fetch(apiUrl(`api/vendors/${id}`), { method: "DELETE" });
  if (!res.ok) throw new Error(`Delete failed: ${res.status}`);
}

// --- Dashboard (spec §24.12) ---

export interface DashboardSummary {
  month: string;
  currency: string;
  spend_this_month: string;
  income_this_month: string;
  net_this_month: string;
  total_transactions: number;
  uncategorised_transactions: number;
  review_items: number;
}

export interface CategoryBreakdownItem {
  category_id: number | null;
  name: string;
  colour: string | null;
  total: string;
  count: number;
}

export interface VendorBreakdownItem {
  vendor_id: number | null;
  name: string;
  total: string;
  count: number;
}

export function getSummary(month?: string): Promise<DashboardSummary> {
  return fetchJson<DashboardSummary>(`api/dashboard/summary${month ? `?month=${month}` : ""}`);
}

export function getCategoryBreakdown(month?: string): Promise<CategoryBreakdownItem[]> {
  return fetchJson<CategoryBreakdownItem[]>(`api/dashboard/categories${month ? `?month=${month}` : ""}`);
}

export function getVendorBreakdown(month?: string): Promise<VendorBreakdownItem[]> {
  return fetchJson<VendorBreakdownItem[]>(`api/dashboard/vendors${month ? `?month=${month}` : ""}`);
}
