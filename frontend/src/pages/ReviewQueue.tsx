import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  categoriseTransaction,
  getReviewCount,
  listCategories,
  listReviewItems,
  listTransactions,
  setReviewStatus,
  type ReviewItem,
  type Transaction,
} from "../api/client";

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
  const qc = useQueryClient();
  const [showResolved, setShowResolved] = useState(false);
  const status = showResolved ? "resolved" : "open";

  const items = useQuery({ queryKey: ["review", status], queryFn: () => listReviewItems(status) });

  const update = useMutation({
    mutationFn: (v: { id: number; status: string }) => setReviewStatus(v.id, v.status),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["review"] }),
  });

  return (
    <>
      <div className="form-row" style={{ justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <p className="muted" style={{ margin: 0 }}>
          Things the app wasn't sure about — unknown vendors, low-confidence reads, unmatched receipts.
          Resolve or ignore each one.
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
            <div key={item.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, padding: "10px 0", borderBottom: "1px solid rgba(127,127,127,0.2)" }}>
              <div>
                <span className="tag" style={{ background: SEVERITY_COLOUR[item.severity] }}>
                  {REASON_LABEL[item.reason] ?? item.reason}
                </span>{" "}
                <span className="muted">{item.item_type}{item.item_id == null ? "" : ` #${item.item_id}`}</span>
                {item.suggested_action && <div style={{ marginTop: 4 }}>{item.suggested_action}</div>}
              </div>
              <div style={{ display: "flex", gap: 6, flexShrink: 0, alignItems: "center" }}>
                {item.item_type === "transaction" && item.item_id != null && (
                  <Link className="btn btn--ghost" to={`/transactions?focus=${item.item_id}`}>
                    Open transaction →
                  </Link>
                )}
                {!showResolved && (
                  <>
                    <button className="btn btn--ghost" disabled={update.isPending} onClick={() => update.mutate({ id: item.id, status: "resolved" })}>Resolve</button>
                    <button className="link-btn" disabled={update.isPending} onClick={() => update.mutate({ id: item.id, status: "ignored" })}>Ignore</button>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

function UncategorisedTab() {
  const qc = useQueryClient();
  const [offset, setOffset] = useState(0);
  const categories = useQuery({ queryKey: ["categories"], queryFn: listCategories });
  const txns = useQuery({
    queryKey: ["uncategorised", "page", offset],
    queryFn: () => listTransactions({ uncategorised: true, limit: PAGE, offset }),
  });

  const data = txns.data;
  const total = data?.total ?? 0;

  // After categorising the last row on a page that isn't the first, step back so
  // we don't strand the user on an empty page.
  useEffect(() => {
    if (data && data.items.length === 0 && offset > 0) setOffset((o) => Math.max(0, o - PAGE));
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
        {data && data.items.length === 0 && (
          <p className="muted">Everything's categorised — nothing here. 🎉</p>
        )}
        {data?.items.map((t) => (
          <UncategorisedRow key={t.id} txn={t} categories={categories.data ?? []} onDone={onDone} />
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
  onDone,
}: Readonly<{ txn: Transaction; categories: { id: number; name: string }[]; onDone: () => void }>) {
  const set = useMutation({
    mutationFn: (categoryId: number) => categoriseTransaction(txn.id, categoryId),
    onSuccess: onDone,
  });
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
      </div>
    </div>
  );
}
