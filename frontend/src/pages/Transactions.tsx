import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  attachTransactionReceipt,
  bulkUpdateTransactions,
  categoriseTransaction,
  createVendorFromTransaction,
  exportTransactionsCsv,
  getAiStatus,
  getSettings,
  listCategories,
  listMembers,
  listProjects,
  listTransactionReceipts,
  listTransactions,
  listVendors,
  recategorise,
  receiptFileUrl,
  setTransactionTags,
  unarchiveTransaction,
  updateTransaction,
  type BulkUpdate,
  type Transaction,
  type TransactionFilters,
} from "../api/client";
import { formatDate, normaliseDateFormat } from "../lib/date";
import SplitEditor from "../components/SplitEditor";
import CountrySelect from "../components/CountrySelect";
import AiBatchPanel from "../components/AiBatchPanel";
import CloudAiBatchPanel from "../components/CloudAiBatchPanel";
import AssignToChildButton from "../components/AssignToChildButton";
import ReceiptPreview from "../components/ReceiptPreview";
import { useAlert, useConfirm, usePrompt } from "../components/dialogs";
import { useResizableColumns, type ColumnDef } from "../useResizableColumns";
import { suggestForTransaction } from "../lib/aiSuggest";
import { recommendedVendorName } from "../lib/vendorSignature";
import { useOptimisticSelect } from "../hooks/useOptimisticSelect";

const PAGE_SIZE = 50;

// The country the backend inferred for a row (txn -> vendor -> default -> currency,
// geo.country_for) when it has no stored `country`. Shown as the picker's default so
// a base-currency row (e.g. GBP -> GB) resolves instead of showing "—". Never persisted;
// picking a country still writes `country` on explicit change only.
function resolvedCountry(t: Transaction): string | null {
  return (t as Transaction & { resolved_country?: string | null }).resolved_country ?? null;
}

// Transactions table columns, in render order (backlog: resizable columns). The
// select checkbox is fixed-width; the rest can be dragged and persist per device.
const COLUMNS: ColumnDef[] = [
  { key: "select", width: 40, resizable: false },
  { key: "date", width: 110 },
  { key: "description", width: 320 },
  { key: "amount", width: 140 },
  { key: "category", width: 170 },
  { key: "project", width: 130 },
  { key: "flags", width: 170 },
];

