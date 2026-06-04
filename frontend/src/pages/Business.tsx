import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  exportTransactionsCsv,
  getBusinessSummary,
  listTransactions,
  type BusinessPeriodRow,
  type Transaction,
} from "../api/client";

const PERIODS: { key: string; label: string }[] = [
  { key: "day", label: "Day" },
  { key: "week", label: "Week" },
  { key: "month", label: "Month" },
  { key: "year", label: "Year" },
];

// Year scope (Budgets-style): the current year down a few, plus "All time".
const THIS_YEAR = new Date().getFullYear();
const YEAR_CHOICES = [THIS_YEAR, THIS_YEAR - 1, THIS_YEAR - 2, THIS_YEAR - 3, THIS_YEAR - 4];

export default function Business() {
  const [period, setPeriod] = useState("month");
  const [year, setYear] = useState<number | null>(THIS_YEAR);
  const [openPeriod, setOpenPeriod] = useState<string | null>(null);
  const summary = useQuery({
    queryKey: ["business-summary", period, year],
    queryFn: () => getBusinessSummary(period, year),
  });
  const exportCsv = useMutation({
    mutationFn: () => exportTransactionsCsv({ is_business: true }),
    onError: (e) => globalThis.alert(String(e instanceof Error ? e.message : e)),
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

      {s?.transaction_count === 0 && (
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
                      <td>
                        <Link
                          title={`See business ${r.name} transactions`}
                          to={
                            r.category_id == null
                              ? `/transactions?is_business=true&uncategorised=true`
                              : `/transactions?is_business=true&category_id=${r.category_id}`
                          }
                        >
                          {r.name}
                        </Link>
                      </td>
                      <td className="num">{r.total}</td>
                      <td className="num">{r.vat}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="card">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
              <h2 className="card__title" style={{ margin: 0 }}>By period</h2>
              <div className="form-row" style={{ gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <select
                  value={year ?? ""}
                  title="Scope to a calendar year"
                  onChange={(e) => { setYear(e.target.value ? Number(e.target.value) : null); setOpenPeriod(null); }}
                >
                  <option value="">All time</option>
                  {YEAR_CHOICES.map((y) => <option key={y} value={y}>{y}</option>)}
                </select>
                <div className="form-row" style={{ gap: 4 }} title="Group business spend by">
                  {PERIODS.map((p) => (
                    <button
                      key={p.key}
                      className={"btn btn--sm" + (period === p.key ? "" : " btn--ghost")}
                      onClick={() => { setPeriod(p.key); setOpenPeriod(null); }}
                    >
                      {p.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr><th>Period</th><th className="num">Spend ({cur})</th><th className="num">VAT ({cur})</th><th className="num">Txns</th></tr>
                </thead>
                <tbody>
                  {s.by_period.map((p) => {
                    const open = openPeriod === p.period;
                    return (
                      <Fragment key={p.period}>
                        <tr>
                          <td>
                            <button className="link-btn" style={{ fontWeight: 600 }} onClick={() => setOpenPeriod(open ? null : p.period)}>
                              {open ? "▾ " : "▸ "}{p.label}
                            </button>
                          </td>
                          <td className="num">{p.total}</td>
                          <td className="num">{p.vat}</td>
                          <td className="num">{p.count}</td>
                        </tr>
                        {open && (
                          <tr>
                            <td colSpan={4} style={{ background: "rgba(127,127,127,0.05)" }}>
                              <PeriodTxns row={p} cur={cur} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function PeriodTxns({ row, cur }: Readonly<{ row: BusinessPeriodRow; cur: string }>) {
  const q = useQuery({
    queryKey: ["business-period-txns", row.start, row.end],
    queryFn: () =>
      listTransactions({ is_business: true, date_from: row.start, date_to: row.end, limit: 200 }),
  });
  if (q.isLoading || !q.data) return <p className="muted" style={{ margin: "6px 0" }}>Loading…</p>;
  const items: Transaction[] = q.data.items;
  if (items.length === 0) return <p className="muted" style={{ margin: "6px 0" }}>No transactions.</p>;
  return (
    <>
      <ul className="kv" style={{ margin: "6px 0", maxWidth: 560 }}>
        {items.map((t) => (
          <li key={t.id}>
            <span>
              <span className="muted">{t.transaction_date}</span> ·{" "}
              <Link to={`/transactions?focus=${t.id}`} title="Open this transaction">
                {t.merchant_raw || t.description_raw}
              </Link>
            </span>
            <span style={{ whiteSpace: "nowrap" }}>
              {t.base_amount ?? t.amount} {cur}
              {t.vat_amount ? <span className="muted"> · VAT {t.vat_amount}</span> : null}
            </span>
          </li>
        ))}
      </ul>
      <p style={{ margin: "0 0 6px" }}>
        <Link
          className="link-btn"
          to={`/transactions?is_business=true&date_from=${row.start}&date_to=${row.end}`}
        >
          Open all in Transactions →
        </Link>
      </p>
    </>
  );
}
