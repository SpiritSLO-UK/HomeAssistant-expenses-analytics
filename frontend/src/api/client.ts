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

// --- MFA session token (backlog #124) ---
// Issued by the backend after a TOTP challenge and sent on every request as the
// X-HAFI-Session header. Stored per-browser in localStorage.
const SESSION_KEY = "hafi_session";

export function getSessionToken(): string | null {
  try {
    return window.localStorage.getItem(SESSION_KEY);
  } catch {
    return null;
  }
}

export function setSessionToken(token: string | null): void {
  try {
    if (token) window.localStorage.setItem(SESSION_KEY, token);
    else window.localStorage.removeItem(SESSION_KEY);
  } catch {
    /* localStorage unavailable (private mode) — requests just won't carry it */
  }
}

/** Error thrown by fetchJson on a non-2xx response, carrying the parsed body. */
export class ApiError extends Error {
  status: number;
  body: Record<string, unknown> | null;
  constructor(status: number, body: Record<string, unknown> | null, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

export function isStepUpError(e: unknown): boolean {
  return e instanceof ApiError && e.status === 403 && e.body?.detail === "step_up_required";
}

export async function fetchJson<T>(endpoint: string, init?: RequestInit): Promise<T> {
  const token = getSessionToken();
  const res = await fetch(apiUrl(endpoint), {
    headers: {
      "Content-Type": "application/json",
      ...(token ? { "X-HAFI-Session": token } : {}),
      ...(init?.headers ?? {}),
    },
    ...init,
  });
  if (!res.ok) {
    let body: Record<string, unknown> | null = null;
    try {
      body = await res.json();
    } catch {
      /* non-JSON error body */
    }
    const detail = typeof body?.detail === "string" ? body.detail : `${res.status} ${res.statusText}`;
    throw new ApiError(res.status, body, `API ${endpoint} failed: ${detail}`);
  }
  if (res.status === 204) return undefined as T; // no content (e.g. DELETE)
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
  tags?: Tag[];
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
  project_id?: number;
  tag_id?: number;
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

export async function updateTransaction(id: number, patch: Record<string, unknown>): Promise<Transaction> {
  const res = await fetch(apiUrl(`api/transactions/${id}`), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `Update failed: ${res.status}`);
  }
  return res.json();
}

export function setTransactionTags(id: number, tags: string[]): Promise<Transaction> {
  return fetchJson<Transaction>(`api/transactions/${id}/tags`, {
    method: "POST",
    body: JSON.stringify({ tags }),
  });
}

// --- Splits (spec §17) ---

export interface Split {
  id: number;
  amount: string;
  category_id: number | null;
  project_id: number | null;
  description: string | null;
  notes: string | null;
}

export interface SplitsResponse {
  transaction_id: number;
  is_split: boolean;
  currency: string;
  total: string;
  splits: Split[];
}

export interface SplitInput {
  amount: string;
  category_id?: number | null;
  project_id?: number | null;
  description?: string | null;
}

export function getSplits(id: number): Promise<SplitsResponse> {
  return fetchJson<SplitsResponse>(`api/transactions/${id}/splits`);
}

export async function setSplits(id: number, splits: SplitInput[]): Promise<SplitsResponse> {
  const res = await fetch(apiUrl(`api/transactions/${id}/split`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ splits }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `Split failed: ${res.status}`);
  }
  return res.json();
}

export function clearSplits(id: number): Promise<SplitsResponse> {
  return fetchJson<SplitsResponse>(`api/transactions/${id}/split`, { method: "DELETE" });
}

// --- Budgets (spec §24.9, §19) ---

export interface Budget {
  id: number;
  name: string;
  amount: string;
  currency: string;
  period: string;
  category_id: number | null;
  project_id: number | null;
  start_date: string | null;
  end_date: string | null;
  rollover_enabled: boolean;
  alert_threshold_percent: number | null;
}

export interface BudgetSummaryItem {
  budget_id: number;
  name: string;
  category_id: number | null;
  project_id: number | null;
  period: string;
  currency: string;
  amount: string;
  spent: string;
  remaining: string;
  percent: number;
  status: "ok" | "warn" | "over";
  alert_threshold_percent: number | null;
  period_start: string;
  period_end: string;
}

export const BUDGET_PERIODS = ["weekly", "monthly", "quarterly", "yearly", "custom"] as const;

export function listBudgets(): Promise<Budget[]> {
  return fetchJson<Budget[]>("api/budgets");
}

export function getBudgetSummary(month?: string): Promise<BudgetSummaryItem[]> {
  return fetchJson<BudgetSummaryItem[]>(`api/budgets/summary${month ? `?month=${month}` : ""}`);
}

export async function createBudget(data: Record<string, unknown>): Promise<Budget> {
  const res = await fetch(apiUrl("api/budgets"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `Create budget failed: ${res.status}`);
  }
  return res.json();
}

export function updateBudget(id: number, data: Record<string, unknown>): Promise<Budget> {
  return fetchJson<Budget>(`api/budgets/${id}`, { method: "PATCH", body: JSON.stringify(data) });
}

export async function deleteBudget(id: number): Promise<void> {
  const res = await fetch(apiUrl(`api/budgets/${id}`), { method: "DELETE" });
  if (!res.ok) throw new Error(`Delete failed: ${res.status}`);
}

// --- MQTT / Home Assistant sensors (spec §27) ---

export interface MqttStatus {
  enabled: boolean;
  available: boolean;
  host: string;
  port: number;
  discovery_prefix: string;
  base_topic: string;
  sensor_count?: number;
}

export function getMqttStatus(): Promise<MqttStatus> {
  return fetchJson<MqttStatus>("api/mqtt/status");
}

export async function publishMqtt(): Promise<{ enabled: boolean; published: number; sensors?: number }> {
  const res = await fetch(apiUrl("api/mqtt/publish"), { method: "POST" });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `Publish failed: ${res.status}`);
  }
  return res.json();
}

