// Lightweight API client.
//
// Under Home Assistant ingress the app is served from a path like
// `/api/hassio_ingress/<token>/`. We capture that base path once at load and
// prefix all API calls with it, so requests resolve correctly both under
// ingress and in local development (where the base is just `/`). The app uses
// a HashRouter, so `globalThis.location.pathname` stays at this base for the whole
// session.

const INGRESS_BASE: string = (() => {
  let path = globalThis.location.pathname;
  if (!path.endsWith("/")) path += "/";
  return path;
})();

export function apiUrl(endpoint: string): string {
  const clean = endpoint.replace(/^\//, "");
  return INGRESS_BASE + clean;
}

// --- MFA session token (backlog #124) ---
// Issued by the backend after a TOTP challenge and sent on every request as the
// X-HAFI-Session header. Stored in **sessionStorage** (not localStorage): the MFA
// challenge should re-appear on a genuinely fresh open of the app, not be silently
// reused for 12h across full reopens — sessionStorage is cleared when the
// page/webview context is closed, so a new open prompts again while in-session
// navigation doesn't nag. The 12h server-side cap still applies on top (#108/#124).
const SESSION_KEY = "hafi_session";

export function getSessionToken(): string | null {
  try {
    return globalThis.sessionStorage.getItem(SESSION_KEY);
  } catch {
    return null;
  }
}

export function setSessionToken(token: string | null): void {
  try {
    if (token) globalThis.sessionStorage.setItem(SESSION_KEY, token);
    else globalThis.sessionStorage.removeItem(SESSION_KEY);
    // Drop any token left in localStorage by an older build, so it can't keep a
    // stale 12h session alive across reopens (the cause of "MFA never prompts").
    globalThis.localStorage.removeItem(SESSION_KEY);
  } catch {
    /* storage unavailable (private mode) — requests just won't carry the token */
  }
}

/** Error thrown by the fetch helpers on a non-2xx response, carrying the parsed body. */
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

// The MFA session header (#124), sent on EVERY authenticated request. Centralised
// so multipart uploads + blob downloads carry it too: those used to use raw fetch
// without it, so every import / receipt-upload / restore / export 403'd under MFA (FE-1).
function sessionHeaders(): Record<string, string> {
  const token = getSessionToken();
  return token ? { "X-HAFI-Session": token } : {};
}

// Turn a non-2xx Response into a typed ApiError carrying status + parsed body, so
// callers (and isStepUpError) can branch on it instead of a plain Error (FE-2).
async function toApiError(res: Response, endpoint: string): Promise<ApiError> {
  let body: Record<string, unknown> | null = null;
  try {
    body = await res.json();
  } catch {
    /* non-JSON error body */
  }
  const detail = typeof body?.detail === "string" ? body.detail : `${res.status} ${res.statusText}`;
  return new ApiError(res.status, body, `API ${endpoint} failed: ${detail}`);
}

export async function fetchJson<T>(endpoint: string, init?: RequestInit): Promise<T> {
  const res = await fetch(apiUrl(endpoint), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...sessionHeaders(),
      ...init?.headers,
    },
  });
  if (!res.ok) throw await toApiError(res, endpoint);
  if (res.status === 204) return undefined as T; // no content (e.g. DELETE)
  return (await res.json()) as T;
}

// Multipart upload through the same auth + error path as fetchJson. Deliberately
// sets NO Content-Type — the browser must set the multipart boundary itself. Fixes
// FE-1 (uploads omitted the session header → 403 under MFA) + FE-2 (raw fetch threw
// untyped errors, so isStepUpError could never fire). Defaults to POST.
export async function fetchForm<T>(endpoint: string, form: FormData, init?: RequestInit): Promise<T> {
  const res = await fetch(apiUrl(endpoint), {
    method: "POST",
    ...init,
    body: form,
    headers: { ...sessionHeaders(), ...init?.headers },
  });
  if (!res.ok) throw await toApiError(res, endpoint);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// Auth'd fetch that returns the raw Response — for blob/file downloads that can't be
// parsed as JSON. Still carries the session header + throws ApiError on failure, so
// backup/config downloads no longer 403 under MFA either (same FE-1 root cause).
async function fetchRaw(endpoint: string, init?: RequestInit): Promise<Response> {
  const res = await fetch(apiUrl(endpoint), {
    ...init,
    headers: { ...sessionHeaders(), ...init?.headers },
  });
  if (!res.ok) throw await toApiError(res, endpoint);
  return res;
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
  // Set when a duplicate is a cross-account Curve match (vs a plain same-account
  // dupe); `warning` flags a kept-but-possible cross match.
  dup_reason: string | null;
  warning: string | null;
}

// A Curve funding-card label found in an upload + its current account mapping.
export interface FundingLabel {
  label: string;
  count: number;
  account_id: number | null;
  account_name: string | null;
}

export interface FundingLink {
  id: number;
  label: string;
  account_id: number;
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
  funding_labels: FundingLabel[];
}

export interface ConfirmResponse {
  import_id: number;
  status: string;
  report: ImportReport;
}

export function listParsers(): Promise<ParserInfo[]> {
  return fetchJson<ParserInfo[]>("api/imports/parsers");
}

export async function uploadImport(
  file: File,
  parserId?: string,
  mapping?: Record<string, string>,
  dateFormat?: DateFormat,
): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  if (parserId) form.append("parser_id", parserId);
  if (mapping) form.append("mapping", JSON.stringify(mapping));
  if (dateFormat) form.append("date_format", dateFormat);
  return fetchForm<UploadResponse>("api/imports/upload", form);
}

// --- Custom CSV column mapping + saved import profiles ---

export interface InspectField {
  key: string;
  label: string;
  required: boolean;
}

export interface InspectResponse {
  headers: string[];
  sample_rows: Record<string, string>[];
  suggested_mapping: Record<string, string>;
  fields: InspectField[];
}

export async function inspectCsv(file: File): Promise<InspectResponse> {
  const form = new FormData();
  form.append("file", file);
  return fetchForm<InspectResponse>("api/imports/inspect", form);
}

// Per-profile CSV date order: "auto" keeps the historic per-file heuristic,
// "dmy" pins UK day-first, "mdy" pins US month-first (matches backend DateFormat).
export type DateFormat = "auto" | "dmy" | "mdy";

export interface ImportProfile {
  id: number;
  name: string;
  mapping: Record<string, string>;
  default_currency: string;
  date_format: DateFormat;
}

export interface ImportProfileInput {
  name: string;
  mapping: Record<string, string>;
  default_currency: string;
  date_format?: DateFormat;
}

export function listImportProfiles(): Promise<ImportProfile[]> {
  return fetchJson<ImportProfile[]>("api/imports/profiles");
}

export function createImportProfile(body: ImportProfileInput): Promise<ImportProfile> {
  return fetchJson<ImportProfile>("api/imports/profiles", { method: "POST", body: JSON.stringify(body) });
}

export function deleteImportProfile(id: number): Promise<void> {
  return fetchJson<void>(`api/imports/profiles/${id}`, { method: "DELETE" });
}

