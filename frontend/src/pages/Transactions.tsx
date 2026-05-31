import { useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  categoriseTransaction,
  getSettings,
  listCategories,
  listTransactions,
  recategorise,
  type TransactionFilters,
} from "../api/client";

const PAGE_SIZE = 50;

export default function Transactions() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [needsReview, setNeedsReview] = useState(false);
  const [page, setPage] = useState(0);

  const filters: TransactionFilters = {
    search: search || undefined,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    needs_review: needsReview || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  };

  const categories = useQuery({ queryKey: ["categories"], queryFn: listCategories });
  const settings = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const base = settings.data?.base_currency ?? "GBP";
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["transactions", filters],
    queryFn: () => listTransactions(filters),
    placeholderData: keepPreviousData,
  });

  const setCategory = useMutation({
    mutationFn: (v: { id: number; categoryId: number | null }) =>
      categoriseTransaction(v.id, v.categoryId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["transactions"] }),
  });

  const recat = useMutation({
    mutationFn: () => recategorise(true),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["transactions"] }),
  });

  const total = data?.total ?? 0;
  const maxPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);

  return (
    <div className="page">
      <div className="page__head">
        <h1 className="page__title">Transactions</h1>
        <button className="btn btn--ghost" disabled={recat.isPending} onClick={() => recat.mutate()}>
          {recat.isPending ? "Re-categorising…" : "Re-categorise uncategorised"}
        </button>
      </div>
      {recat.isSuccess && (
        <p className="muted">Re-categorised {recat.data.recategorised} transaction(s).</p>
      )}

      <div className="card">
        <div className="filters">
          <input
            placeholder="Search description / merchant"
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(0); }}
          />
          <label>From <input type="date" value={dateFrom} onChange={(e) => { setDateFrom(e.target.value); setPage(0); }} /></label>
          <label>To <input type="date" value={dateTo} onChange={(e) => { setDateTo(e.target.value); setPage(0); }} /></label>
          <label className="checkbox">
            <input type="checkbox" checked={needsReview} onChange={(e) => { setNeedsReview(e.target.checked); setPage(0); }} />
            Needs review
          </label>
        </div>
      </div>

      <div className="card">
        {isLoading && <p className="muted">Loading…</p>}
        {isError && <p className="status status--error">{String(error)}</p>}
        {data && data.items.length === 0 && (
          <p className="muted">
            No transactions. Import a CSV on the <strong>Import</strong> page to get started.
          </p>
        )}
        {data && data.items.length > 0 && (
          <>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Description</th>
                    <th className="num">Amount</th>
                    <th>Category</th>
                    <th>Flags</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((t) => (
                    <tr key={t.id}>
                      <td>{t.transaction_date}</td>
                      <td>
                        {t.description_raw}
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
                      <td>
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
                            <option key={c.id} value={c.id}>
                              {c.name}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        {t.is_transfer && <span className="tag">transfer</span>}
                        {t.is_income && <span className="tag">income</span>}
                        {t.needs_review && <span className="tag tag--dup">review</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="pager">
              <button className="btn btn--ghost" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
                ← Prev
              </button>
              <span className="muted">{total} total · page {page + 1} of {maxPage + 1}</span>
              <button className="btn btn--ghost" disabled={page >= maxPage} onClick={() => setPage((p) => p + 1)}>
                Next →
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
