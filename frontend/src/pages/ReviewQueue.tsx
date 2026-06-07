import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  categoriseTransaction,
  createTransactionFromReceipt,
  createVendorFromTransaction,
  getAiStatus,
  getReviewCount,
  listCategories,
  listReceipts,
  listReviewItems,
  listTransactions,
  setReviewStatus,
  updateTransaction,
  type ReviewItem,
  type Transaction,
} from "../api/client";
import { suggestForTransaction } from "../lib/aiSuggest";

const PAGE = 25;

const REASON_LABEL: Record<string, string> = {
  unknown_vendor: "Unknown vendor",
  unknown_category: "Uncategorised",
  low_confidence: "Low confidence",
  duplicate_possible: "Possible duplicate",
  receipt_unmatched: "Unmatched receipt",
  split_invalid: "Invalid split",
  cloud_ai_approval_required: "Cloud AI approval",
  sensitive_data_detected: "Sensitive data",
  parser_error: "Parser error",
};

const SEVERITY_COLOUR: Record<string, string> = {
  info: "rgba(127,127,127,0.25)",
  warning: "#d8930a",
  error: "#c0392b",
};

export default function ReviewQueue() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "uncategorised" ? "uncategorised" : "review";
  const setTab = (t: string) =>
    setParams(t === "uncategorised" ? { tab: "uncategorised" } : {}, { replace: true });

  // Both counts power the tab labels regardless of which tab is open. The review
  // queue (curated ReviewItem rows) and uncategorised transactions stay separate
  // data — this page is just the single place to clear both.
  const reviewCount = useQuery({ queryKey: ["review", "count"], queryFn: getReviewCount });
  const uncatCount = useQuery({
    queryKey: ["uncategorised", "count"],
    queryFn: () => listTransactions({ uncategorised: true, limit: 1 }),
  });

  return (
    <div className="page">
      <h1 className="page__title">Review Queue</h1>

      <div className="form-row" style={{ gap: 6, marginBottom: 4 }}>
        <button
          className={"btn btn--sm" + (tab === "review" ? "" : " btn--ghost")}
          onClick={() => setTab("review")}
        >
          To review{reviewCount.data ? ` (${reviewCount.data.open})` : ""}
        </button>
        <button
          className={"btn btn--sm" + (tab === "uncategorised" ? "" : " btn--ghost")}
          onClick={() => setTab("uncategorised")}
        >
          Uncategorised{uncatCount.data ? ` (${uncatCount.data.total})` : ""}
        </button>
      </div>

      {tab === "review" ? <ReviewTab /> : <UncategorisedTab />}
    </div>
  );
}

