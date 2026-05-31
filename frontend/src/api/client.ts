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
  base_amount: string | null;
  fx_rate: string | null;
  needs_rate: boolean;
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
  opts: { learnVendor?: boolean; learnRule?: boolean } = {},
): Promise<Transaction> {
  return fetchJson<Transaction>(`api/transactions/${id}/categorise`, {
    method: "POST",
    body: JSON.stringify({
      category_id: categoryId,
      learn_vendor: opts.learnVendor ?? false,
      learn_rule: opts.learnRule ?? false,
    }),
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

// --- Backup / restore / demo (spec §26.5; backlog #9, #10, #16) ---

export function loadDemoData(): Promise<{ rows_detected: number; new: number; duplicates: number }> {
  return fetchJson("api/backup/demo", { method: "POST" });
}

// Browser-side file download by clicking a temporary anchor.
function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export async function downloadDatabaseBackup(): Promise<void> {
  const res = await fetch(apiUrl("api/backup/database"));
  if (!res.ok) throw new Error(`Backup failed: ${res.status}`);
  triggerDownload(await res.blob(), "ha-finance-backup.db");
}

export async function restoreDatabase(file: File): Promise<{ status: string }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(apiUrl("api/backup/restore"), { method: "POST", body: form });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `Restore failed: ${res.status}`);
  }
  return res.json();
}

export async function downloadEncryptedBackup(passphrase: string): Promise<void> {
  const form = new FormData();
  form.append("passphrase", passphrase);
  const res = await fetch(apiUrl("api/backup/database/encrypted"), { method: "POST", body: form });
  if (!res.ok) throw new Error(`Encrypted backup failed: ${res.status}`);
  triggerDownload(await res.blob(), "ha-finance-backup.db.enc");
}

export async function restoreEncryptedDatabase(file: File, passphrase: string): Promise<{ status: string }> {
  const form = new FormData();
  form.append("file", file);
  form.append("passphrase", passphrase);
  const res = await fetch(apiUrl("api/backup/restore/encrypted"), { method: "POST", body: form });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `Restore failed: ${res.status}`);
  }
  return res.json();
}

export async function exportConfig(): Promise<void> {
  const res = await fetch(apiUrl("api/backup/config"));
  if (!res.ok) throw new Error(`Export failed: ${res.status}`);
  const data = await res.json();
  triggerDownload(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }), "ha-finance-config.json");
}

export async function importConfig(
  file: File,
): Promise<{ categories_added: number; vendors_added: number; settings_set: number }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(apiUrl("api/backup/config"), { method: "POST", body: form });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `Import failed: ${res.status}`);
  }
  return res.json();
}

// --- Settings + FX (spec §24.2; backlog #29) ---

export interface AppSettings {
  base_currency: string;
  fx_mode: string; // manual | frankfurter
  [key: string]: string;
}

export function getSettings(): Promise<AppSettings> {
  return fetchJson<AppSettings>("api/settings");
}

export function updateSettings(patch: Partial<AppSettings>): Promise<AppSettings & { recompute?: unknown }> {
  return fetchJson("api/settings", { method: "PUT", body: JSON.stringify(patch) });
}

export interface FxRate {
  id: number;
  rate_date: string;
  base: string;
  quote: string;
  rate: string;
  source: string;
}

export function listFxRates(): Promise<FxRate[]> {
  return fetchJson<FxRate[]>("api/fx/rates");
}

export function addFxRate(rate_date: string, quote: string, rate: string): Promise<FxRate> {
  return fetchJson<FxRate>("api/fx/rates", {
    method: "POST",
    body: JSON.stringify({ rate_date, quote, rate }),
  });
}

export function backfillFx(): Promise<{ checked: number; filled: number; still_missing: number }> {
  return fetchJson("api/fx/backfill", { method: "POST" });
}

export function fxMissing(): Promise<{ needs_rate: number }> {
  return fetchJson("api/fx/missing");
}

// --- Rules (spec §24.7) ---

export interface Rule {
  id: number;
  name: string;
  priority: number;
  enabled: boolean;
  condition_type: string;
  condition_value: string;
  action_type: string;
  action_value: string | null;
  created_from: string | null;
}

export const RULE_CONDITION_TYPES = [
  "description_contains",
  "merchant_contains",
  "vendor_equals",
  "account_equals",
  "category_equals",
  "amount_equals",
  "amount_between",
] as const;

export const RULE_ACTION_TYPES = [
  "set_category",
  "set_vendor",
  "set_project",
  "mark_transfer",
  "mark_income",
  "mark_subscription",
  "require_review",
  "block_cloud_ai",
] as const;

export function listRules(): Promise<Rule[]> {
  return fetchJson<Rule[]>("api/rules");
}

export function createRule(data: Record<string, unknown>): Promise<Rule> {
  return fetchJson<Rule>("api/rules", { method: "POST", body: JSON.stringify(data) });
}

export function updateRule(id: number, data: Record<string, unknown>): Promise<Rule> {
  return fetchJson<Rule>(`api/rules/${id}`, { method: "PATCH", body: JSON.stringify(data) });
}

export async function deleteRule(id: number): Promise<void> {
  const res = await fetch(apiUrl(`api/rules/${id}`), { method: "DELETE" });
  if (!res.ok) throw new Error(`Delete failed: ${res.status}`);
}

export interface RuleTestResult {
  match_count: number;
  total: number;
  sample: { id: number; transaction_date: string; description_raw: string; amount: string }[];
}

export function testRule(condition_type: string, condition_value: string): Promise<RuleTestResult> {
  return fetchJson<RuleTestResult>("api/rules/test", {
    method: "POST",
    body: JSON.stringify({ condition_type, condition_value }),
  });
}