export function confirmImport(importId: number): Promise<ConfirmResponse> {
  return fetchJson<ConfirmResponse>(`api/imports/${importId}/confirm`, { method: "POST" });
}

// Curve funding-card → account mappings (cross-account dedup of the overlay card).
export function listFundingLinks(): Promise<FundingLink[]> {
  return fetchJson<FundingLink[]>("api/imports/funding-links");
}

// Map a Curve "Card Name" to a real account (accountId null clears it). Returns
// the full updated list.
export function setFundingLink(label: string, accountId: number | null): Promise<FundingLink[]> {
  return fetchJson<FundingLink[]>("api/imports/funding-links", {
    method: "PUT",
    body: JSON.stringify({ label, account_id: accountId }),
  });
}

// Opt-in vision-AI fallback: extract transactions from a statement image OCR
// couldn't read, staged as a normal import (preview + confirm). The image is sent
// to the configured AI (the caller warns first).
export async function aiExtractImport(file: File, accountId?: number): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  if (accountId != null) form.append("account_id", String(accountId));
  return fetchForm<UploadResponse>("api/imports/ai-extract", form);
}

// --- Transactions (spec §24.4) ---

export interface Transaction {
  id: number;
  account_id: number | null;
  transaction_date: string;
  posted_date: string | null;
  description_raw: string;
  merchant_raw: string | null;
  merchant_id: number | null;
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
  is_business: boolean;
  vat_amount: string | null;
  country?: string | null;  // per-transaction country for the spend-by-location map
  archived_at?: string | null;
  tags?: Tag[];
}

export interface TransactionListResponse {
  items: Transaction[];
  total: number;
  limit: number;
  offset: number;
}

export interface TransactionFilters {
  transaction_id?: number;
  member_id?: number;
  search?: string;
  date_from?: string;
  date_to?: string;
  amount_min?: string;
  amount_max?: string;
  category_id?: number;
  vendor_id?: number;
  project_id?: number;
  tag_id?: number;
  country?: string;
  needs_review?: boolean;
  uncategorised?: boolean;
  is_business?: boolean;
  include_archived?: boolean;
  limit?: number;
  offset?: number;
}

// Serialise a filter object to a query string, dropping empty values. Shared by the
// transactions list and the CSV export so the two can't diverge — the list used to
// keep `null` (serialising it as the string "null") while the export dropped it, so
// the list and its CSV could return different rows.
function toQuery(filters: Record<string, unknown>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    // Filter values are primitives (string | number | boolean); skip empties and
    // only stringify a confirmed primitive so we never serialise "[object Object]".
    if (value === undefined || value === null || value === "" || value === false) continue;
    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
      params.append(key, String(value));
    }
  }
  return params.toString();
}