function ReviewTab() {
  const [showResolved, setShowResolved] = useState(false);
  const status = showResolved ? "resolved" : "open";

  const items = useQuery({ queryKey: ["review", status], queryFn: () => listReviewItems(status) });
  const categories = useQuery({ queryKey: ["categories"], queryFn: listCategories });
  const aiStatus = useQuery({ queryKey: ["ai-status"], queryFn: getAiStatus });

  return (
    <>
      <div className="form-row" style={{ justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <p className="muted" style={{ margin: 0 }}>
          Things the app wasn't sure about — unknown vendors, low-confidence reads, unmatched receipts.
          Fix each one right here, or resolve/ignore it.
        </p>
        <label className="checkbox" style={{ whiteSpace: "nowrap" }}>
          <input type="checkbox" checked={showResolved} onChange={(e) => setShowResolved(e.target.checked)} />{" "}
          Show resolved
        </label>
      </div>

      <div className="card">
        {items.isLoading && <p className="muted">Loading…</p>}
        {items.data?.length === 0 && (
          <p className="muted">{showResolved ? "Nothing resolved yet." : "All clear — nothing to review. 🎉"}</p>
        )}
        <div>
          {items.data?.map((item: ReviewItem) => (
            <ReviewRow
              key={item.id}
              item={item}
              showResolved={showResolved}
              categories={categories.data ?? []}
              aiEnabled={aiStatus.data?.enabled ?? false}
            />
          ))}
        </div>
      </div>
    </>
  );
}

function ReviewRow({
  item,
  showResolved,
  categories,
  aiEnabled,
}: Readonly<{
  item: ReviewItem;
  showResolved: boolean;
  categories: { id: number; name: string }[];
  aiEnabled: boolean;
}>) {
  const qc = useQueryClient();
  const isTxn = item.item_type === "transaction" && item.item_id != null;
  const isReceipt = item.item_type === "receipt" && item.item_id != null;

  const update = useMutation({
    mutationFn: (newStatus: string) => setReviewStatus(item.id, newStatus),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["review"] }),
  });

  // Categorising the transaction is the fix for an uncategorised/unknown-vendor
  // item — once it has a category, resolve the review item too so the row clears.
  const categorise = useMutation({
    mutationFn: (categoryId: number) => categoriseTransaction(item.item_id as number, categoryId),
    onSuccess: async () => {
      await setReviewStatus(item.id, "resolved");
      qc.invalidateQueries({ queryKey: ["review"] });
      qc.invalidateQueries({ queryKey: ["uncategorised"] });
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });

  // Unmatched receipts: surface the recommended transaction + add it in one click
  // (to a dedicated "Cash & receipts" account). Shared ["receipts"] cache → one fetch.
  const receipts = useQuery({ queryKey: ["receipts"], queryFn: listReceipts, enabled: isReceipt });
  const receipt = isReceipt ? receipts.data?.find((x) => x.id === item.item_id) : undefined;
  const rec = receipt?.recommended_transaction ?? null;
  const addTxn = useMutation({
    mutationFn: () => createTransactionFromReceipt(item.item_id as number, { new_account: true }),
    onSuccess: () => {
      // create_transaction_from_receipt attaches the receipt, which resolves the
      // receipt_unmatched item server-side; refresh the affected views.
      qc.invalidateQueries({ queryKey: ["review"] });
      qc.invalidateQueries({ queryKey: ["receipts"] });
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["accounts"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });

  async function onSuggest() {
    if (item.item_id == null) return;
    try {
      const s = await suggestForTransaction(item.item_id);
      if (!s) return;
      if (s.country) await updateTransaction(item.item_id, { country: s.country });
      if (s.vendor) {
        await createVendorFromTransaction(item.item_id, s.vendor);
        qc.invalidateQueries({ queryKey: ["vendors"] });
      }
      if (s.categoryId != null) categorise.mutate(s.categoryId); // resolves item + invalidates
      else qc.invalidateQueries({ queryKey: ["transactions"] }); // category-less change
    } catch (e) {
      globalThis.alert(String(e instanceof Error ? e.message : e));
    }
  }

  const pending = update.isPending || categorise.isPending;

  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap", padding: "10px 0", borderBottom: "1px solid rgba(127,127,127,0.2)" }}>
      <div style={{ minWidth: 0 }}>
        <span className="tag" style={{ background: SEVERITY_COLOUR[item.severity] }}>
          {REASON_LABEL[item.reason] ?? item.reason}
        </span>{" "}
        <span className="muted">{item.item_type}{item.item_id == null ? "" : ` #${item.item_id}`}</span>
        {item.suggested_action && <div style={{ marginTop: 4 }}>{item.suggested_action}</div>}
        {isReceipt && rec && (
          <div className="muted" style={{ marginTop: 4, fontSize: "0.85rem" }}>
            💡 Recommended: <strong>{rec.merchant}</strong> · {rec.transaction_date} ·{" "}
            <span className="amt--neg">{rec.amount} {rec.currency}</span>
            {rec.category_name ? ` · ${rec.category_name}` : ""}
          </div>
        )}
      </div>
      <div style={{ display: "flex", gap: 6, flexShrink: 0, alignItems: "center", flexWrap: "wrap" }}>
        {/* Fix-in-place: categorise the transaction without leaving the queue. */}
        {isTxn && !showResolved && (
          <>
            <select
              defaultValue=""
              disabled={pending}
              aria-label="Categorise this transaction"
              onChange={(e) => { if (e.target.value) categorise.mutate(Number(e.target.value)); }}
            >
              <option value="">Categorise…</option>
              {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            {aiEnabled && (
              <button className="link-btn" disabled={pending} title="Ask the AI assistant to suggest a category" onClick={onSuggest}>
                ✨ suggest
              </button>
            )}
          </>
        )}
        {isTxn && (
          <Link className="btn btn--ghost" to={`/transactions?focus=${item.item_id}`}>
            Open transaction →
          </Link>
        )}
        {/* Unmatched receipt: add the recommended transaction in one click (to a
            "Cash & receipts" account), or open the receipt to pick another account
            / find a match / edit fields. */}
        {isReceipt && rec && !showResolved && (
          <button
            className="btn"
            disabled={addTxn.isPending}
            title={`Add ${rec.merchant} ${rec.amount} ${rec.currency} to a "Cash & receipts" account`}
            onClick={() => addTxn.mutate()}
          >
            {addTxn.isPending ? "Adding…" : "➕ Add transaction"}
          </button>
        )}
        {isReceipt && (
          <Link className="btn btn--ghost" to={`/receipts?focus=${item.item_id}`}>
            Open receipt →
          </Link>
        )}
        {!showResolved && (
          <>
            <button className="btn btn--ghost" disabled={pending} onClick={() => update.mutate("resolved")}>Resolve</button>
            <button className="link-btn" disabled={pending} onClick={() => update.mutate("ignored")}>Ignore</button>
          </>
        )}
      </div>
    </div>
  );
}

function UncategorisedTab() {
  const qc = useQueryClient();
  const [offset, setOffset] = useState(0);
  const categories = useQuery({ queryKey: ["categories"], queryFn: listCategories });
  const aiStatus = useQuery({ queryKey: ["ai-status"], queryFn: getAiStatus });
  const txns = useQuery({
    queryKey: ["uncategorised", "page", offset],
    queryFn: () => listTransactions({ uncategorised: true, limit: PAGE, offset }),
  });

  const data = txns.data;
  const total = data?.total ?? 0;

  // After categorising the last row on a page that isn't the first, step back so
  // we don't strand the user on an empty page.
  useEffect(() => {
    if (data?.items.length === 0 && offset > 0) setOffset((o) => Math.max(0, o - PAGE));
  }, [data, offset]);

  const onDone = () => {
    qc.invalidateQueries({ queryKey: ["uncategorised"] });
    qc.invalidateQueries({ queryKey: ["transactions"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
  };

  return (
    <>
      <p className="muted">
        Transactions with no category yet. Pick one to categorise it — the row then drops off this list.
        (These stay separate from the review queue; this is just the one place to clear both.)
      </p>
      <div className="card">
        {txns.isLoading && <p className="muted">Loading…</p>}
        {data?.items.length === 0 && (
          <p className="muted">Everything's categorised — nothing here. 🎉</p>
        )}
        {data?.items.map((t) => (
          <UncategorisedRow
            key={t.id}
            txn={t}
            categories={categories.data ?? []}
            aiEnabled={aiStatus.data?.enabled ?? false}
            onDone={onDone}
          />
        ))}
        {total > PAGE && (
          <div className="form-row" style={{ justifyContent: "space-between", alignItems: "center", marginTop: 10 }}>
            <button className="btn btn--sm btn--ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>← Prev</button>
            <span className="muted">{offset + 1}–{Math.min(offset + PAGE, total)} of {total}</span>
            <button className="btn btn--sm btn--ghost" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Next →</button>
          </div>
        )}
      </div>
    </>
  );
}

function UncategorisedRow({
  txn,
  categories,
  aiEnabled,
  onDone,
}: Readonly<{ txn: Transaction; categories: { id: number; name: string }[]; aiEnabled: boolean; onDone: () => void }>) {
  const set = useMutation({
    mutationFn: (categoryId: number) => categoriseTransaction(txn.id, categoryId),
    onSuccess: onDone,
  });

  // Same ✨ suggest as the "To review" tab + the Transactions list: category (+
  // country/vendor when the AI infers them). Applying the category drops the row.
  async function onSuggest() {
    try {
      const s = await suggestForTransaction(txn.id);
      if (!s) return;
      if (s.country) await updateTransaction(txn.id, { country: s.country });
      if (s.vendor) await createVendorFromTransaction(txn.id, s.vendor);
      if (s.categoryId != null) set.mutate(s.categoryId);
      else onDone();
    } catch (e) {
      globalThis.alert(String(e instanceof Error ? e.message : e));
    }
  }

  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, padding: "10px 0", borderBottom: "1px solid rgba(127,127,127,0.2)" }}>
      <div style={{ minWidth: 0 }}>
        <span className="muted">{txn.transaction_date}</span>{" "}
        <Link to={`/transactions?focus=${txn.id}`}>{txn.description_raw}</Link>
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexShrink: 0 }}>
        <span style={{ whiteSpace: "nowrap" }}>{txn.amount} {txn.currency}</span>
        <select
          defaultValue=""
          disabled={set.isPending}
          onChange={(e) => { if (e.target.value) set.mutate(Number(e.target.value)); }}
        >
          <option value="">Categorise…</option>
          {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        {aiEnabled && (
          <button className="link-btn" disabled={set.isPending} title="Ask the AI assistant to suggest a category" onClick={onSuggest}>
            ✨ suggest
          </button>
        )}
      </div>
    </div>
  );
}
