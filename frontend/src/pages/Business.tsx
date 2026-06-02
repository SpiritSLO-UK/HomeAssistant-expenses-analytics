import { useMutation, useQuery } from "@tanstack/react-query";
import { exportTransactionsCsv, getBusinessSummary } from "../api/client";

export default function Business() {
  const summary = useQuery({ queryKey: ["business-summary"], queryFn: getBusinessSummary });
  const exportCsv = useMutation({
    mutationFn: () => exportTransactionsCsv({ is_business: true }),
    onError: (e) => window.alert(String(e instanceof Error ? e.message : e)),
  });

  const s = summary.data;
  const cur = s?.currency ?? "GBP";

  return (
    <div className="page">
      <div className="page__head">
        <h1 className="page__title">Business expenses</h1>
        <button
          className="btn btn--ghost"
          disabled={exportCsv.isPending || (s?.transaction_count ?? 0) === 0}
          title="Download business transactions as CSV (with VAT columns) for claiming"
          onClick={() => exportCsv.mutate()}
        >
          {exportCsv.isPending ? "Exporting…" : "⬇ Export CSV"}
        </button>
      </div>
      <p className="muted">
        Everything you've flagged as <strong>business</strong> on the Transactions page, with the
        reclaimable VAT totalled. Mark a transaction business (and set its VAT, or let a matched
        receipt fill it in) to see it here.
      </p>

      {summary.isLoading && <p className="muted">Loading…</p>}

      {s && s.transaction_count === 0 && (
        <div className="card">
          <p className="muted">
            No business expenses yet. On the <strong>Transactions</strong> page, use the
            “business” toggle on a row (and optionally set its VAT).
          </p>
        </div>
      )}

      {s && s.transaction_count > 0 && (
        <>
          <div className="stat-grid">
            <div className="stat">
              <div className="stat__label">Business spend</div>
              <div className="stat__value">{s.total} {cur}</div>
            </div>
            <div className="stat">
              <div className="stat__label">Reclaimable VAT</div>
              <div className="stat__value">{s.vat} {cur}</div>
            </div>
            <div className="stat">
              <div className="stat__label">Transactions</div>
              <div className="stat__value">{s.transaction_count}</div>
            </div>
            <div className="stat">
              <div className="stat__label">Period</div>
              <div className="stat__value" style={{ fontSize: "0.95rem" }}>
                {s.first && s.last ? `${s.first} → ${s.last}` : "—"}
              </div>
            </div>
          </div>

          <div className="card">
            <h2 className="card__title">By category</h2>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr><th>Category</th><th className="num">Spend ({cur})</th><th className="num">VAT ({cur})</th></tr>
                </thead>
                <tbody>
                  {s.by_category.map((r) => (
                    <tr key={r.category_id ?? "none"}>
                      <td>{r.name}</td>
                      <td className="num">{r.total}</td>
                      <td className="num">{r.vat}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {s.by_month.length > 0 && (
            <div className="card">
              <h2 className="card__title">By month</h2>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr><th>Month</th><th className="num">Spend ({cur})</th></tr>
                  </thead>
                  <tbody>
                    {s.by_month.map((m) => (
                      <tr key={m.month}><td>{m.month}</td><td className="num">{m.total}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