// Quick date-range presets for the Transactions filter (local-time safe — avoids
// the UTC shift of toISOString).
function isoDay(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function presetRange(key: string): [string, string] {
  const now = new Date();
  const y = now.getFullYear();
  const m = now.getMonth();
  if (key === "this-month") return [isoDay(new Date(y, m, 1)), isoDay(new Date(y, m + 1, 0))];
  if (key === "last-month") return [isoDay(new Date(y, m - 1, 1)), isoDay(new Date(y, m, 0))];
  if (key === "year") return [isoDay(new Date(y, 0, 1)), isoDay(new Date(y, 11, 31))];
  const from = new Date(now); // last 90 days
  from.setDate(from.getDate() - 90);
  return [isoDay(from), isoDay(now)];
}

// Filter-value normalisers: empty controls drop out of the query. Defined at
// module level so these increments don't inflate the Transactions component.
const blank = (v: string): string | undefined => v || undefined;
const flag = (v: boolean): true | undefined => v || undefined;
const numOrUndef = (v: string): number | undefined => (v ? Number(v) : undefined);

export default function Transactions() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const prompt = usePrompt();
  const alert = useAlert();
  // Deep-link (Review Queue "Open transaction →", trip drill-down): when a
  // ?focus=<id> is present we narrow the list to *just that one transaction* so
  // it's always surfaced — the previous highlight-only approach silently failed
  // when the row fell on a different page of the paginated list.
  const [searchParams, setSearchParams] = useSearchParams();
  const focusId = searchParams.get("focus");
  const focusNum = focusId ? Number(focusId) : undefined;
  // The filter controls seed from the URL once, so a drill-down link from the
  // Dashboard/Vendors/etc. (e.g. ?category_id=5&date_from=…) arrives pre-filtered
  // and the matching control reflects it — and stays fully editable from here.
  const [search, setSearch] = useState(() => searchParams.get("search") ?? "");
  // The query uses a debounced copy of `search` so we fire one request after the
  // user pauses typing, not one per keystroke (backlog #43). The input below stays
  // bound to `search` for instant feedback.
  const [debouncedSearch, setDebouncedSearch] = useState(search);
  const [dateFrom, setDateFrom] = useState(() => searchParams.get("date_from") ?? "");
  const [dateTo, setDateTo] = useState(() => searchParams.get("date_to") ?? "");
  const [needsReview, setNeedsReview] = useState(() => searchParams.get("needs_review") === "true");
  const [uncategorisedOnly, setUncategorisedOnly] = useState(() => searchParams.get("uncategorised") === "true");
  const [showArchived, setShowArchived] = useState(() => searchParams.get("include_archived") === "true");
  const [businessOnly, setBusinessOnly] = useState(() => searchParams.get("is_business") === "true");
  const [projectFilter, setProjectFilter] = useState(() => searchParams.get("project_id") ?? "");
  const [memberFilter, setMemberFilter] = useState(() => searchParams.get("member_id") ?? "");
  const [categoryFilter, setCategoryFilter] = useState(() => searchParams.get("category_id") ?? "");
  const [vendorFilter, setVendorFilter] = useState(() => searchParams.get("vendor_id") ?? "");
  const [countryFilter, setCountryFilter] = useState(() => (searchParams.get("country") ?? "").toUpperCase());
  const [page, setPage] = useState(() => Number(searchParams.get("page")) || 0);
  const [splitId, setSplitId] = useState<number | null>(null);
  // Which row's detail panel is expanded (click a row to edit it). The focused
  // deep-link row opens automatically.
  const [openId, setOpenId] = useState<number | null>(null);
  // Multi-edit: checkbox-selected transaction ids + a bulk-actions bar.
  const [selected, setSelected] = useState<Set<number>>(new Set());
  // Per-device draggable column widths (backlog: resize table columns).
  const cols = useResizableColumns("transactions", COLUMNS);
  const [showAiBatch, setShowAiBatch] = useState(false);
  const [showCloudBatch, setShowCloudBatch] = useState(false);
  const [ruleMsg, setRuleMsg] = useState<string | null>(null);

  const filters: TransactionFilters = focusNum
    ? // Focused: ignore the page filters and fetch only the deep-linked row
      // (include archived so a focused aged-out transaction still shows).
      { transaction_id: focusNum, include_archived: true, limit: 1, offset: 0 }
    : {
        search: blank(debouncedSearch),
        date_from: blank(dateFrom),
        date_to: blank(dateTo),
        needs_review: flag(needsReview),
        uncategorised: flag(uncategorisedOnly),
        is_business: flag(businessOnly),
        category_id: numOrUndef(categoryFilter),
        vendor_id: numOrUndef(vendorFilter),
        country: blank(countryFilter),
        project_id: numOrUndef(projectFilter),
        member_id: numOrUndef(memberFilter),
        include_archived: flag(showArchived),
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      };

  const anyFilterActive = Boolean(
    search || dateFrom || dateTo || needsReview || uncategorisedOnly || businessOnly ||
    showArchived || categoryFilter || vendorFilter || countryFilter || projectFilter || memberFilter,
  );

  // Reset every filter control to its default and drop any drill-down params from
  // the URL (so a refresh doesn't silently re-apply them).
  function clearAllFilters() {
    setSearch(""); setDebouncedSearch(""); setDateFrom(""); setDateTo("");
    setNeedsReview(false); setUncategorisedOnly(false); setBusinessOnly(false); setShowArchived(false);
    setCategoryFilter(""); setVendorFilter(""); setCountryFilter("");
    setProjectFilter(""); setMemberFilter("");
    setPage(0);
    if (searchParams.toString()) setSearchParams({});
  }

  const categories = useQuery({ queryKey: ["categories"], queryFn: listCategories });
  const projects = useQuery({ queryKey: ["projects"], queryFn: listProjects });
  // Build id→name lookups once per data change instead of scanning the arrays
  // for every rendered row.
  const categoryNameById = useMemo(
    () => new Map<number, string>((categories.data ?? []).map((c) => [c.id, c.name])),
    [categories.data],
  );
  const projectNameById = useMemo(
    () => new Map<number, string>((projects.data ?? []).map((p) => [p.id, p.name])),
    [projects.data],
  );
  // Look a name up by id, tolerating a null id (unset) and a missing entry.
  const nameFor = (map: Map<number, string>, id: number | null | undefined): string | null =>
    id != null ? (map.get(id) ?? null) : null;
  const members = useQuery({ queryKey: ["members"], queryFn: listMembers });
  const vendors = useQuery({ queryKey: ["vendors"], queryFn: listVendors });
  const settings = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const aiStatus = useQuery({ queryKey: ["ai-status"], queryFn: getAiStatus });
  const base = settings.data?.base_currency ?? "GBP";
  const dateFmt = normaliseDateFormat(settings.data?.date_format);
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["transactions", filters],
    queryFn: () => listTransactions(filters),
    placeholderData: keepPreviousData,
  });

  // Debounce the search box: wait ~300ms after the last keystroke before the
  // query refetches, so typing "Amazon" fires one request, not six (backlog #43).
  useEffect(() => {
    const id = globalThis.setTimeout(() => setDebouncedSearch(search), 300);
    return () => globalThis.clearTimeout(id);
  }, [search]);

  // Scroll the deep-linked (focused) transaction into view once it's rendered.
  useEffect(() => {
    if (!focusId || !data) return;
    document.getElementById(`txn-row-${focusId}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [focusId, data]);

  // Re-sync the filter controls FROM the URL whenever the query string changes.
  // The seeds above only read the URL once at mount. That covers a fresh mount
  // (arriving from another route re-runs them), but NOT a same-path query change:
  // navigating /transactions?category_id=5 → /transactions?vendor_id=3 keeps this
  // component mounted (React Router preserves the element across search-only
  // changes), so the seed never re-runs and a drill-down link (e.g. a category or
  // vendor chip) would silently fail to apply its filter. This effect keeps the URL
  // the source of truth for the controls in that case. We adopt only params that
  // are PRESENT, so an unrelated navigation can't wipe a manually-set control
  // (matching the seed semantics). The mirror effect below then writes the state
  // back to the URL; the values are already equal, so React bails and it converges
  // — no loop. `focus` is derived per-render (focusId above), so it needs none here.
  useEffect(() => {
    if (searchParams.has("search")) setSearch(searchParams.get("search") ?? "");
    if (searchParams.has("date_from")) setDateFrom(searchParams.get("date_from") ?? "");
    if (searchParams.has("date_to")) setDateTo(searchParams.get("date_to") ?? "");
    if (searchParams.has("needs_review")) setNeedsReview(searchParams.get("needs_review") === "true");
    if (searchParams.has("uncategorised")) setUncategorisedOnly(searchParams.get("uncategorised") === "true");
    if (searchParams.has("include_archived")) setShowArchived(searchParams.get("include_archived") === "true");
    if (searchParams.has("is_business")) setBusinessOnly(searchParams.get("is_business") === "true");
    if (searchParams.has("category_id")) setCategoryFilter(searchParams.get("category_id") ?? "");
    if (searchParams.has("vendor_id")) setVendorFilter(searchParams.get("vendor_id") ?? "");
    if (searchParams.has("country")) setCountryFilter((searchParams.get("country") ?? "").toUpperCase());
    if (searchParams.has("project_id")) setProjectFilter(searchParams.get("project_id") ?? "");
    if (searchParams.has("member_id")) setMemberFilter(searchParams.get("member_id") ?? "");
    if (searchParams.has("page")) setPage(Number(searchParams.get("page")) || 0);
  }, [searchParams]);

  // Mirror the active filters into the URL (replace — no history spam) so a reload
  // restores them and the filtered view stays shareable/bookmarkable. A ?focus=
  // deep-link owns the URL, so skip syncing while focused.
  useEffect(() => {
    if (focusId) return;
    const p: Record<string, string> = {};
    if (search) p.search = search;
    if (dateFrom) p.date_from = dateFrom;
    if (dateTo) p.date_to = dateTo;
    if (needsReview) p.needs_review = "true";
    if (uncategorisedOnly) p.uncategorised = "true";
    if (showArchived) p.include_archived = "true";
    if (businessOnly) p.is_business = "true";
    if (categoryFilter) p.category_id = categoryFilter;
    if (vendorFilter) p.vendor_id = vendorFilter;
    if (countryFilter) p.country = countryFilter;
    if (projectFilter) p.project_id = projectFilter;
    if (memberFilter) p.member_id = memberFilter;
    if (page) p.page = String(page);
    setSearchParams(p, { replace: true });
  }, [
    search, dateFrom, dateTo, needsReview, uncategorisedOnly, showArchived, businessOnly,
    categoryFilter, vendorFilter, countryFilter, projectFilter, memberFilter, page, focusId,
    setSearchParams,
  ]);

  // Selection is scoped to the *visible page*: reset it whenever the user pages
  // or re-filters. Without this the Set silently accumulates ids from earlier
  // pages, so a bulk action (archive/delete/…) could hit rows that scrolled
  // off-screen — acting on more than the user can see. Clearing here keeps the
  // header select-all, the row checkboxes, the bulk-bar count and the bulk
  // mutation all operating on exactly the same loaded set. (Not keyed on `data`
  // so a post-mutation refetch doesn't wipe a selection the user is still using.)
  useEffect(() => {
    setSelected(new Set());
  }, [
    page, debouncedSearch, dateFrom, dateTo, needsReview, uncategorisedOnly,
    showArchived, businessOnly, categoryFilter, vendorFilter, countryFilter,
    projectFilter, memberFilter, focusId,
  ]);

  const setCategory = useMutation({
    mutationFn: (v: { id: number; categoryId: number | null }) =>
      categoriseTransaction(v.id, v.categoryId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["dash-categories"] }); // dashboard breakdown depends on categories
      qc.invalidateQueries({ queryKey: ["dash-vendors"] });
    },
  });

  // "Make rule": keep this category AND create a rule so similar future
  // transactions auto-categorise (spec §15.3).
  const makeRule = useMutation({
    mutationFn: (v: { id: number; categoryId: number }) =>
      categoriseTransaction(v.id, v.categoryId, { learnRule: true }),
    onSuccess: () => {
      setRuleMsg("✓ Rule saved — similar transactions will auto-categorise from now on (see the Rules page).");
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["rules"] });
      qc.invalidateQueries({ queryKey: ["dash-categories"] });
      qc.invalidateQueries({ queryKey: ["dash-vendors"] });
    },
    onError: (e) => setRuleMsg(`Couldn't save rule: ${e instanceof Error ? e.message : e}`),
  });

  const recat = useMutation({
    mutationFn: () => recategorise(true),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["dash-categories"] });
      qc.invalidateQueries({ queryKey: ["dash-vendors"] });
    },
  });

  const unarchive = useMutation({
    mutationFn: (id: number) => unarchiveTransaction(id),
    // Unarchiving re-includes a transaction in aggregates, so refresh the
    // transaction list + the dashboard summaries it feeds (mirrors the other
    // mutations rather than a blanket invalidate).
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["summary"] });
      qc.invalidateQueries({ queryKey: ["dash-categories"] });
      qc.invalidateQueries({ queryKey: ["dash-vendors"] });
    },
    onError: (e) => { alert({ message: String(e instanceof Error ? e.message : e) }); },
  });

  const setBusiness = useMutation({
    mutationFn: (v: { id: number; value: boolean }) =>
      updateTransaction(v.id, { is_business: v.value }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["business-summary"] });
    },
    onError: (e) => { alert({ message: String(e instanceof Error ? e.message : e) }); },
  });

  const setVat = useMutation({
    mutationFn: (v: { id: number; value: string | null }) =>
      updateTransaction(v.id, { vat_amount: v.value }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["business-summary"] });
    },
    onError: (e) => { alert({ message: String(e instanceof Error ? e.message : e) }); },
  });

  // Prompt for a VAT amount (blank clears it).
  async function editVat(t: Transaction) {
    const current = t.vat_amount ?? "";
    const input = await prompt({ title: "VAT amount", message: `VAT amount for this transaction (in ${t.currency}, blank to clear):`, defaultValue: String(current), confirmLabel: "Save" });
    if (input === null) return; // cancelled
    const trimmed = input.trim();
    if (trimmed === "") {
      setVat.mutate({ id: t.id, value: null });
      return;
    }
    if (!/^\d+(\.\d{1,2})?$/.test(trimmed)) {
      await alert({ message: "Enter a number like 4.20 (or blank to clear)." });
      return;
    }
    setVat.mutate({ id: t.id, value: trimmed });
  }

  // Export the ticked selection when some rows are selected, else the whole
  // *filtered* set (the client drops limit/offset so it's not just the page).
  const exportCsv = useMutation({
    mutationFn: () =>
      exportTransactionsCsv(filters, selected.size > 0 ? [...selected] : undefined),
    onError: (e) => { alert({ message: String(e instanceof Error ? e.message : e) }); },
  });

  const setProject = useMutation({
    mutationFn: (v: { id: number; projectId: number | null }) =>
      updateTransaction(v.id, { project_id: v.projectId }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["dashboard-projects"] });
    },
  });

  // Manually assign (or clear) a vendor on a row (spec §15.3).
  const setVendor = useMutation({
    mutationFn: (v: { id: number; vendorId: number | null }) =>
      updateTransaction(v.id, { merchant_id: v.vendorId }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["vendors"] });
      qc.invalidateQueries({ queryKey: ["dash-vendors"] });
    },
  });

  // "Suggest & confirm" vendor recommendation: create a vendor from the recommended
  // name (OCR signature, or an AI-suggested name) and link it to the transaction.
  const createVendor = useMutation({
    mutationFn: (v: { id: number; name?: string }) => createVendorFromTransaction(v.id, v.name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["vendors"] });
      qc.invalidateQueries({ queryKey: ["dash-vendors"] });
    },
    onError: (e) => { alert({ message: String(e instanceof Error ? e.message : e) }); },
  });

  // Set (or clear) a transaction's own country — beats the vendor's country for the
  // spend-by-location map (a vendor like Tesco spans many countries). "" clears.
  const setCountry = useMutation({
    mutationFn: (v: { id: number; country: string }) => updateTransaction(v.id, { country: v.country }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["dash-geo"] });
    },
  });

  // Optimistic overlays for the per-row select-on-change controls (#247): each
  // reflects the chosen value immediately and, if the mutation rejects, snaps the
  // control back to the server value and surfaces the error. One instance per
  // select-kind, keyed by row id (a shared instance would let one field's pending
  // choice mask another's on the same row).
  const surfaceError = (e: unknown) => { alert({ message: String(e instanceof Error ? e.message : e) }); };
  const categorySelect = useOptimisticSelect<number, number | null>(surfaceError);
  const vendorSelect = useOptimisticSelect<number, number | null>(surfaceError);
  const projectSelect = useOptimisticSelect<number, number | null>(surfaceError);
  const countrySelect = useOptimisticSelect<number, string | null>(surfaceError);

  // Multi-edit: apply one change to every selected transaction at once.
  const bulk = useMutation({
    mutationFn: (patch: BulkUpdate) => bulkUpdateTransactions([...selected], patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["vendors"] });
      qc.invalidateQueries({ queryKey: ["dash-vendors"] });
      qc.invalidateQueries({ queryKey: ["dashboard-projects"] });
      qc.invalidateQueries({ queryKey: ["tags"] });
    },
    onError: (e) => { alert({ message: String(e instanceof Error ? e.message : e) }); },
  });

  function applyBulk(patch: BulkUpdate, clearAfter = false) {
    bulk.mutate(patch, { onSuccess: () => { if (clearAfter) setSelected(new Set()); } });
  }

  // Value-setting bulk actions apply to every selected row at once, so confirm
  // first and show the count (destructive Archive/Delete confirm separately).
  async function applyBulkConfirmed(patch: BulkUpdate, label: string, clearAfter = false) {
    if (await confirm({ message: `Apply ${label} to ${selected.size} transaction(s)?`, confirmLabel: "Apply" }))
      applyBulk(patch, clearAfter);
  }

  const toggleSelected = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const setTags = useMutation({
    mutationFn: (v: { id: number; tags: string[] }) => setTransactionTags(v.id, v.tags),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["tags"] });
    },
    onError: (e) => { alert({ message: String(e instanceof Error ? e.message : e) }); },
  });

  // Add a tag via a small prompt, then persist the new full set (spec §18.3).
  async function addTag(t: Transaction) {
    const name = (await prompt({ title: "Add a tag", message: "Add a tag (e.g. reimbursable, work, gift):", confirmLabel: "Add" }))?.trim();
    if (!name) return;
    const current = (t.tags ?? []).map((x) => x.name);
    if (current.some((c) => c.toLowerCase() === name.toLowerCase())) return;
    setTags.mutate({ id: t.id, tags: [...current, name] });
  }
  function removeTag(t: Transaction, name: string) {
    setTags.mutate({ id: t.id, tags: (t.tags ?? []).map((x) => x.name).filter((n) => n !== name) });
  }

  // AI suggests a category — and a country when it can tell (never auto-applies).
  // Cloud-manual mode previews the redacted payload first (spec §22.5). Shared helper.
  async function suggestAi(t: Transaction) {
    try {
      const s = await suggestForTransaction(t.id);
      if (!s) return;
      if (s.categoryId != null) setCategory.mutate({ id: t.id, categoryId: s.categoryId }, { onError: surfaceError });
      if (s.country) setCountry.mutate({ id: t.id, country: s.country }, { onError: surfaceError });
      if (s.vendor && !t.merchant_id) createVendor.mutate({ id: t.id, name: s.vendor });
    } catch (e) {
      await alert({ message: String(e instanceof Error ? e.message : e) });
    }
  }

  const total = data?.total ?? 0;
  const maxPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);

  // Header select-all state, computed against the currently loaded page only.
  const pageCount = data?.items.length ?? 0;
  const selectedOnPage = useMemo(
    () => (data?.items ?? []).filter((t) => selected.has(t.id)).length,
    [data, selected],
  );
  const pageAllSelected = pageCount > 0 && selectedOnPage === pageCount;
  const pageSomeSelected = selectedOnPage > 0 && selectedOnPage < pageCount;

  // Export button copy: mid-export, "Export N selected" when rows are ticked,
  // else "Export all (filtered)". Precomputed so the JSX stays a single ternary.
  const exportIdle = selected.size > 0 ? `⬇ Export ${selected.size} selected` : "⬇ Export CSV";
  const exportLabel = exportCsv.isPending ? "Exporting…" : exportIdle;
  const exportTitle =
    selected.size > 0
      ? "Download the selected transactions as CSV"
      : "Download these transactions as CSV (honours the filters below)";

  // Select/clear every row on the current page (indeterminate → select-all).
  const toggleAllOnPage = (checked: boolean) =>
    setSelected((prev) => {
      const next = new Set(prev);
      for (const t of data?.items ?? []) {
        if (checked) next.add(t.id);
        else next.delete(t.id);
      }
      return next;
    });

  return (
    <div className="page">
      <div className="page__head">
        <h1 className="page__title">Transactions</h1>
        <div style={{ display: "flex", gap: 8 }}>
          {aiStatus.data?.enabled && aiStatus.data?.privacy_mode === "local_llm" && (
            <button className="btn btn--ghost" onClick={() => setShowAiBatch((v) => !v)}>
              {showAiBatch ? "Hide AI categorise" : "✨ AI categorise…"}
            </button>
          )}
          {aiStatus.data?.enabled && aiStatus.data?.is_cloud && (
            <button className="btn btn--ghost" onClick={() => setShowCloudBatch((v) => !v)}>
              {showCloudBatch ? "Hide cloud AI" : "☁️ AI categorise (cloud)…"}
            </button>
          )}
          <button className="btn btn--ghost" disabled={recat.isPending} onClick={() => recat.mutate()}>
            {recat.isPending ? "Re-categorising…" : "Re-categorise uncategorised"}
          </button>
          <button
            className="btn btn--ghost"
            disabled={exportCsv.isPending || (data?.total ?? 0) === 0}
            title={exportTitle}
            onClick={() => exportCsv.mutate()}
          >
            {exportLabel}
          </button>
          <Link
            className="btn btn--ghost"
            to="/tags"
            title="Merge or remove tags (tag housekeeping) on the Tags page"
          >
            🏷 Manage tags
          </Link>
        </div>
      </div>
      {recat.isSuccess && (
        <p className="muted">Re-categorised {recat.data.recategorised} transaction(s).</p>
      )}
      {ruleMsg && <p className="status status--ok">{ruleMsg}</p>}

      {showAiBatch && <AiBatchPanel base={base} onClose={() => setShowAiBatch(false)} />}
      {showCloudBatch && <CloudAiBatchPanel base={base} onClose={() => setShowCloudBatch(false)} />}

      {focusId && (
        <div className="card focus-banner">
          <span>🔎 Showing one transaction (<strong>#{focusId}</strong>) — opened from a link elsewhere in the app.</span>
          <button className="btn btn--ghost" onClick={() => setSearchParams({})}>← Show all transactions</button>
        </div>
      )}

      {!focusId && (
      <div className="card">
        <div className="filters">
          <input
            name="txn-filter-search"
            autoComplete="off"
            placeholder="Search description / merchant"
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(0); }}
          />
          <label>From <input name="txn-filter-date-from" type="date" value={dateFrom} onChange={(e) => { setDateFrom(e.target.value); setPage(0); }} /></label>
          <label>To <input name="txn-filter-date-to" type="date" value={dateTo} onChange={(e) => { setDateTo(e.target.value); setPage(0); }} /></label>
          <span style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            <span className="muted" style={{ fontSize: "0.8rem" }}>Quick:</span>
            {[
              ["this-month", "This month"],
              ["last-month", "Last month"],
              ["last-90", "Last 90 days"],
              ["year", "This year"],
            ].map(([key, label]) => (
              <button
                key={key}
                className="link-btn"
                onClick={() => { const [f, t] = presetRange(key); setDateFrom(f); setDateTo(t); setPage(0); }}
              >
                {label}
              </button>
            ))}
            {(dateFrom || dateTo) && (
              <button className="link-btn" onClick={() => { setDateFrom(""); setDateTo(""); setPage(0); }}>Clear</button>
            )}
          </span>
          <label>Category{" "}
            <select name="txn-filter-category" value={categoryFilter} onChange={(e) => { setCategoryFilter(e.target.value); setPage(0); }}>
              <option value="">All</option>
              {categories.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          <label>Vendor{" "}
            <select name="txn-filter-vendor" value={vendorFilter} onChange={(e) => { setVendorFilter(e.target.value); setPage(0); }}>
              <option value="">All</option>
              {vendors.data?.map((v) => (
                <option key={v.id} value={v.id}>{v.display_name ?? v.canonical_name}</option>
              ))}
            </select>
          </label>
          <label>Project{" "}
            <select name="txn-filter-project" value={projectFilter} onChange={(e) => { setProjectFilter(e.target.value); setPage(0); }}>
              <option value="">All</option>
              {projects.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
          {countryFilter && (
            <span className="chip-toggle" title="Filtered to one country (from the spend-by-location map)">
              <span>📍 {countryFilter}</span>
              <button className="link-btn" onClick={() => { setCountryFilter(""); setPage(0); }}>✕</button>
            </span>
          )}
          {(members.data?.length ?? 0) > 1 && (
            <label title="Show one household member's own-account transactions">Member{" "}
              <select name="txn-filter-member" value={memberFilter} onChange={(e) => { setMemberFilter(e.target.value); setPage(0); }}>
                <option value="">All members</option>
                {members.data?.map((m) => <option key={m.id} value={m.id}>{m.display_name}</option>)}
              </select>
            </label>
          )}
        </div>
        <div className="filter-toggles">
          <span className="filter-toggles__label">Show only</span>
          <label className="chip-toggle" title="Transactions that have no category assigned yet">
            <input type="checkbox" checked={uncategorisedOnly} onChange={(e) => { setUncategorisedOnly(e.target.checked); setPage(0); }} />
            <span>Uncategorised</span>
          </label>
          <label className="chip-toggle" title="Transactions flagged for you to check (low-confidence or imported from a PDF/photo)">
            <input type="checkbox" checked={needsReview} onChange={(e) => { setNeedsReview(e.target.checked); setPage(0); }} />
            <span>Needs review</span>
          </label>
          <label className="chip-toggle" title="Transactions marked as a business expense">
            <input type="checkbox" checked={businessOnly} onChange={(e) => { setBusinessOnly(e.target.checked); setPage(0); }} />
            <span>Business</span>
          </label>
          <span className="filter-toggles__sep" aria-hidden="true" />
          <label className="chip-toggle" title="Also include archived (aged-out) transactions, which are hidden by default">
            <input type="checkbox" checked={showArchived} onChange={(e) => { setShowArchived(e.target.checked); setPage(0); }} />
            <span>Include archived</span>
          </label>
          {anyFilterActive && (
            <>
              <span className="filter-toggles__sep" aria-hidden="true" />
              <button className="link-btn" onClick={clearAllFilters}>✕ Clear all filters</button>
            </>
          )}
        </div>
        {needsReview && !uncategorisedOnly && (data?.total ?? 0) === 0 && (
          <p className="muted filter-hint">
            Nothing is flagged for review. Looking for transactions without a category?
            {" "}
            <button className="link-btn" onClick={() => { setNeedsReview(false); setUncategorisedOnly(true); setPage(0); }}>
              Show uncategorised
            </button>
            {" "}instead.
          </p>
        )}
      </div>
      )}

      <div className="card">
        {isLoading && <p className="muted">Loading…</p>}
        {isError && <p className="status status--error">{String(error)}</p>}
        {data?.items.length === 0 && (
          focusId ? (
            <p className="muted">
              Transaction #{focusId} wasn't found — it may have been deleted or isn't visible to you.{" "}
              <button className="link-btn" onClick={() => setSearchParams({})}>Show all transactions</button>.
            </p>
          ) : (
            <p className="muted">
              No transactions. Import a CSV on the <strong>Import</strong> page to get started.
            </p>
          )
        )}
        {data && data.items.length > 0 && (
          <>
            {selected.size > 0 && !focusId && (
              <div className="bulk-bar">
                <strong>{selected.size} selected</strong>
                <select
                  value=""
                  title="Set category for selected"
                  onChange={(e) => {
                    const v = e.target.value;
                    if (v) applyBulkConfirmed({ category_id: v === "__none" ? null : Number(v) }, v === "__none" ? "clear category" : "this category");
                  }}
                >
                  <option value="">Set category…</option>
                  <option value="__none">— clear —</option>
                  {categories.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
                <select
                  value=""
                  title="Set project for selected"
                  onChange={(e) => {
                    const v = e.target.value;
                    if (v) applyBulkConfirmed({ project_id: v === "__none" ? null : Number(v) }, v === "__none" ? "clear project" : "this project");
                  }}
                >
                  <option value="">Set project…</option>
                  <option value="__none">— clear —</option>
                  {projects.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
                <CountrySelect
                  value={null}
                  onChange={(code) => { if (code) applyBulkConfirmed({ country: code }, "this country"); }}
                  title="Set the country of the selected transactions (spend-by-location map)"
                  style={{ minWidth: 150 }}
                />
                <button
                  className="btn btn--sm btn--ghost"
                  onClick={async () => {
                    const t = (await prompt({ title: "Add a tag", message: `Add a tag to the ${selected.size} selected transactions:`, confirmLabel: "Add" }))?.trim();
                    if (t) applyBulk({ add_tag: t });
                  }}
                >
                  + tag
                </button>
                <button className="btn btn--sm btn--ghost" onClick={() => applyBulkConfirmed({ is_business: true }, "Business = yes")}>
                  Mark business
                </button>
                <button className="btn btn--sm btn--ghost" onClick={() => applyBulkConfirmed({ is_business: false }, "Business = no")}>
                  Unmark
                </button>
                <button
                  className="btn btn--sm btn--ghost"
                  onClick={async () => {
                    if (await confirm({ message: `Archive ${selected.size} transaction(s)? They're hidden from totals (reversible).`, confirmLabel: "Archive" }))
                      applyBulk({ archive: true }, true);
                  }}
                >
                  Archive
                </button>
                <button
                  className="btn btn--sm btn--ghost"
                  onClick={async () => {
                    if (await confirm({ message: `Permanently delete ${selected.size} transaction(s)? This can't be undone.`, confirmLabel: "Delete", danger: true }))
                      applyBulk({ delete: true }, true);
                  }}
                >
                  Delete
                </button>
                <button className="link-btn" onClick={() => setSelected(new Set())}>Clear</button>
              </div>
            )}
            <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 4 }}>
              <button className="link-btn" onClick={cols.reset} title="Reset column widths to default">
                ↔ Reset columns
              </button>
            </div>
            <div className="table-wrap">
              <table className="table table--resizable">
                <colgroup>
                  {COLUMNS.map((c) => (
                    <col key={c.key} style={{ width: cols.widths[c.key] }} />
                  ))}
                </colgroup>
                <thead>
                  <tr>
                    <th className="col-select">
                      <SelectAllCheckbox
                        allSelected={pageAllSelected}
                        someSelected={pageSomeSelected}
                        onToggle={toggleAllOnPage}
                      />
                    </th>
                    <ResizableTh col="date" cols={cols}>Date</ResizableTh>
                    <ResizableTh col="description" cols={cols}>Description</ResizableTh>
                    <ResizableTh col="amount" cols={cols} className="num">Amount</ResizableTh>
                    <ResizableTh col="category" cols={cols}>Category</ResizableTh>
                    <ResizableTh col="project" cols={cols}>Project</ResizableTh>
                    <ResizableTh col="flags" cols={cols}>Flags &amp; tags</ResizableTh>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((t) => {
                    const isOpen = openId === t.id || String(t.id) === focusId;
                    const catName = t.is_split ? "Split" : nameFor(categoryNameById, t.category_id);
                    const projName = nameFor(projectNameById, t.project_id);
                    return (
                    <Fragment key={t.id}>
                    <tr
                      id={`txn-row-${t.id}`}
                      className={
                        "txn-row" +
                        (String(t.id) === focusId ? " tr--focus" : "") +
                        (isOpen ? " txn-row--open" : "")
                      }
                      style={t.archived_at ? { opacity: 0.6 } : undefined}
                    >
                      <td className="col-select">
                        <input
                          type="checkbox"
                          checked={selected.has(t.id)}
                          onChange={() => toggleSelected(t.id)}
                        />
                      </td>
                      <td>{formatDate(t.transaction_date, dateFmt)}</td>
                      <td>
                        <button
                          className="link-btn txn-row__toggle"
                          title="Show details / edit this transaction"
                          onClick={() => setOpenId(isOpen ? null : t.id)}
                        >
                          {isOpen ? "▾ " : "▸ "}{t.description_raw}
                        </button>
                        {t.merchant_raw && t.merchant_raw !== t.description_raw && (
                          <span className="muted"> · {t.merchant_raw}</span>
                        )}
                      </td>
                      <td className={"num " + (t.direction === "credit" ? "amt--pos" : "amt--neg")}>
                        {t.amount}
                        {t.currency !== base && <span className="muted"> {t.currency}</span>}
                        {t.currency !== base && t.needs_rate && (
                          <span className="tag tag--dup">rate?</span>
                        )}
                        {t.currency !== base && !t.needs_rate && t.base_amount && (
                          <div className="muted" style={{ fontSize: "0.78rem" }}>
                            ≈ {t.base_amount} {base}
                          </div>
                        )}
                      </td>
                      <td className={catName ? undefined : "muted"}>
                        {catName ?? "— uncategorised —"}
                        {!catName && aiStatus.data?.enabled && (
                          <button
                            className="link-btn"
                            style={{ marginLeft: 6 }}
                            title="Ask the AI assistant to suggest a category"
                            onClick={() => suggestAi(t)}
                          >
                            ✨ suggest
                          </button>
                        )}
                      </td>
                      <td className={projName ? undefined : "muted"}>{projName ?? "—"}</td>
                      <td>
                        {t.is_split && <span className="tag">split</span>}
                        {t.is_transfer && <span className="tag">transfer</span>}
                        {t.is_income && <span className="tag">income</span>}
                        {t.needs_review && <span className="tag tag--dup">review</span>}
                        {t.is_business && <span className="tag" title="Business expense">💼</span>}
                        {t.archived_at && (
                          <span className="tag tag--dup" title="Aged out by data retention — hidden from totals">
                            archived
                          </span>
                        )}
                        {(t.tags ?? []).map((tag) => (
                          <span key={tag.id} className="tag" style={{ background: tag.colour ?? undefined }}>
                            {tag.name}
                          </span>
                        ))}
                      </td>
                    </tr>
                    {isOpen && (
                      <tr className="txn-detail">
                        <td colSpan={7}>
                          <div className="txn-detail__grid">
                            <div className="txn-detail__field">
                              <span>Category</span>
                              {t.is_split ? (
                                <span className="muted">Split across categories — edit the split below.</span>
                              ) : (
                                <span className="txn-detail__row">
                                  <select
                                    className={t.category_id ? "" : "select--empty"}
                                    value={categorySelect.valueFor(t.id, t.category_id ?? null) ?? ""}
                                    onChange={(e) => {
                                      const categoryId = e.target.value ? Number(e.target.value) : null;
                                      categorySelect.choose(t.id, categoryId, () =>
                                        setCategory.mutateAsync({ id: t.id, categoryId }),
                                      );
                                    }}
                                  >
                                    <option value="">— uncategorised —</option>
                                    {categories.data?.map((c) => (
                                      <option key={c.id} value={c.id}>{c.name}</option>
                                    ))}
                                  </select>
                                  {t.category_id !== null && (
                                    <button
                                      className="link-btn"
                                      title="Create a rule so similar transactions auto-categorise"
                                      onClick={() => makeRule.mutate({ id: t.id, categoryId: t.category_id! })}
                                    >
                                      + rule
                                    </button>
                                  )}
                                  {t.category_id === null && aiStatus.data?.enabled && (
                                    <button
                                      className="link-btn"
                                      title="Ask the AI assistant to suggest a category"
                                      onClick={() => suggestAi(t)}
                                    >
                                      ✨ suggest
                                    </button>
                                  )}
                                </span>
                              )}
                            </div>
                            <div className="txn-detail__field">
                              <span>Vendor</span>
                              <select
                                className={t.merchant_id ? "" : "select--empty"}
                                value={vendorSelect.valueFor(t.id, t.merchant_id ?? null) ?? ""}
                                onChange={(e) => {
                                  const vendorId = e.target.value ? Number(e.target.value) : null;
                                  vendorSelect.choose(t.id, vendorId, () =>
                                    setVendor.mutateAsync({ id: t.id, vendorId }),
                                  );
                                }}
                              >
                                <option value="">— none —</option>
                                {vendors.data?.map((v) => (
                                  <option key={v.id} value={v.id}>{v.display_name || v.canonical_name}</option>
                                ))}
                              </select>
                              {!t.merchant_id && (() => {
                                const rec = recommendedVendorName(t.merchant_raw, t.description_raw);
                                return rec ? (
                                  <button
                                    type="button"
                                    className="link-btn"
                                    disabled={createVendor.isPending}
                                    title={`Create the vendor "${rec}" and link it to this transaction`}
                                    onClick={() => createVendor.mutate({ id: t.id, name: rec })}
                                  >
                                    ➕ Create vendor “{rec}”
                                  </button>
                                ) : null;
                              })()}
                            </div>
                            <div className="txn-detail__field">
                              <span>Country</span>
                              <CountrySelect
                                value={countrySelect.valueFor(t.id, t.country ?? resolvedCountry(t))}
                                onChange={(code) =>
                                  countrySelect.choose(t.id, code, () =>
                                    setCountry.mutateAsync({ id: t.id, country: code ?? "" }),
                                  )
                                }
                                title="This transaction's country for the spend-by-location map (overrides the vendor's)"
                              />
                            </div>
                            <div className="txn-detail__field">
                              <span>Project</span>
                              <select
                                className={t.project_id ? "" : "select--empty"}
                                value={projectSelect.valueFor(t.id, t.project_id ?? null) ?? ""}
                                onChange={(e) => {
                                  const projectId = e.target.value ? Number(e.target.value) : null;
                                  projectSelect.choose(t.id, projectId, () =>
                                    setProject.mutateAsync({ id: t.id, projectId }),
                                  );
                                }}
                              >
                                <option value="">— none —</option>
                                {projects.data?.map((p) => (
                                  <option key={p.id} value={p.id}>{p.name}</option>
                                ))}
                              </select>
                            </div>
                            <div className="txn-detail__field">
                              <span>Tags</span>
                              <span className="txn-detail__row">
                                {(t.tags ?? []).map((tag) => (
                                  <button
                                    key={tag.id}
                                    type="button"
                                    className="tag"
                                    title="Remove tag"
                                    style={{ cursor: "pointer", background: tag.colour ?? undefined, border: "none", font: "inherit" }}
                                    onClick={() => removeTag(t, tag.name)}
                                  >
                                    {tag.name} ✕
                                  </button>
                                ))}
                                <button className="link-btn" onClick={() => addTag(t)}>+ tag</button>
                              </span>
                            </div>
                            <div className="txn-detail__field">
                              <span>Business</span>
                              <BusinessField
                                t={t}
                                onToggle={() => setBusiness.mutate({ id: t.id, value: !t.is_business })}
                                onEditVat={() => editVat(t)}
                              />
                            </div>
                            <div className="txn-detail__field">
                              <span>Actions</span>
                              <span className="txn-detail__row">
                                <button
                                  className="link-btn"
                                  onClick={() => setSplitId(splitId === t.id ? null : t.id)}
                                >
                                  {t.is_split ? "edit split" : "split"}
                                </button>
                                <AssignToChildButton txn={t} base={base} />
                                {t.archived_at && (
                                  <button
                                    className="link-btn"
                                    disabled={unarchive.isPending}
                                    onClick={() => unarchive.mutate(t.id)}
                                  >
                                    unarchive
                                  </button>
                                )}
                              </span>
                            </div>

                            <div className="txn-detail__field">
                              <span>Receipt</span>
                              <ReceiptsField txnId={t.id} />
                            </div>
                          </div>
                          {splitId === t.id && (
                            <div style={{ marginTop: 10 }}>
                              <SplitEditor
                                txnId={t.id}
                                amount={t.amount}
                                currency={t.currency}
                                isSplit={t.is_split}
                                categories={categories.data ?? []}
                                onDone={() => setSplitId(null)}
                              />
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                    </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {!focusId && (
              <div className="pager">
                <button className="btn btn--ghost" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
                  ← Prev
                </button>
                <span className="muted">{total} total · page {page + 1} of {maxPage + 1}</span>
                <button className="btn btn--ghost" disabled={page >= maxPage} onClick={() => setPage((p) => p + 1)}>
                  Next →
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// Header "select all on this page" checkbox. React has no `indeterminate` prop,
// so the DOM property is set imperatively via a ref: unchecked when none of the
// page's rows are selected, checked when all are, and indeterminate when only
// some are. Clicking while none/some are selected selects the whole page;
// clicking while all are selected clears it (native checkbox semantics).
function SelectAllCheckbox({
  allSelected,
  someSelected,
  onToggle,
}: Readonly<{
  allSelected: boolean;
  someSelected: boolean;
  onToggle: (checked: boolean) => void;
}>) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = someSelected;
  }, [someSelected]);
  return (
    <input
      ref={ref}
      type="checkbox"
      title="Select all on this page"
      checked={allSelected}
      onChange={(e) => onToggle(e.target.checked)}
    />
  );
}

// A header cell with a drag handle on its right edge for resizing (backlog).
function ResizableTh({
  col,
  cols,
  className,
  children,
}: Readonly<{
  col: string;
  cols: ReturnType<typeof useResizableColumns>;
  className?: string;
  children: ReactNode;
}>) {
  return (
    <th className={className}>
      {children}
      <span className="col-resize" title="Drag to resize" onMouseDown={(e) => cols.startResize(col, e)} onTouchStart={(e) => cols.startResize(col, e)} />
    </th>
  );
}

// The expanded-row "Business" control: toggle the business flag and, once set,
// edit the VAT amount. Extracted into its own component so the transaction-row
// render callback stays under the cognitive-complexity budget.
function BusinessField({
  t,
  onToggle,
  onEditVat,
}: Readonly<{
  t: Transaction;
  onToggle: () => void;
  onEditVat: () => void;
}>) {
  return (
    <span className="txn-detail__row">
      <button
        className="link-btn"
        title={t.is_business ? "Unmark as business" : "Mark as a business expense"}
        onClick={onToggle}
      >
        {t.is_business ? "✓ business" : "mark business"}
      </button>
      {t.is_business && (
        <button className="link-btn" onClick={onEditVat}>
          {t.vat_amount ? `VAT ${t.vat_amount}` : "set VAT"}
        </button>
      )}
    </span>
  );
}

// Attach a receipt image/PDF to a transaction and view what's attached (the
// original is kept so it stays viewable). Drill-down receipt viewer.
function ReceiptsField({ txnId }: Readonly<{ txnId: number }>) {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["txn-receipts", txnId], queryFn: () => listTransactionReceipts(txnId) });
  const attach = useMutation({
    mutationFn: (file: File) => attachTransactionReceipt(txnId, file),
    onSuccess: () => {
      setErr(null);
      qc.invalidateQueries({ queryKey: ["txn-receipts", txnId] });
      qc.invalidateQueries({ queryKey: ["transactions"] });
    },
    onError: (e) => setErr(String(e instanceof Error ? e.message : e)),
  });
  const receipts = q.data ?? [];
  const [preview, setPreview] = useState<{ id: number; name: string | null } | null>(null);
  return (
    <span className="txn-detail__row" style={{ flexDirection: "column", alignItems: "flex-start", gap: 6 }}>
      {receipts.map((r) => (
        <span key={r.id} style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {r.has_file ? (
            <button className="link-btn" title="Preview in a popup" onClick={() => setPreview({ id: r.id, name: r.source_filename })}>
              🧾 {r.source_filename || `receipt #${r.id}`}
            </button>
          ) : (
            <span className="muted">🧾 {r.source_filename || `receipt #${r.id}`} (original removed)</span>
          )}
          {r.total_amount && <span className="muted">· {r.total_amount}</span>}
        </span>
      ))}
      {preview && (
        <ReceiptPreview url={receiptFileUrl(preview.id)} filename={preview.name} onClose={() => setPreview(null)} />
      )}
      {receipts.length === 0 && <span className="muted">No receipt attached.</span>}
      <label className="link-btn" style={{ cursor: "pointer" }}>
        {attach.isPending ? "Uploading…" : "+ Attach receipt"}
        <input
          type="file"
          accept="image/*,application/pdf"
          style={{ display: "none" }}
          disabled={attach.isPending}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) attach.mutate(f);
            e.target.value = ""; // allow re-selecting the same file
          }}
        />
      </label>
      {err && <span className="status status--error">{err}</span>}
    </span>
  );
}