export function listTransactions(filters: TransactionFilters = {}): Promise<TransactionListResponse> {
  const qs = toQuery(filters as Record<string, unknown>);
  return fetchJson<TransactionListResponse>(qs ? `api/transactions?${qs}` : "api/transactions");
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

export function updateTransaction(id: number, patch: Record<string, unknown>): Promise<Transaction> {
  return fetchJson<Transaction>(`api/transactions/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
}

export function unarchiveTransaction(id: number): Promise<Transaction> {
  return fetchJson<Transaction>(`api/transactions/${id}/unarchive`, { method: "POST" });
}

// Create (or reuse) a vendor for a transaction that has none, and link it. The
// name defaults to the OCR/parsed merchant signature; pass `name` to override
// (e.g. an AI-suggested vendor). Returns the updated transaction.
export function createVendorFromTransaction(id: number, name?: string): Promise<Transaction> {
  return fetchJson<Transaction>(`api/transactions/${id}/create-vendor`, {
    method: "POST",
    body: JSON.stringify(name ? { name } : {}),
  });
}

// Multi-edit: apply one or more changes to many transactions at once. Only the
// fields present are applied (category_id/project_id/merchant_id may be null to
// clear them); `delete: true` removes them.
export interface BulkUpdate {
  category_id?: number | null;
  project_id?: number | null;
  merchant_id?: number | null;
  is_business?: boolean;
  add_tag?: string;
  country?: string | null;  // ISO alpha-2 for the spend-by-location map
  archive?: boolean;
  delete?: boolean;
}

export function bulkUpdateTransactions(
  ids: number[],
  patch: BulkUpdate,
): Promise<{ updated?: number; deleted?: number }> {
  return fetchJson(`api/transactions/bulk`, {
    method: "POST",
    body: JSON.stringify({ transaction_ids: ids, ...patch }),
  });
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

export function setSplits(id: number, splits: SplitInput[]): Promise<SplitsResponse> {
  return fetchJson<SplitsResponse>(`api/transactions/${id}/split`, {
    method: "POST",
    body: JSON.stringify({ splits }),
  });
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

export function getBudgetSummary(month?: string, annual = false): Promise<BudgetSummaryItem[]> {
  const params = new URLSearchParams();
  if (month) params.set("month", month);
  if (annual) params.set("annual", "true");
  const qs = params.toString();
  return fetchJson<BudgetSummaryItem[]>(qs ? `api/budgets/summary?${qs}` : "api/budgets/summary");
}

export interface BudgetTxn {
  id: number;
  transaction_date: string;
  description: string;
  amount: string;
}

export function getBudgetTransactions(
  budgetId: number,
  opts: { month?: string; annual?: boolean } = {},
): Promise<BudgetTxn[]> {
  const params = new URLSearchParams();
  if (opts.month) params.set("month", opts.month);
  if (opts.annual) params.set("annual", "true");
  const qs = params.toString();
  const path = `api/budgets/${budgetId}/transactions`;
  return fetchJson<BudgetTxn[]>(qs ? `${path}?${qs}` : path);
}

export function createBudget(data: Record<string, unknown>): Promise<Budget> {
  return fetchJson<Budget>("api/budgets", { method: "POST", body: JSON.stringify(data) });
}

export function updateBudget(id: number, data: Record<string, unknown>): Promise<Budget> {
  return fetchJson<Budget>(`api/budgets/${id}`, { method: "PATCH", body: JSON.stringify(data) });
}

export function deleteBudget(id: number): Promise<void> {
  return fetchJson<void>(`api/budgets/${id}`, { method: "DELETE" });
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

export function publishMqtt(): Promise<{ enabled: boolean; published: number; sensors?: number }> {
  return fetchJson("api/mqtt/publish", { method: "POST" });
}

// Choose what gets published to MQTT (per-group + per-sensor).
export interface MqttSensorGroup {
  key: string;
  label: string;
  disabled: boolean;
}
export interface MqttSensor {
  key: string;
  name: string;
  group: string;
  enabled: boolean;
}
export interface MqttSensorSelection {
  groups: MqttSensorGroup[];
  sensors: MqttSensor[];
  disabled_sensors: string[]; // raw individual-sensor denylist (for round-tripping)
}

export function getMqttSensors(): Promise<MqttSensorSelection> {
  return fetchJson<MqttSensorSelection>("api/mqtt/sensors");
}

export function updateMqttSensors(
  disabledGroups: string[],
  disabledSensors: string[],
): Promise<MqttSensorSelection> {
  return fetchJson<MqttSensorSelection>("api/mqtt/sensors", {
    method: "PUT",
    body: JSON.stringify({ disabled_groups: disabledGroups, disabled_sensors: disabledSensors }),
  });
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

export function createProject(data: Record<string, unknown>): Promise<Project> {
  return fetchJson<Project>("api/projects", { method: "POST", body: JSON.stringify(data) });
}

export function updateProject(id: number, data: Record<string, unknown>): Promise<Project> {
  return fetchJson<Project>(`api/projects/${id}`, { method: "PATCH", body: JSON.stringify(data) });
}

export function deleteProject(id: number): Promise<void> {
  return fetchJson<void>(`api/projects/${id}`, { method: "DELETE" });
}

export function getDashboardProjects(memberId?: number): Promise<ProjectTotal[]> {
  return fetchJson<ProjectTotal[]>(memberId ? `api/dashboard/projects?member_id=${memberId}` : "api/dashboard/projects");
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

export interface TagUsage {
  id: number;
  count: number;
}

export function getTagUsage(): Promise<TagUsage[]> {
  return fetchJson<TagUsage[]>("api/tags/usage");
}

export function mergeTags(sourceId: number, targetId: number): Promise<Tag> {
  return fetchJson<Tag>("api/tags/merge", {
    method: "POST",
    body: JSON.stringify({ source_id: sourceId, target_id: targetId }),
  });
}

export function deleteUnusedTags(): Promise<{ deleted: number }> {
  return fetchJson<{ deleted: number }>("api/tags/unused", { method: "DELETE" });
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

export function deleteSubscription(id: number): Promise<void> {
  return fetchJson<void>(`api/subscriptions/${id}`, { method: "DELETE" });
}

export function getDashboardSubscriptions(memberId?: number): Promise<DashboardSubscriptions> {
  return fetchJson<DashboardSubscriptions>(memberId ? `api/dashboard/subscriptions?member_id=${memberId}` : "api/dashboard/subscriptions");
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

export interface RecommendedTransaction {
  merchant: string;
  transaction_date: string;
  amount: string; // signed (negative = money out)
  currency: string;
  category_id: number | null;
  category_name: string | null;
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
  has_file: boolean;
  matches: ReceiptMatch[];
  // Present when nothing matched and a total is set: a pre-filled transaction to add.
  recommended_transaction: RecommendedTransaction | null;
  already_imported?: boolean; // set on upload when a byte-identical receipt already existed
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
  return fetchForm<Receipt>("api/receipts/upload", form);
}

export function updateReceipt(id: number, fields: Record<string, unknown>): Promise<Receipt> {
  return fetchJson<Receipt>(`api/receipts/${id}`, { method: "PATCH", body: JSON.stringify(fields) });
}

// Opt-in vision-AI fallback: read merchant/date/total from a receipt image OCR
// couldn't. The image is sent to the configured AI (the caller warns first).
export function aiExtractReceipt(id: number): Promise<Receipt> {
  return fetchJson<Receipt>(`api/receipts/${id}/ai-extract`, { method: "POST" });
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

export function deleteReceipt(id: number): Promise<void> {
  return fetchJson<void>(`api/receipts/${id}`, { method: "DELETE" });
}

export interface CreateTransactionResult {
  transaction_id: number;
  receipt: Receipt;
}

// Materialise a transaction from an unmatched receipt (cash / un-imported).
// Either target an existing account, or set new_account to use/create a
// dedicated "Cash & receipts" account.
export function createTransactionFromReceipt(
  id: number,
  body: { account_id?: number; new_account?: boolean },
): Promise<CreateTransactionResult> {
  return fetchJson<CreateTransactionResult>(`api/receipts/${id}/create-transaction`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// Per-transaction receipt attach + viewer (backlog: receipt image on a transaction).
export function receiptFileUrl(id: number): string {
  return apiUrl(`api/receipts/${id}/file`);
}

export function listTransactionReceipts(transactionId: number): Promise<Receipt[]> {
  return fetchJson<Receipt[]>(`api/transactions/${transactionId}/receipts`);
}

export async function attachTransactionReceipt(transactionId: number, file: File): Promise<Receipt> {
  const form = new FormData();
  form.append("file", file);
  return fetchForm<Receipt>(`api/transactions/${transactionId}/receipts`, form);
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
  country?: string | null; // ISO-3166-1 alpha-2, when the AI could infer it
  vendor?: string | null; // clean merchant name the AI inferred, to create + link
  payload?: Record<string, unknown> | null;
}

export const PRIVACY_MODES = ["strict_local", "local_llm", "cloud_manual", "cloud_auto", "no_ai"] as const;

export function getAiStatus(): Promise<AIStatus> {
  return fetchJson<AIStatus>("api/ai/status");
}

export interface AiTestResult {
  ok: boolean;
  reason: string; // off | not_configured | error | ok
  message: string;
  sample_category?: string | null;
}

export function testAiConnection(): Promise<AiTestResult> {
  return fetchJson<AiTestResult>("api/ai/test", { method: "POST" });
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

function aiPost(path: string): Promise<ClassifyResult> {
  return fetchJson<ClassifyResult>(path, { method: "POST" });
}

export function classifyWithAi(transactionId: number): Promise<ClassifyResult> {
  return aiPost(`api/ai/classify/${transactionId}`);
}

// Approve a pending cloud request (cloud_manual): sends it and returns the suggestion.
export function approveAiRequest(requestId: number): Promise<ClassifyResult> {
  return aiPost(`api/ai/requests/${requestId}/approve`);
}

export function rejectAiRequest(requestId: number): Promise<void> {
  return fetchJson<void>(`api/ai/requests/${requestId}/reject`, { method: "POST" });
}

export function listAiRequests(opts?: { includeArchived?: boolean }): Promise<AIRequestRow[]> {
  const qs = opts?.includeArchived ? "?include_archived=true" : "";
  return fetchJson<AIRequestRow[]>(`api/ai/requests${qs}`);
}

export interface BatchSuggestion {
  transaction_id: number;
  description: string;
  amount: string;
  category_id: number;
  category_name: string;
  confidence: number | null;
  rationale: string | null;
  already_ai_processed?: boolean;
}

export interface BatchResult {
  considered: number;
  count: number;
  suggestions: BatchSuggestion[];
}

export type BatchScope = "uncategorised" | "recheck";

export function classifyBatch(limit = 25, scope: BatchScope = "uncategorised"): Promise<BatchResult> {
  return fetchJson<BatchResult>(`api/ai/classify-batch?limit=${limit}&scope=${scope}`, { method: "POST" });
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
  already_ai_processed?: boolean; // has a prior completed AIRequest → unticked by default
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

export function cloudBatchPrepare(limit = 25, scope: BatchScope = "uncategorised"): Promise<CloudBatchPreview> {
  return fetchJson<CloudBatchPreview>(`api/ai/cloud-batch/prepare?limit=${limit}&scope=${scope}`, { method: "POST" });
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

export function deleteCategory(id: number): Promise<void> {
  return fetchJson<void>(`api/categories/${id}`, { method: "DELETE" });
}

export function mergeCategory(id: number, targetId: number): Promise<Category> {
  return fetchJson<Category>(`api/categories/${id}/merge`, {
    method: "POST",
    body: JSON.stringify({ target_id: targetId }),
  });
}

export function getCategoryPrivacyDefault(): Promise<{ level: string }> {
  return fetchJson("api/categories/privacy");
}

export function setAllCategoryPrivacy(level: string): Promise<{ updated: number; level: string }> {
  return fetchJson("api/categories/privacy", { method: "POST", body: JSON.stringify({ level }) });
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
  country: string | null;  // ISO alpha-2 (spend-by-location map)
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

// Patch vendor fields (e.g. country for the spend-by-location map; "" → null clears).
export function updateVendor(id: number, patch: Record<string, unknown>): Promise<Vendor> {
  return fetchJson<Vendor>(`api/vendors/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
}

export function setVendorDefaultCategory(id: number, categoryId: number | null): Promise<Vendor> {
  return fetchJson<Vendor>(`api/vendors/${id}/set-default-category`, {
    method: "POST",
    body: JSON.stringify({ category_id: categoryId }),
  });
}

export function deleteVendor(id: number): Promise<void> {
  return fetchJson<void>(`api/vendors/${id}`, { method: "DELETE" });
}

// Absorb one vendor into another: `sourceId` is deleted and its transactions +
// aliases re-point to `targetId`. Owner only; returns the surviving target (#334).
export function mergeVendors(sourceId: number, targetId: number): Promise<Vendor> {
  return fetchJson<Vendor>(`api/vendors/${sourceId}/merge`, {
    method: "POST",
    body: JSON.stringify({ target_id: targetId }),
  });
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

function dashQuery(month?: string, view?: string, memberId?: number): string {
  const q = new URLSearchParams();
  if (month) q.set("month", month);
  if (view && view !== "all") q.set("view", view);  // Mine/Shared/All toggle (#66)
  if (memberId) q.set("member_id", String(memberId));  // per-member filter (#66/#82)
  const qs = q.toString();
  return qs ? `?${qs}` : "";
}

export function getSummary(month?: string, view?: string, memberId?: number): Promise<DashboardSummary> {
  return fetchJson<DashboardSummary>(`api/dashboard/summary${dashQuery(month, view, memberId)}`);
}

export function getCategoryBreakdown(month?: string, view?: string, memberId?: number): Promise<CategoryBreakdownItem[]> {
  return fetchJson<CategoryBreakdownItem[]>(`api/dashboard/categories${dashQuery(month, view, memberId)}`);
}

export function getVendorBreakdown(month?: string, view?: string, memberId?: number): Promise<VendorBreakdownItem[]> {
  return fetchJson<VendorBreakdownItem[]>(`api/dashboard/vendors${dashQuery(month, view, memberId)}`);
}

export interface CountryBreakdownItem {
  country_code: string | null;
  name: string;
  flag: string;
  total: string;
  count: number;
}

export function getCountryBreakdown(month?: string, view?: string, memberId?: number): Promise<CountryBreakdownItem[]> {
  return fetchJson<CountryBreakdownItem[]>(`api/dashboard/by-country${dashQuery(month, view, memberId)}`);
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

export function getMonthlySeries(months = 6, month?: string, view?: string, memberId?: number): Promise<MonthlySeries> {
  const q = new URLSearchParams({ months: String(months) });
  if (month) q.set("month", month);
  if (view && view !== "all") q.set("view", view);
  if (memberId) q.set("member_id", String(memberId));
  return fetchJson<MonthlySeries>(`api/dashboard/monthly?${q.toString()}`);
}

export function getOutliers(month?: string, memberId?: number): Promise<OutliersResponse> {
  const q = new URLSearchParams();
  if (month) q.set("month", month);
  if (memberId) q.set("member_id", String(memberId));
  const qs = q.toString();
  return fetchJson<OutliersResponse>(qs ? `api/dashboard/outliers?${qs}` : "api/dashboard/outliers");
}

export interface ProcessingStats {
  statements_imported: number;
  transactions_imported: number;
  receipts_total: number;
  receipts_processed: number;
  receipts_failed: number;
  receipts_pending: number;
  ai_total: number;
  ai_completed: number;
  ai_failed: number;
  ai_pending: number;
  ai_cloud: number;
  ai_local: number;
  ai_avg_seconds: number | null;
  ai_by_task: Record<string, number>;
}

export function getProcessingStats(): Promise<ProcessingStats> {
  return fetchJson<ProcessingStats>("api/dashboard/processing");
}

export interface MemberSpendRow {
  member_id: number | null; // null = the "Shared / unassigned" row (unowned accounts)
  display_name: string;
  role: string | null;
  spend: string;
  income: string;
  net: string;
}

export interface MemberBreakdown {
  month: string;
  currency: string;
  members: MemberSpendRow[];
}

export function getMemberBreakdown(month?: string): Promise<MemberBreakdown> {
  const qs = month ? `?month=${encodeURIComponent(month)}` : "";
  return fetchJson<MemberBreakdown>(`api/dashboard/by-member${qs}`);
}

// --- Savings (spec §12.4; backlog #96, #91) ---

export interface SavingsAccount {
  id: number;
  name: string;
  institution: string | null;
  currency: string;
  latest_balance: string | null;
  balance_count: number;
  interest_rate: string | null;
  projected_annual_interest: string | null;
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

// Total savings over time (monthly point-in-time) for the Savings chart. Returns
// the shared TimeSeries shape (defined in the Travel section).
export function getSavingsHistory(months = 12): Promise<TimeSeries> {
  return fetchJson<TimeSeries>(`api/savings/history?months=${months}`);
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

// Deposit/withdraw via the +/- control (records a snapshot at latest ± amount).
export function adjustSavingsBalance(
  accountId: number,
  data: { amount: string; direction: "deposit" | "withdraw"; note?: string },
): Promise<SavingsBalance> {
  return fetchJson(`api/savings/accounts/${accountId}/adjust`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

// Edit a savings account (currently the interest rate; null clears it).
export function updateSavingsAccount(
  accountId: number,
  data: { interest_rate?: string | null },
): Promise<SavingsAccount> {
  return fetchJson(`api/savings/accounts/${accountId}`, {
    method: "PATCH",
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

// --- Investments & pensions (spec §12.4, §27) ---

export interface InvestmentAccount {
  id: number;
  name: string;
  institution: string | null;
  currency: string;
  account_type: string; // investment | pension
  current_value: string | null;
  cost_basis: string | null;
  gain: string | null;
  gain_pct: number | null;
  has_holdings: boolean;
  holdings_count: number;
  value_count: number;
}

export interface Holding {
  id: number;
  account_id: number;
  symbol: string;
  name: string | null;
  units: string;
  avg_cost: string | null;
  last_price: string | null;
  last_price_at: string | null;
  currency: string;
  market_value: string | null;
  cost_basis: string | null;
  gain: string | null;
  gain_pct: number | null;
}

export interface AccountValue {
  id: number;
  account_id: number;
  as_of_date: string;
  value: string;
  currency: string;
  note: string | null;
}

export interface InvestmentSummary {
  currency: string;
  total_value: string;
  total_cost: string | null;
  total_gain: string | null;
  total_gain_pct: number | null;
  by_type: Record<string, string>;
  accounts: InvestmentAccount[];
}

export function getInvestmentSummary(): Promise<InvestmentSummary> {
  return fetchJson<InvestmentSummary>("api/investments/summary");
}

export interface PeriodChange {
  change: string;
  pct: number | null;
}

export interface InvestmentHistory {
  currency: string;
  total_value: string;
  points: { date: string; value: string }[];
  change_day: PeriodChange;
  change_month: PeriodChange;
  change_year: PeriodChange;
}

// Portfolio value over time + day/month/year change (for the charts).
export function getInvestmentHistory(days = 365): Promise<InvestmentHistory> {
  return fetchJson<InvestmentHistory>(`api/investments/history?days=${days}`);
}

export function listInvestmentAccounts(): Promise<InvestmentAccount[]> {
  return fetchJson<InvestmentAccount[]>("api/investments/accounts");
}

export function createInvestmentAccount(data: {
  name: string;
  account_type: "investment" | "pension";
  institution?: string;
  currency?: string;
}): Promise<InvestmentAccount> {
  return fetchJson("api/investments/accounts", { method: "POST", body: JSON.stringify(data) });
}

export function getValueHistory(accountId: number): Promise<AccountValue[]> {
  return fetchJson<AccountValue[]>(`api/investments/accounts/${accountId}/values`);
}

export function recordAccountValue(
  accountId: number,
  data: { as_of_date: string; value: string; note?: string },
): Promise<AccountValue> {
  return fetchJson(`api/investments/accounts/${accountId}/values`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

// Contribution/withdrawal via the +/- control (snapshot at latest ± amount).
export function adjustAccountValue(
  accountId: number,
  data: { amount: string; direction: "contribution" | "withdrawal"; note?: string },
): Promise<AccountValue> {
  return fetchJson(`api/investments/accounts/${accountId}/adjust`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function getHoldings(accountId: number): Promise<Holding[]> {
  return fetchJson<Holding[]>(`api/investments/accounts/${accountId}/holdings`);
}

export function createHolding(
  accountId: number,
  data: { symbol: string; units: string; name?: string; avg_cost?: string; last_price?: string },
): Promise<Holding> {
  return fetchJson(`api/investments/accounts/${accountId}/holdings`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function updateHolding(
  holdingId: number,
  data: Record<string, unknown>,
): Promise<Holding> {
  return fetchJson(`api/investments/holdings/${holdingId}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export function deleteHolding(holdingId: number): Promise<void> {
  return fetchJson(`api/investments/holdings/${holdingId}`, { method: "DELETE" });
}

export interface InvestmentPriceStatus {
  source: string; // manual | stooq | alphavantage
  api_key_present: boolean;
  ready: boolean;
}

export function getInvestmentPriceStatus(): Promise<InvestmentPriceStatus> {
  return fetchJson<InvestmentPriceStatus>("api/investments/price-status");
}

export interface PriceSyncResult {
  source: string;
  ran: boolean;
  updated: number;
  failed: number;
  total: number;
}

// Fetch latest quotes for the caller's holdings (no-op when source is manual).
export function syncInvestmentPrices(): Promise<PriceSyncResult> {
  return fetchJson<PriceSyncResult>("api/investments/sync-prices", { method: "POST" });
}

// --- Assets (car/home/other; spec §25.1) ---

export interface CarSegment {
  date: string;
  from_odometer: string;
  to_odometer: string;
  distance: string;
  litres: string;
  l_per_100km: number;
  mpg: number;
  economy: number;   // in the asset's system (MPG or L/100km)
  fuel: string;      // in the asset's system (gal or L)
  cost: string | null;
}

export interface CarStats {
  distance_unit: string;
  system: string;        // "imperial" | "metric"
  fuel_unit: string;     // "gal" | "L"
  economy_unit: string;  // "MPG" | "L/100km"
  refuel_count: number;
  latest_odometer: string | null;
  total_fuel_cost: string;
  total_litres: string;
  total_fuel: string;    // in the asset's system
  avg_l_per_100km: number | null;
  avg_mpg: number | null;
  avg_economy: number | null;   // in the asset's system
  last_economy: number | null;  // in the asset's system
  last_l_per_100km: number | null;
  last_mpg: number | null;
  segments: CarSegment[];
}

export interface MeterSegment {
  date: string;
  usage: string;
  days: number;
  avg_per_day: number | null;
  cost: string | null;
}

export interface MeterStats {
  meter: string;
  unit: string | null;
  latest_reading: string;
  reading_count: number;
  total_usage: string;
  total_cost: string;
  segments: MeterSegment[];
}

export interface HomeStats {
  meters: MeterStats[];
}

export interface AssetLog {
  id: number;
  asset_id: number;
  log_date: string;
  kind: string; // refuel | service | expense | reading | note
  note: string | null;
  cost: string | null;
  odometer: string | null;
  litres: string | null;
  is_full_tank: boolean | null;
  fuel_type: string | null;
  meter: string | null;
  reading: string | null;
  unit: string | null;
  transaction_id: number | null;
}

export interface Asset {
  id: number;
  name: string;
  kind: string; // car | home | other
  identifier: string | null;
  distance_unit: string;
  is_active: boolean;
  log_count: number;
  total_cost: string;
  car?: CarStats;
  home?: HomeStats;
  logs?: AssetLog[];
}

export function listAssets(kind?: string): Promise<Asset[]> {
  return fetchJson<Asset[]>(kind ? `api/assets?kind=${encodeURIComponent(kind)}` : "api/assets");
}

export function getAsset(id: number): Promise<Asset> {
  return fetchJson<Asset>(`api/assets/${id}`);
}

export function createAsset(data: {
  name: string;
  kind: string;
  identifier?: string;
  distance_unit?: string;
}): Promise<Asset> {
  return fetchJson("api/assets", { method: "POST", body: JSON.stringify(data) });
}

export function updateAsset(id: number, data: Record<string, unknown>): Promise<Asset> {
  return fetchJson(`api/assets/${id}`, { method: "PATCH", body: JSON.stringify(data) });
}

export function deleteAsset(id: number): Promise<void> {
  return fetchJson(`api/assets/${id}`, { method: "DELETE" });
}

export function addAssetLog(assetId: number, data: Record<string, unknown>): Promise<AssetLog> {
  return fetchJson(`api/assets/${assetId}/logs`, { method: "POST", body: JSON.stringify(data) });
}

export function deleteAssetLog(logId: number): Promise<void> {
  return fetchJson(`api/assets/logs/${logId}`, { method: "DELETE" });
}

// --- Paperless-ngx import (spec §21) ---

export interface PaperlessStatus {
  configured: boolean;
  url: string | null;
  url_source: "settings" | "env" | null;
  token_present: boolean;
}

export interface PaperlessDoc {
  id: number;
  title: string;
  created: string | null;
}

export interface PaperlessImportResult {
  receipt_id: number;
  created: boolean;
  source: string;
  filename: string;
}

export function getPaperlessStatus(): Promise<PaperlessStatus> {
  return fetchJson<PaperlessStatus>("api/paperless/status");
}

export function testPaperlessConnection(): Promise<{ ok: boolean; url: string }> {
  return fetchJson("api/paperless/test", { method: "POST" });
}

export function listPaperlessDocuments(query?: string, limit = 25): Promise<PaperlessDoc[]> {
  const qs = new URLSearchParams();
  if (query) qs.set("query", query);
  qs.set("limit", String(limit));
  return fetchJson<PaperlessDoc[]>(`api/paperless/documents?${qs.toString()}`);
}

export function importPaperlessDocument(id: number): Promise<PaperlessImportResult> {
  return fetchJson<PaperlessImportResult>(`api/paperless/documents/${id}/import`, { method: "POST" });
}

// --- Backup / restore / demo (spec §26.5; backlog #9, #10, #16) ---

export function loadDemoData(): Promise<{ rows_detected: number; new: number; duplicates: number }> {
  return fetchJson("api/backup/demo", { method: "POST" });
}

export function getDemoStatus(): Promise<{ has_demo_data: boolean }> {
  return fetchJson("api/backup/demo");
}

export function removeDemoData(): Promise<{ removed: boolean; counts: Record<string, number> }> {
  return fetchJson("api/backup/demo", { method: "DELETE" });
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
  const res = await fetchRaw("api/backup/database");
  triggerDownload(await res.blob(), "ha-finance-backup.db");
}

export async function restoreDatabase(file: File): Promise<{ status: string }> {
  const form = new FormData();
  form.append("file", file);
  return fetchForm<{ status: string }>("api/backup/restore", form);
}

export async function downloadEncryptedBackup(passphrase: string): Promise<void> {
  const form = new FormData();
  form.append("passphrase", passphrase);
  const res = await fetchRaw("api/backup/database/encrypted", { method: "POST", body: form });
  triggerDownload(await res.blob(), "ha-finance-backup.db.enc");
}

export async function restoreEncryptedDatabase(file: File, passphrase: string): Promise<{ status: string }> {
  const form = new FormData();
  form.append("file", file);
  form.append("passphrase", passphrase);
  return fetchForm<{ status: string }>("api/backup/restore/encrypted", form);
}

export async function exportConfig(): Promise<void> {
  const res = await fetchRaw("api/backup/config");
  const data = await res.json();
  triggerDownload(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }), "ha-finance-config.json");
}

export async function importConfig(
  file: File,
): Promise<{
  categories_added: number;
  vendors_added: number;
  settings_set: number;
  settings_skipped?: number;
  skipped_setting_keys?: string[];
}> {
  const form = new FormData();
  form.append("file", file);
  return fetchForm("api/backup/config", form);
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

export interface Currency {
  code: string;
  name: string;
  symbol: string;
}

// Curated base-currency choices for the Settings dropdown (top-10).
export function getSupportedCurrencies(): Promise<Currency[]> {
  return fetchJson<Currency[]>("api/settings/currencies");
}

export interface Country {
  code: string; // ISO-3166-1 alpha-2
  name: string;
}

// ISO countries (code + name) for the vendor / trip country pickers.
export function getCountries(): Promise<Country[]> {
  return fetchJson<Country[]>("api/settings/countries");
}

export function updateSettings(patch: Partial<AppSettings>): Promise<AppSettings & { recompute?: unknown }> {
  return fetchJson("api/settings", { method: "PUT", body: JSON.stringify(patch) });
}

// --- Services control panel (Settings → Services; backlog §38) ---

export interface ServiceState {
  enabled: boolean;
  mode?: string;
  configured?: boolean;
  configurable: boolean;
  detail: string;
}

export interface ServicesStatus {
  ai: ServiceState;
  ocr: ServiceState;
  fx: ServiceState;
  mqtt: ServiceState;
}

export function getServices(): Promise<ServicesStatus> {
  return fetchJson<ServicesStatus>("api/settings/services");
}

export function updateServiceSettings(patch: {
  privacy_mode?: string;
  fx_mode?: string;
  ocr_enabled?: boolean;
}): Promise<unknown> {
  return fetchJson("api/settings", { method: "PUT", body: JSON.stringify(patch) });
}

// Storage + processing/AI tallies for the Settings "Storage & statistics" card
// (manager-gated). DB size is the on-disk SQLite file (+ wal/shm sidecars).
export interface SettingsStats {
  database_bytes: number;
  transactions: number;
  statements: number;
  receipts: number;
  ai_total: number;
  ai_cloud: number;
  ai_local: number;
  ai_completed: number;
  ai_failed: number;
  ai_avg_seconds: number | null;
}

export function getSettingsStats(): Promise<SettingsStats> {
  return fetchJson<SettingsStats>("api/settings/stats");
}

// --- Global search ---

export interface SearchResults {
  query: string;
  transactions: { id: number; transaction_date: string; description: string; amount: string; currency: string }[];
  vendors: { id: number; name: string }[];
  categories: { id: number; name: string; colour: string | null }[];
  projects: { id: number; name: string; status: string }[];
}

export function searchAll(q: string): Promise<SearchResults> {
  return fetchJson<SearchResults>(`api/search?q=${encodeURIComponent(q)}`);
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

export function getMissingFx(): Promise<{ needs_rate: number }> {
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
  "set_country",
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

export function deleteRule(id: number): Promise<void> {
  return fetchJson<void>(`api/rules/${id}`, { method: "DELETE" });
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
  stored_key_present?: boolean;
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

// --- Energy-cost offset (HA) ---

export interface EnergyOffset {
  month: string;
  currency: string;
  source: string; // off | ha_api | mqtt
  configured: boolean;
  available: boolean;
  produced_kwh: string;
  unit_price: string | null;
  unit_price_source: string; // tariff | derived | none
  saving: string;
  energy_spend: string;
  net_cost: string;
  energy_category_id: number | null;
}

export interface EnergyConfig {
  source: string;
  production_entities: string[];
  production_topics: string[];
  tariff_per_kwh: string;
  energy_category_id: number | null;
  production_semantics: string; // cumulative | interval
}

export interface EnergyStatus extends EnergyConfig {
  available: boolean;
  ha_api_available: boolean;
  derived_unit_price: string | null;
}

export function getEnergyOffset(month?: string): Promise<EnergyOffset> {
  const qs = month ? `?month=${encodeURIComponent(month)}` : "";
  return fetchJson<EnergyOffset>(`api/energy/offset${qs}`);
}

export function getEnergyStatus(): Promise<EnergyStatus> {
  return fetchJson<EnergyStatus>("api/energy/status");
}

export function getEnergyConfig(): Promise<EnergyConfig> {
  return fetchJson<EnergyConfig>("api/energy/config");
}

export function updateEnergyConfig(patch: Partial<EnergyConfig>): Promise<EnergyConfig> {
  return fetchJson<EnergyConfig>("api/energy/config", { method: "PUT", body: JSON.stringify(patch) });
}

export interface EnergyHistory {
  period: string; // day | month | year
  currency: string;
  energy_category_id: number | null;
  buckets: { label: string; spend: string }[];
}

export function getEnergyHistory(period: string, count: number): Promise<EnergyHistory> {
  return fetchJson<EnergyHistory>(`api/energy/history?period=${period}&count=${count}`);
}

export interface EnergyProductionHistory {
  period: string; // day | month | year
  currency: string;
  semantics: string; // cumulative | interval
  unit_price: string | null;
  buckets: { label: string; produced_kwh: string; saving: string }[];
}

export function getEnergyProductionHistory(period: string, count: number): Promise<EnergyProductionHistory> {
  return fetchJson<EnergyProductionHistory>(
    `api/energy/production-history?period=${encodeURIComponent(period)}&count=${encodeURIComponent(count)}`,
  );
}

// --- Users & access control (spec §6, §28; backlog #82, #126) ---

export interface Me {
  id: number;
  display_name: string;
  role: string;
  status: string;
  is_admin: boolean;
  can_write: boolean;
  can_manage_settings: boolean;
  blocked_nav_keys: string[]; // pages this user is restricted from (#108)
  mfa_enabled: boolean;
  mfa_scope: string; // app | app_admin — what MFA gates (#157)
  mfa_policy: string; // optional | required (admin-set, #157)
  mfa_required: boolean;
  mfa_setup_required: boolean; // admin requires MFA but the user hasn't enrolled (#157)
}

export interface User {
  id: number;
  display_name: string;
  email: string | null;
  role: string;
  status: string;
  is_active: boolean;
  can_manage_settings: boolean;
  blocked_nav_keys: string[]; // pages this user is restricted from (#108)
  mfa_enabled: boolean;
  mfa_policy: string; // optional | required (admin-set, #157)
  external_id: string | null;
  last_seen_at: string | null;
  created_at: string;
}

export interface Member {
  id: number;
  display_name: string;
  role: string;
}

export function getMe(): Promise<Me> {
  return fetchJson<Me>("api/users/me");
}

export function listUsers(): Promise<User[]> {
  return fetchJson<User[]>("api/users");
}

// Approved household members for the per-member spend filter (any approved user).
export function listMembers(): Promise<Member[]> {
  return fetchJson<Member[]>("api/users/members");
}

export function updateUser(
  id: number,
  patch: {
    role?: string;
    status?: string;
    display_name?: string;
    email?: string;
    can_manage_settings?: boolean;
    blocked_nav_keys?: string[];
    mfa_policy?: string;
  },
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

export function mfaEnable(code: string, scope?: string): Promise<{ status: string; mfa_scope: string }> {
  return fetchJson("api/auth/mfa/enable", { method: "POST", body: JSON.stringify({ code, scope }) });
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

// One-time backup/recovery codes (CR-FEAT-1). POST replaces any prior set and
// returns the plaintext codes ONCE (step-up gated); GET reports how many remain.
export function generateMfaBackupCodes(): Promise<{ codes: string[]; remaining: number }> {
  return fetchJson("api/auth/mfa/backup-codes", { method: "POST" });
}

export function getMfaBackupCodesRemaining(): Promise<{ remaining: number }> {
  return fetchJson("api/auth/mfa/backup-codes");
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

// Filters shared by the activity listing and its CSV export (backend applies
// them in SQL): action-name prefix, free-text q over action + details, actor
// substring, and an inclusive ISO date range on the entry timestamp.
export interface ActivityLogFilters {
  limit?: number;
  action?: string;
  includeArchived?: boolean;
  q?: string;
  actor?: string;
  dateFrom?: string;
  dateTo?: string;
}

export function listActivityLog(opts?: ActivityLogFilters): Promise<AuditLogRow[]> {
  const params = new URLSearchParams();
  if (opts?.limit) params.set("limit", String(opts.limit));
  if (opts?.action) params.set("action", opts.action);
  if (opts?.includeArchived) params.set("include_archived", "true");
  if (opts?.q) params.set("q", opts.q);
  if (opts?.actor) params.set("actor", opts.actor);
  if (opts?.dateFrom) params.set("date_from", opts.dateFrom);
  if (opts?.dateTo) params.set("date_to", opts.dateTo);
  const qs = params.toString();
  return fetchJson<AuditLogRow[]>(qs ? `api/logs/activity?${qs}` : "api/logs/activity");
}

export function listAuditActions(): Promise<string[]> {
  return fetchJson<string[]>("api/logs/actions");
}

// --- Travel / spend-abroad (backlog: holidays by country/currency) ---

export interface CurrencySpend {
  currency: string;
  place: string;
  original_total: string;
  base_total: string;
  count: number;
  first: string;
  last: string;
}

export interface TravelByCurrency {
  base_currency: string;
  currencies: CurrencySpend[];
}

export interface TripTxn {
  id: number;
  transaction_date: string;
  description: string;
  amount: string;
  currency: string;
  base_amount: string;
}

export interface Trip {
  first: string;
  last: string;
  currencies: string[];
  places: string[];
  label: string;
  base_total: string;
  base_currency: string;
  transaction_count: number;
  transaction_ids: number[];
  transactions: TripTxn[];
}

export function getTravelByCurrency(): Promise<TravelByCurrency> {
  return fetchJson<TravelByCurrency>("api/travel/by-currency");
}

// Over-time series (monthly totals, base currency) shared by the Travel + Projects
// charts (and reused by the Savings/Investments period selectors).
export interface TimeSeries {
  currency: string;
  months: { month: string; total: string }[];
}

export function getTravelHistory(months = 12): Promise<TimeSeries> {
  return fetchJson<TimeSeries>(`api/travel/history?months=${months}`);
}

export function getProjectsHistory(months = 12): Promise<TimeSeries> {
  return fetchJson<TimeSeries>(`api/projects/history?months=${months}`);
}

export function getTravelTrips(gapDays?: number): Promise<Trip[]> {
  const qs = gapDays ? `?gap_days=${gapDays}` : "";
  return fetchJson<Trip[]>(`api/travel/trips${qs}`);
}

export function createProjectFromTrip(
  name: string,
  transactionIds: number[],
  budgetAmount?: string,
): Promise<{ project_id: number; name: string }> {
  return fetchJson("api/travel/trips/project", {
    method: "POST",
    body: JSON.stringify({
      name,
      transaction_ids: transactionIds,
      budget_amount: budgetAmount || null,
    }),
  });
}

// --- Business / VAT expenses (backlog: corporate receipts) ---

export interface BusinessCategoryRow {
  category_id: number | null;
  name: string;
  total: string;
  vat: string;
}

export interface BusinessPeriodRow {
  period: string;
  label: string;
  start: string;
  end: string;
  total: string;
  vat: string;
  count: number;
}

export interface BusinessSummary {
  currency: string;
  period: string;
  total: string;
  vat: string;
  transaction_count: number;
  first: string | null;
  last: string | null;
  by_category: BusinessCategoryRow[];
  by_period: BusinessPeriodRow[];
}

export function getBusinessSummary(period = "month", year?: number | null): Promise<BusinessSummary> {
  const q = new URLSearchParams({ period });
  if (year != null) q.set("year", String(year));
  return fetchJson<BusinessSummary>(`api/business/summary?${q.toString()}`);
}

// --- Data retention (spec §28; backlog #78, #147) ---

export interface RetentionTypePolicy {
  archive_after_days?: number | null;
  purge_after_days?: number | null;
  auto_purge?: boolean;
}

export interface BackupTrim {
  max_age_days: number;
  max_total_mb: number;
  min_keep: number;
}

export interface RetentionPolicyResponse {
  policy: Record<string, RetentionTypePolicy>;
  data_types: string[];
  archivable: string[];
  receipt_delete_after_processing: boolean;
  backup_trim: BackupTrim;
}

export interface RetentionTypePlan {
  archive_due: number;
  purge_due: number;
  auto_purge: boolean;
}

export interface RetentionPlan {
  pending_purge: number;
  [dataType: string]: RetentionTypePlan | number;
}

export interface RetentionRunResult {
  counts: Record<string, { archived: number; purged: number }>;
  backup_taken: boolean;
}

export interface RetentionPolicyUpdate {
  policy?: Record<string, RetentionTypePolicy>;
  receipt_delete_after_processing?: boolean;
  backup_trim?: Partial<BackupTrim>;
}

export function getRetentionPolicy(): Promise<RetentionPolicyResponse> {
  return fetchJson<RetentionPolicyResponse>("api/retention/policy");
}

export function updateRetentionPolicy(patch: RetentionPolicyUpdate): Promise<RetentionPolicyResponse> {
  return fetchJson<RetentionPolicyResponse>("api/retention/policy", {
    method: "PUT",
    body: JSON.stringify(patch),
  });
}

export function previewRetention(): Promise<RetentionPlan> {
  return fetchJson<RetentionPlan>("api/retention/preview");
}

export function runRetention(): Promise<RetentionRunResult> {
  return fetchJson<RetentionRunResult>("api/retention/run", { method: "POST" });
}

// --- CSV export (backlog #132) ---
// Fetched (not a plain <a download>) so the MFA session header travels with the
// request; the response is turned into a blob and downloaded client-side.

// Sanitise a server-provided download filename: strip path separators and clamp the
// length so it can't traverse paths or be absurdly long (CR-FEAT-13).
function sanitizeFilename(name: string | undefined, fallback: string): string {
  const cleaned = (name ?? "").replace(/[\\/]/g, "").trim().slice(0, 100);
  return cleaned || fallback;
}

async function downloadCsv(endpoint: string, fallbackName: string): Promise<void> {
  const res = await fetchRaw(endpoint);
  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^"]+)"?/.exec(disposition);
  const name = sanitizeFilename(match?.[1], fallbackName);
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
  const qs = toQuery(filters);
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

// Owner-only activity/audit log export. Honours the same action-prefix, q,
// actor, date-range and include_archived filters as the /activity listing
// (backend routes_logs), so a filtered view exports what you see.
export function exportAuditLogCsv(filters: Omit<ActivityLogFilters, "limit"> = {}): Promise<void> {
  const qs = exportParams({
    action: filters.action,
    include_archived: filters.includeArchived,
    q: filters.q,
    actor: filters.actor,
    date_from: filters.dateFrom,
    date_to: filters.dateTo,
  });
  return downloadCsv(`api/logs/audit/export.csv${qs}`, "audit-log.csv");
}

// --- Child allowance (backlog #82) ---

export interface AllowanceItem {
  id: number;
  as_of_date: string;
  description: string | null;
  category_id: number | null;
  category_name: string | null;
  amount: string;
  currency: string;
  transaction_id: number | null;
}

export interface ChildBudgetStatus {
  budget_id: number;
  name: string;
  category_id: number | null;
  period: string;
  currency: string;
  amount: string;
  spent: string;
  remaining: string;
  percent: number;
  status: string;
  period_start: string;
  period_end: string;
}

export interface AllowanceSummary {
  user_id: number;
  display_name: string;
  currency: string;
  budgets: ChildBudgetStatus[];
  savings: { total_savings: string; accounts: SavingsAccount[]; goals: SavingsGoal[] };
  items: AllowanceItem[];
}

// Pass userId only as a parent (owner/member) to view a child; a child always
// gets their own regardless.
export function getAllowanceSummary(userId?: number): Promise<AllowanceSummary> {
  return fetchJson<AllowanceSummary>(userId ? `api/allowance/summary?user_id=${userId}` : "api/allowance/summary");
}

export interface AllocationInput {
  child_id: number;
  transaction_id?: number;
  split_id?: number;
  category_id?: number | null;
  amount?: string;
  description?: string;
  as_of?: string;
}

export function createAllocation(data: AllocationInput): Promise<AllowanceItem> {
  return fetchJson<AllowanceItem>("api/allowance/allocations", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function deleteAllocation(id: number): Promise<void> {
  return fetchJson(`api/allowance/allocations/${id}`, { method: "DELETE" });
}

// --- Accounts (shared vs private; backlog #66/#82) ---

export interface Account {
  id: number;
  name: string;
  institution: string | null;
  account_type: string;
  currency: string;
  is_active: boolean;
  owner_user_id: number | null;
  owner_name: string | null;
  is_shared: boolean;
  is_private: boolean;
  in_use: boolean; // has transactions/snapshots → can only be merged, not deleted
}

// Account types the backend accepts (mirrors schemas/accounts.ACCOUNT_TYPES).
export const ACCOUNT_TYPES = [
  "current_account", "debit_card", "credit_card", "savings", "loan", "mortgage",
  "cash", "investment", "pension", "other",
] as const;

export function listAccounts(): Promise<Account[]> {
  return fetchJson<Account[]>("api/accounts");
}

export function createAccount(payload: {
  name: string;
  account_type?: string;
  currency?: string;
  institution?: string;
  owner_user_id?: number | null;
  is_shared?: boolean;
}): Promise<Account> {
  return fetchJson<Account>("api/accounts", { method: "POST", body: JSON.stringify(payload) });
}

export function updateAccount(
  id: number,
  patch: { name?: string; account_type?: string; is_shared?: boolean; owner_user_id?: number | null },
): Promise<Account> {
  return fetchJson<Account>(`api/accounts/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
}

export function deleteAccount(id: number): Promise<{ deleted: boolean; id: number }> {
  return fetchJson<{ deleted: boolean; id: number }>(`api/accounts/${id}`, { method: "DELETE" });
}

export function mergeAccount(id: number, targetId: number): Promise<Account> {
  return fetchJson<Account>(`api/accounts/${id}/merge`, {
    method: "POST",
    body: JSON.stringify({ target_id: targetId }),
  });
}
