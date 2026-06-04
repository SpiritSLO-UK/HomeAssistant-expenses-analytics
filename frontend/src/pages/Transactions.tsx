import { Fragment, useEffect, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  approveAiRequest,
  attachTransactionReceipt,
  bulkUpdateTransactions,
  categoriseTransaction,
  classifyWithAi,
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
import SplitEditor from "../components/SplitEditor";
import AiBatchPanel from "../components/AiBatchPanel";
import CloudAiBatchPanel from "../components/CloudAiBatchPanel";
import AssignToChildButton from "../components/AssignToChildButton";
import { useResizableColumns, type ColumnDef } from "../useResizableColumns";

const PAGE_SIZE = 50;

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

export default function Transactions() {
  const qc = useQueryClient();
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
  const [page, setPage] = useState(0);
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
        search: search || undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        needs_review: needsReview || undefined,
        uncategorised: uncategorisedOnly || undefined,
        is_business: businessOnly || undefined,
        category_id: categoryFilter ? Number(categoryFilter) : undefined,
        vendor_id: vendorFilter ? Number(vendorFilter) : undefined,
        country: countryFilter || undefined,
        project_id: projectFilter ? Number(projectFilter) : undefined,
        member_id: memberFilter ? Number(memberFilter) : undefined,
        include_archived: showArchived || undefined,
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
    setSearch(""); setDateFrom(""); setDateTo("");
    setNeedsReview(false); setUncategorisedOnly(false); setBusinessOnly(false); setShowArchived(false);
    setCategoryFilter(""); setVendorFilter(""); setCountryFilter("");
    setProjectFilter(""); setMemberFilter("");
    setPage(0);
    if (searchParams.toString()) setSearchParams({});
  }

  const categories = useQuery({ queryKey: ["categories"], queryFn: listCategories });
  const projects = useQuery({ queryKey: ["projects"], queryFn: listProjects });
  const members = useQuery({ queryKey: ["members"], queryFn: listMembers });
  const vendors = useQuery({ queryKey: ["vendors"], queryFn: listVendors });
  const settings = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const aiStatus = useQuery({ queryKey: ["ai-status"], queryFn: getAiStatus });
  const base = settings.data?.base_currency ?? "GBP";
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["transactions", filters],
    queryFn: () => listTransactions(filters),
    placeholderData: keepPreviousData,
  });

  // Scroll the deep-linked (focused) transaction into view once it's rendered.
  useEffect(() => {
    if (!focusId || !data) return;
    document.getElementById(`txn-row-${focusId}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [focusId, data]);

  const setCategory = useMutation({
    mutationFn: (v: { id: number; categoryId: number | null }) =>
      categoriseTransaction(v.id, v.categoryId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["transactions"] }),
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
    },
    onError: (e) => setRuleMsg(`Couldn't save rule: ${e instanceof Error ? e.message : e}`),
  });

  const recat = useMutation({
    mutationFn: () => recategorise(true),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["transactions"] }),
  });

  const unarchive = useMutation({
    mutationFn: (id: number) => unarchiveTransaction(id),
    onSuccess: () => qc.invalidateQueries(),
  });

  const setBusiness = useMutation({
    mutationFn: (v: { id: number; value: boolean }) =>
      updateTransaction(v.id, { is_business: v.value }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["business-summary"] });
    },
  });

  const setVat = useMutation({
    mutationFn: (v: { id: number; value: string | null }) =>
      updateTransaction(v.id, { vat_amount: v.value }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["business-summary"] });
    },
  });

  // Prompt for a VAT amount (blank clears it).
  function editVat(t: Transaction) {
    const current = t.vat_amount ?? "";
    const input = globalThis.prompt(`VAT amount for this transaction (in ${t.currency}, blank to clear):`, current);
    if (input === null) return; // cancelled
    const trimmed = input.trim();
    if (trimmed === "") {
      setVat.mutate({ id: t.id, value: null });
      return;
    }
    if (!/^\d+(\.\d{1,2})?$/.test(trimmed)) {
      globalThis.alert("Enter a number like 4.20 (or blank to clear).");
      return;
    }
    setVat.mutate({ id: t.id, value: trimmed });
  }

  // Export the *filtered* set (the client drops limit/offset so it's not just
  // the current page).
  const exportCsv = useMutation({
    mutationFn: () => exportTransactionsCsv(filters),
    onError: (e) => globalThis.alert(String(e instanceof Error ? e.message : e)),
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
    onError: (e) => globalThis.alert(String(e instanceof Error ? e.message : e)),
  });

  function applyBulk(patch: BulkUpdate, clearAfter = false) {
    bulk.mutate(patch, { onSuccess: () => { if (clearAfter) setSelected(new Set()); } });
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
  });

  // Add a tag via a small prompt, then persist the new full set (spec §18.3).
  function addTag(t: Transaction) {
    const name = globalThis.prompt("Add a tag (e.g. reimbursable, work, gift):")?.trim();
    if (!name) return;
    const current = (t.tags ?? []).map((x) => x.name);
    if (current.some((c) => c.toLowerCase() === name.toLowerCase())) return;
    setTags.mutate({ id: t.id, tags: [...current, name] });
  }
  function removeTag(t: Transaction, name: string) {
    setTags.mutate({ id: t.id, tags: (t.tags ?? []).map((x) => x.name).filter((n) => n !== name) });
  }

  // AI suggests a category (never auto-applies). Cloud-manual mode previews the
  // redacted payload and asks for approval first (spec §22.5).
  async function suggestAi(t: Transaction) {
    try {
      let res = await classifyWithAi(t.id);
      if (res.status === "approval_required") {
        const preview = JSON.stringify(res.payload ?? {}, null, 2);
        if (!globalThis.confirm(`Cloud AI needs approval. Only this redacted payload is sent:\n\n${preview}\n\nApprove?`)) return;
        res = await approveAiRequest(res.ai_request_id);
      }
      if (res.status === "ok" && res.category_id) {
        const pct = res.confidence != null ? ` (${Math.round(res.confidence * 100)}%)` : "";
        if (globalThis.confirm(`AI suggests: ${res.category_name}${pct}\n${res.rationale ?? ""}\n\nApply this category?`)) {
          setCategory.mutate({ id: t.id, categoryId: res.category_id });
        }
      } else {
        globalThis.alert("AI couldn't suggest a category for this transaction.");
      }
    } catch (e) {
      globalThis.alert(String(e instanceof Error ? e.message : e));
    }
  }

  const total = data?.total ?? 0;
  const maxPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);

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
            title="Download these transactions as CSV (honours the filters below)"
            onClick={() => exportCsv.mutate()}
          >
            {exportCsv.isPending ? "Exporting…" : "⬇ Export CSV"}
          </button>
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
            placeholder="Search description / merchant"
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(0); }}
          />
          <label>From <input type="date" value={dateFrom} onChange={(e) => { setDateFrom(e.target.value); setPage(0); }} /></label>
          <label>To <input type="date" value={dateTo} onChange={(e) => { setDateTo(e.target.value); setPage(0); }} /></label>
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
          <label>Category
            <select value={categoryFilter} onChange={(e) => { setCategoryFilter(e.target.value); setPage(0); }}>
              <option value="">All</option>
              {categories.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          <label>Vendor
            <select value={vendorFilter} onChange={(e) => { setVendorFilter(e.target.value); setPage(0); }}>
              <option value="">All</option>
              {vendors.data?.map((v) => (
                <option key={v.id} value={v.id}>{v.display_name ?? v.canonical_name}</option>
              ))}
            </select>
          </label>
          <label>Project
            <select value={projectFilter} onChange={(e) => { setProjectFilter(e.target.value); setPage(0); }}>
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
            <label title="Show one household member's own-account transactions">Member
              <select value={memberFilter} onChange={(e) => { setMemberFilter(e.target.value); setPage(0); }}>
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
        {data && data.items.length === 0 && (
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
                    if (v) applyBulk({ category_id: v === "__none" ? null : Number(v) });
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
                    if (v) applyBulk({ project_id: v === "__none" ? null : Number(v) });
                  }}
                >
                  <option value="">Set project…</option>
                  <option value="__none">— clear —</option>
                  {projects.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
                <button
                  className="btn btn--sm btn--ghost"
                  onClick={() => {
                    const t = globalThis.prompt("Add a tag to the selected transactions:")?.trim();
                    if (t) applyBulk({ add_tag: t });
                  }}
                >
                  + tag
                </button>
                <button className="btn btn--sm btn--ghost" onClick={() => applyBulk({ is_business: true })}>
                  Mark business
                </button>
                <button className="btn btn--sm btn--ghost" onClick={() => applyBulk({ is_business: false })}>
                  Unmark
                </button>
                <button
                  className="btn btn--sm btn--ghost"
                  onClick={() => {
                    if (globalThis.confirm(`Archive ${selected.size} transaction(s)? They're hidden from totals (reversible).`))
                      applyBulk({ archive: true }, true);
                  }}
                >
                  Archive
                </button>
                <button
                  className="btn btn--sm btn--ghost"
                  onClick={() => {
                    if (globalThis.confirm(`Permanently delete ${selected.size} transaction(s)? This can't be undone.`))
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
                      <input
                        type="checkbox"
                        title="Select all on this page"
                        checked={data.items.every((t) => selected.has(t.id))}
                        onChange={(e) =>
                          setSelected((prev) => {
                            const next = new Set(prev);
                            for (const t of data.items) {
                              if (e.target.checked) next.add(t.id);
                              else next.delete(t.id);
                            }
                            return next;
                          })
                        }
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
                    const catName = t.is_split
                      ? "Split"
                      : (categories.data?.find((c) => c.id === t.category_id)?.name ?? null);
                    const projName = projects.data?.find((p) => p.id === t.project_id)?.name ?? null;
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
                      <td>{t.transaction_date}</td>
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
                      <td className={catName ? undefined : "muted"}>{catName ?? "— uncategorised —"}</td>
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
                                    value={t.category_id ?? ""}
                                    onChange={(e) =>
                                      setCategory.mutate({
                                        id: t.id,
                                        categoryId: e.target.value ? Number(e.target.value) : null,
                                      })
                                    }
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
                                value={t.merchant_id ?? ""}
                                onChange={(e) =>
                                  setVendor.mutate({
                                    id: t.id,
                                    vendorId: e.target.value ? Number(e.target.value) : null,
                                  })
                                }
                              >
                                <option value="">— none —</option>
                                {vendors.data?.map((v) => (
                                  <option key={v.id} value={v.id}>{v.display_name || v.canonical_name}</option>
                                ))}
                              </select>
                            </div>
                            <div className="txn-detail__field">
                              <span>Project</span>
                              <select
                                className={t.project_id ? "" : "select--empty"}
                                value={t.project_id ?? ""}
                                onChange={(e) =>
                                  setProject.mutate({
                                    id: t.id,
                                    projectId: e.target.value ? Number(e.target.value) : null,
                                  })
                                }
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
                              <span className="txn-detail__row">
                                <button
                                  className="link-btn"
                                  title={t.is_business ? "Unmark as business" : "Mark as a business expense"}
                                  onClick={() => setBusiness.mutate({ id: t.id, value: !t.is_business })}
                                >
                                  {t.is_business ? "✓ business" : "mark business"}
                                </button>
                                {t.is_business && (
                                  <button className="link-btn" onClick={() => editVat(t)}>
                                    {t.vat_amount ? `VAT ${t.vat_amount}` : "set VAT"}
                                  </button>
                                )}
                              </span>
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

// A header cell with a drag handle on its right edge for resizing (backlog).
function ResizableTh({
  col,
  cols,
  className,
  children,
}: {
  col: string;
  cols: ReturnType<typeof useResizableColumns>;
  className?: string;
  children: ReactNode;
}) {
  return (
    <th className={className}>
      {children}
      <span className="col-resize" title="Drag to resize" onMouseDown={(e) => cols.startResize(col, e)} />
    </th>
  );
}

// Attach a receipt image/PDF to a transaction and view what's attached (the
// original is kept so it stays viewable). Drill-down receipt viewer.
function ReceiptsField({ txnId }: { txnId: number }) {
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
  return (
    <span className="txn-detail__row" style={{ flexDirection: "column", alignItems: "flex-start", gap: 6 }}>
      {receipts.map((r) => (
        <span key={r.id} style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {r.has_file ? (
            <a href={receiptFileUrl(r.id)} target="_blank" rel="noreferrer">
              🧾 {r.source_filename || `receipt #${r.id}`}
            </a>
          ) : (
            <span className="muted">🧾 {r.source_filename || `receipt #${r.id}`} (original removed)</span>
          )}
          {r.total_amount && <span className="muted">· {r.total_amount}</span>}
        </span>
      ))}
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