// --- Projects (spec §24.8, §18) ---

export interface Project {
  id: number;
  name: string;
  description: string | null;
  status: string;
  budget_amount: string | null;
  start_date: string | null;
  end_date: string | null;
}

export interface BreakdownItem {
  id: number | null;
  name: string;
  total: string;
}

export interface ProjectTotal {
  project_id: number;
  name: string;
  status: string;
  currency: string;
  spent: string;
  budget: string | null;
  remaining: string | null;
  percent: number | null;
}

export interface ProjectSummary extends ProjectTotal {
  transaction_count: number;
  first_transaction: string | null;
  last_transaction: string | null;
  by_category: BreakdownItem[];
  by_vendor: BreakdownItem[];
}

export const PROJECT_STATUSES = ["planned", "active", "paused", "complete", "archived"] as const;

export function listProjects(): Promise<Project[]> {
  return fetchJson<Project[]>("api/projects");
}

export function getProjectSummary(id: number): Promise<ProjectSummary> {
  return fetchJson<ProjectSummary>(`api/projects/${id}/summary`);
}

export async function createProject(data: Record<string, unknown>): Promise<Project> {
  const res = await fetch(apiUrl("api/projects"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `Create project failed: ${res.status}`);
  }
  return res.json();
}

export function updateProject(id: number, data: Record<string, unknown>): Promise<Project> {
  return fetchJson<Project>(`api/projects/${id}`, { method: "PATCH", body: JSON.stringify(data) });
}

export async function deleteProject(id: number): Promise<void> {
  const res = await fetch(apiUrl(`api/projects/${id}`), { method: "DELETE" });
  if (!res.ok) throw new Error(`Delete failed: ${res.status}`);
}

export function getDashboardProjects(): Promise<ProjectTotal[]> {
  return fetchJson<ProjectTotal[]>("api/dashboard/projects");
}

// --- Tags (spec §18.3) ---

export interface Tag {
  id: number;
  name: string;
  colour: string | null;
}

export function listTags(): Promise<Tag[]> {
  return fetchJson<Tag[]>("api/tags");
}

// --- Subscriptions / recurring payments (spec §20) ---

export interface Subscription {
  id: number;
  vendor_id: number | null;
  category_id: number | null;
  name: string;
  amount: string;
  currency: string;
  frequency: string;
  monthly_amount: string;
  interval_days: number;
  next_expected_date: string | null;
  last_seen_date: string | null;
  confidence_score: number | null;
  occurrences: number;
  status: string;
}

export interface DashboardSubscriptions {
  currency: string;
  monthly_total: string;
  count: number;
  subscriptions: Subscription[];
}

export const SUBSCRIPTION_STATUSES = ["active", "possible", "cancelled", "ignored"] as const;

export function listSubscriptions(): Promise<Subscription[]> {
  return fetchJson<Subscription[]>("api/subscriptions");
}

export function detectSubscriptions(): Promise<{ created: number; updated: number; total: number }> {
  return fetchJson("api/subscriptions/detect", { method: "POST" });
}

export function updateSubscription(id: number, patch: Record<string, unknown>): Promise<Subscription> {
  return fetchJson<Subscription>(`api/subscriptions/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
}

export async function deleteSubscription(id: number): Promise<void> {
  const res = await fetch(apiUrl(`api/subscriptions/${id}`), { method: "DELETE" });
  if (!res.ok) throw new Error(`Delete failed: ${res.status}`);
}

export function getDashboardSubscriptions(): Promise<DashboardSubscriptions> {
  return fetchJson<DashboardSubscriptions>("api/dashboard/subscriptions");
}

export interface SubscriptionAlert extends Subscription {
  days_until: number | null;
  days_overdue: number | null;
  expected_date: string | null;
}

export interface SubscriptionAlerts {
  currency: string;
  ref: string;
  within_days: number;
  upcoming: SubscriptionAlert[];
  overdue: SubscriptionAlert[];
}

export function getSubscriptionAlerts(withinDays = 7): Promise<SubscriptionAlerts> {
  return fetchJson<SubscriptionAlerts>(`api/subscriptions/alerts?within_days=${withinDays}`);
}

// --- Receipts + OCR (spec §21) ---

export interface ReceiptMatch {
  transaction_id: number;
  match_score: number | null;
  match_status: string;
  matched_by: string | null;
}

export interface Receipt {
  id: number;
  source_filename: string | null;
  receipt_date: string | null;
  merchant_raw: string | null;
  total_amount: string | null;
  vat_amount: string | null;
  currency: string | null;
  ocr_status: string;
  ocr_confidence: number | null;
  needs_review: boolean;
  matches: ReceiptMatch[];
}

export interface MatchCandidate {
  transaction_id: number;
  score: number;
  breakdown: Record<string, number>;
  transaction_date: string;
  amount: string;
  description: string;
}

export interface MatchResult {
  status: string;
  best_score: number;
  candidates: MatchCandidate[];
}

export interface OcrStatus {
  available: boolean;
  image_ocr: boolean;
  pdf_text: boolean;
  image_formats: string[];
}

export function getOcrStatus(): Promise<OcrStatus> {
  return fetchJson<OcrStatus>("api/receipts/status");
}

export function listReceipts(): Promise<Receipt[]> {
  return fetchJson<Receipt[]>("api/receipts");
}

export async function uploadReceipt(file: File): Promise<Receipt> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(apiUrl("api/receipts/upload"), { method: "POST", body: form });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `Upload failed: ${res.status}`);
  }
  return res.json();
}

export function updateReceipt(id: number, fields: Record<string, unknown>): Promise<Receipt> {
  return fetchJson<Receipt>(`api/receipts/${id}`, { method: "PATCH", body: JSON.stringify(fields) });
}

export function matchReceipt(id: number): Promise<MatchResult> {
  return fetchJson<MatchResult>(`api/receipts/${id}/match`, { method: "POST" });
}

export function confirmReceiptMatch(id: number, transactionId: number): Promise<Receipt> {
  return fetchJson<Receipt>(`api/receipts/${id}/confirm-match`, {
    method: "POST",
    body: JSON.stringify({ transaction_id: transactionId }),
  });
}

export async function deleteReceipt(id: number): Promise<void> {
  const res = await fetch(apiUrl(`api/receipts/${id}`), { method: "DELETE" });
  if (!res.ok) throw new Error(`Delete failed: ${res.status}`);
}

// --- Review queue (spec §23) ---

export interface ReviewItem {
  id: number;
  item_type: string;
  item_id: number | null;
  reason: string;
  severity: string;
  status: string;
  suggested_action: string | null;
  created_at: string;
  resolved_at: string | null;
}

export function listReviewItems(status = "open"): Promise<ReviewItem[]> {
  return fetchJson<ReviewItem[]>(`api/review?status=${status}`);
}

export function setReviewStatus(id: number, status: string): Promise<ReviewItem> {
  return fetchJson<ReviewItem>(`api/review/${id}`, { method: "PATCH", body: JSON.stringify({ status }) });
}

export function getReviewCount(): Promise<{ open: number }> {
  return fetchJson<{ open: number }>("api/review/count");
}

// --- AI gateway (spec §22) ---

export interface AIStatus {
  privacy_mode: string;
  enabled: boolean;
  is_cloud: boolean;
  provider: string | null;
  base_url: string | null;
  model: string | null;
  configured: boolean;
  has_api_key: boolean;
}

export interface ClassifyResult {
  status: string; // ok | approval_required
  ai_request_id: number;
  category_id: number | null;
  category_name: string | null;
  confidence: number | null;
  rationale: string | null;
  payload?: Record<string, unknown> | null;
}

export const PRIVACY_MODES = ["strict_local", "local_llm", "cloud_manual", "cloud_auto", "no_ai"] as const;

export function getAiStatus(): Promise<AIStatus> {
  return fetchJson<AIStatus>("api/ai/status");
}

export interface AIRequestRow {
  id: number;
  provider: string;
  model: string | null;
  task_type: string;
  privacy_mode: string;
  approval_status: string;
  status: string;
  confidence_score: number | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
}

async function aiPost(path: string): Promise<ClassifyResult> {
  const res = await fetch(apiUrl(path), { method: "POST" });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `AI request failed: ${res.status}`);
  }
  return res.json();
}

export function classifyWithAi(transactionId: number): Promise<ClassifyResult> {
  return aiPost(`api/ai/classify/${transactionId}`);
}

// Approve a pending cloud request (cloud_manual): sends it and returns the suggestion.
export function approveAiRequest(requestId: number): Promise<ClassifyResult> {
  return aiPost(`api/ai/requests/${requestId}/approve`);
}

export async function rejectAiRequest(requestId: number): Promise<void> {
  const res = await fetch(apiUrl(`api/ai/requests/${requestId}/reject`), { method: "POST" });
  if (!res.ok) throw new Error(`Reject failed: ${res.status}`);
}

export function listAiRequests(): Promise<AIRequestRow[]> {
  return fetchJson<AIRequestRow[]>("api/ai/requests");
}

export interface BatchSuggestion {
  transaction_id: number;
  description: string;
  amount: string;
  category_id: number;
  category_name: string;
  confidence: number | null;
  rationale: string | null;
}

export interface BatchResult {
  considered: number;
  count: number;
  suggestions: BatchSuggestion[];
}

export async function classifyBatch(limit = 25): Promise<BatchResult> {
  const res = await fetch(apiUrl(`api/ai/classify-batch?limit=${limit}`), { method: "POST" });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `Batch AI failed: ${res.status}`);
  }
  return res.json();
}

export function applyAiCategories(
  items: { transaction_id: number; category_id: number }[],
): Promise<{ applied: number }> {
  return fetchJson("api/ai/apply", { method: "POST", body: JSON.stringify({ items }) });
}

// --- Cloud batch AI (backlog #154) ---
// Two stages: prepare (preview the redacted payloads that would be sent, nothing
// leaves the device) → send (approve the whole list at once, then review the
// returned suggestions and apply with applyAiCategories).

export interface CloudBatchItem {
  ai_request_id: number;
  transaction_id: number;
  description: string; // redacted — exactly what would be sent
  amount: string;
  currency: string;
  payload: Record<string, unknown>;
}

export interface CloudBatchPreview {
  considered: number;
  count: number;
  items: CloudBatchItem[];
}

export interface CloudBatchSendResult {
  count: number;
  suggestions: BatchSuggestion[];
  failed: number[];
  rejected: number;
}

export function cloudBatchPrepare(limit = 25): Promise<CloudBatchPreview> {
  return fetchJson<CloudBatchPreview>(`api/ai/cloud-batch/prepare?limit=${limit}`, { method: "POST" });
}

export function cloudBatchSend(
  approveIds: number[],
  rejectIds: number[] = [],
): Promise<CloudBatchSendResult> {
  return fetchJson<CloudBatchSendResult>("api/ai/cloud-batch/send", {
    method: "POST",
    body: JSON.stringify({ approve_ids: approveIds, reject_ids: rejectIds }),
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

// --- Trends & outliers (backlog #146, #150) ---

export interface MonthlyPoint {
  month: string; // YYYY-MM
  spend: string;
  income: string;
  net: string;
}

export interface TrendMetric {
  current: string;
  previous: string;
  delta: string;
  pct: number | null;
  direction: "up" | "down" | "flat";
}

export interface MonthlySeries {
  currency: string;
  months: MonthlyPoint[];
  trend: Record<string, TrendMetric>;
}

export interface OutlierItem {
  type: "large_charge" | "category_spike" | "new_merchant" | "budget";
  severity: "warn" | "info";
  title: string;
  detail: string;
  amount: string | null;
  transaction_id: number | null;
  category_id: number | null;
  budget_id: number | null;
}

export interface OutliersResponse {
  month: string;
  currency: string;
  items: OutlierItem[];
}

export function getMonthlySeries(months = 6, month?: string): Promise<MonthlySeries> {
  const q = new URLSearchParams({ months: String(months) });
  if (month) q.set("month", month);
  return fetchJson<MonthlySeries>(`api/dashboard/monthly?${q.toString()}`);
}

export function getOutliers(month?: string): Promise<OutliersResponse> {
  return fetchJson<OutliersResponse>(`api/dashboard/outliers${month ? `?month=${month}` : ""}`);
}

// --- Savings (spec §12.4; backlog #96, #91) ---

export interface SavingsAccount {
  id: number;
  name: string;
  institution: string | null;
  currency: string;
  latest_balance: string | null;
  balance_count: number;
}

export interface SavingsBalance {
  id: number;
  account_id: number;
  as_of_date: string;
  balance: string;
  currency: string;
  note: string | null;
}

export interface SavingsGoal {
  id: number;
  name: string;
  target_amount: string;
  target_date: string | null;
  account_id: number | null;
  current_amount: string;
  current: string;
  remaining: string;
  percent: number;
  currency: string;
  status: string;
}

export interface SavingsSummary {
  currency: string;
  total_savings: string;
  accounts: SavingsAccount[];
  goals: SavingsGoal[];
}

export function getSavingsSummary(): Promise<SavingsSummary> {
  return fetchJson<SavingsSummary>("api/savings/summary");
}

export function createSavingsAccount(data: {
  name: string;
  institution?: string;
  currency?: string;
}): Promise<SavingsAccount> {
  return fetchJson("api/savings/accounts", { method: "POST", body: JSON.stringify(data) });
}

export function getBalanceHistory(accountId: number): Promise<SavingsBalance[]> {
  return fetchJson<SavingsBalance[]>(`api/savings/accounts/${accountId}/balances`);
}

export function recordBalance(
  accountId: number,
  data: { as_of_date: string; balance: string; note?: string },
): Promise<SavingsBalance> {
  return fetchJson(`api/savings/accounts/${accountId}/balances`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function createSavingsGoal(data: Record<string, unknown>): Promise<SavingsGoal> {
  return fetchJson("api/savings/goals", { method: "POST", body: JSON.stringify(data) });
}

export function updateSavingsGoal(id: number, data: Record<string, unknown>): Promise<SavingsGoal> {
  return fetchJson(`api/savings/goals/${id}`, { method: "PATCH", body: JSON.stringify(data) });
}

export function deleteSavingsGoal(id: number): Promise<void> {
  return fetchJson(`api/savings/goals/${id}`, { method: "DELETE" });
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

// --- Security / at-rest encryption (backlog #15b) ---

export interface FailedUnlockSummary {
  recent: number;
  total_stored: number;
  last_attempt_at: string | null;
}

export interface SecurityStatus {
  encryption_available: boolean;
  encryption_enabled: boolean;
  unlock_mode: string | null;
  locked: boolean;
  failed_unlocks?: FailedUnlockSummary;
}

export function getSecurityStatus(): Promise<SecurityStatus> {
  return fetchJson<SecurityStatus>("api/security/status");
}

// --- Security health panel (backlog #128/#130) ---

export interface SecurityCheck {
  id: string;
  title: string;
  severity: "ok" | "info" | "warn";
  recommendation: string;
  actionable: boolean;
  dismissed: boolean;
  snoozed_until: string | null;
  active: boolean;
}

export interface SecurityHealth {
  checks: SecurityCheck[];
  active_count: number;
  failed_unlocks: FailedUnlockSummary;
}

export function getSecurityHealth(): Promise<SecurityHealth> {
  return fetchJson<SecurityHealth>("api/security/health");
}

export function dismissSecurityCheck(
  check_id: string,
  opts: { snooze_days?: number; clear?: boolean } = {},
): Promise<{ check_id: string; dismissed: boolean; snoozed_until: string | null }> {
  return fetchJson("api/security/health/dismiss", {
    method: "POST",
    body: JSON.stringify({ check_id, ...opts }),
  });
}

export function unlockDatabase(passphrase: string): Promise<{ status: string }> {
  return fetchJson("api/security/unlock", { method: "POST", body: JSON.stringify({ passphrase }) });
}

export function enableEncryption(passphrase: string, unlock_mode: string): Promise<{ status: string }> {
  return fetchJson("api/security/enable", {
    method: "POST",
    body: JSON.stringify({ passphrase, unlock_mode }),
  });
}

export function disableEncryption(passphrase: string): Promise<{ status: string }> {
  return fetchJson("api/security/disable", { method: "POST", body: JSON.stringify({ passphrase }) });
}

// --- Users & access control (spec §6, §28; backlog #82, #126) ---

export interface Me {
  id: number;
  display_name: string;
  role: string;
  status: string;
  is_admin: boolean;
  can_write: boolean;
  mfa_enabled: boolean;
  mfa_required: boolean;
}

export interface User {
  id: number;
  display_name: string;
  email: string | null;
  role: string;
  status: string;
  is_active: boolean;
  external_id: string | null;
  last_seen_at: string | null;
  created_at: string;
}

export function getMe(): Promise<Me> {
  return fetchJson<Me>("api/users/me");
}

export function listUsers(): Promise<User[]> {
  return fetchJson<User[]>("api/users");
}

export function updateUser(
  id: number,
  patch: { role?: string; status?: string; display_name?: string; email?: string },
): Promise<User> {
  return fetchJson<User>(`api/users/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
}

export function approveUser(id: number): Promise<User> {
  return fetchJson<User>(`api/users/${id}/approve`, { method: "POST" });
}

export function deleteUser(id: number): Promise<{ status: string; id: number }> {
  return fetchJson(`api/users/${id}`, { method: "DELETE" });
}

// --- MFA (TOTP, backlog #124) ---

export interface MfaSetup {
  secret: string;
  otpauth_uri: string;
}

export function mfaSetup(): Promise<MfaSetup> {
  return fetchJson<MfaSetup>("api/auth/mfa/setup", { method: "POST" });
}

export function mfaEnable(code: string): Promise<{ status: string }> {
  return fetchJson("api/auth/mfa/enable", { method: "POST", body: JSON.stringify({ code }) });
}

export function mfaDisable(code: string): Promise<{ status: string }> {
  return fetchJson("api/auth/mfa/disable", { method: "POST", body: JSON.stringify({ code }) });
}

export async function mfaVerify(code: string): Promise<{ token: string }> {
  const res = await fetchJson<{ token: string; expires_in_seconds: number }>(
    "api/auth/mfa/verify",
    { method: "POST", body: JSON.stringify({ code }) },
  );
  setSessionToken(res.token);
  return res;
}

export function mfaStepUp(code: string): Promise<{ status: string }> {
  return fetchJson("api/auth/mfa/step-up", { method: "POST", body: JSON.stringify({ code }) });
}

// --- Activity log / audit viewer (owner-only, backlog #92) ---

export interface AuditLogRow {
  id: number;
  created_at: string;
  actor: string | null;
  action: string;
  entity_type: string | null;
  entity_id: number | null;
  details: Record<string, unknown> | null;
}

export function listActivityLog(opts?: { limit?: number; action?: string }): Promise<AuditLogRow[]> {
  const params = new URLSearchParams();
  if (opts?.limit) params.set("limit", String(opts.limit));
  if (opts?.action) params.set("action", opts.action);
  const qs = params.toString();
  return fetchJson<AuditLogRow[]>(`api/logs/activity${qs ? `?${qs}` : ""}`);
}

export function listAuditActions(): Promise<string[]> {
  return fetchJson<string[]>("api/logs/actions");
}

// --- CSV export (backlog #132) ---
// Fetched (not a plain <a download>) so the MFA session header travels with the
// request; the response is turned into a blob and downloaded client-side.

async function downloadCsv(endpoint: string, fallbackName: string): Promise<void> {
  const token = getSessionToken();
  const res = await fetch(apiUrl(endpoint), {
    headers: { ...(token ? { "X-HAFI-Session": token } : {}) },
  });
  if (!res.ok) {
    let body: Record<string, unknown> | null = null;
    try {
      body = await res.json();
    } catch {
      /* non-JSON error body */
    }
    const detail = typeof body?.detail === "string" ? body.detail : `${res.status} ${res.statusText}`;
    throw new ApiError(res.status, body, `Export failed: ${detail}`);
  }
  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^"]+)"?/.exec(disposition);
  const name = match?.[1] ?? fallbackName;
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function exportParams(filters: Record<string, unknown>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== null && value !== "" && value !== false) {
      params.append(key, String(value));
    }
  }
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export function exportTransactionsCsv(filters: TransactionFilters = {}): Promise<void> {
  const rest: Record<string, unknown> = { ...filters };
  delete rest.limit; // export all matching rows, not just the page
  delete rest.offset;
  return downloadCsv(`api/export/transactions.csv${exportParams(rest)}`, "transactions.csv");
}

export function exportCategoriesCsv(month?: string): Promise<void> {
  return downloadCsv(`api/export/categories.csv${exportParams({ month })}`, "categories.csv");
}

export function exportMonthlyCsv(months?: number, month?: string): Promise<void> {
  return downloadCsv(`api/export/monthly.csv${exportParams({ months, month })}`, "monthly.csv");
}
