import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import Sparkline from "../components/Sparkline";
import {
  exportCategoriesCsv,
  exportMonthlyCsv,
  getCategoryBreakdown,
  getMe,
  getMonthlySeries,
  getOutliers,
  getSecurityHealth,
  getSummary,
  getVendorBreakdown,
  type MonthlyPoint,
  type TrendMetric,
} from "../api/client";

function downloadOrAlert(p: Promise<void>): void {
  p.catch((e) => window.alert(String(e instanceof Error ? e.message : e)));
}

function thisMonth(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

function gbp(value: string): string {
  return "£" + Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export default function Dashboard() {
  const [month, setMonth] = useState(thisMonth());
  const monthDate = `${month}-01`;

  const summary = useQuery({ queryKey: ["summary", monthDate], queryFn: () => getSummary(monthDate) });
  const categories = useQuery({
    queryKey: ["dash-categories", monthDate],
    queryFn: () => getCategoryBreakdown(monthDate),
  });
  const vendors = useQuery({
    queryKey: ["dash-vendors", monthDate],
    queryFn: () => getVendorBreakdown(monthDate),
  });

  const maxCat = Math.max(1, ...(categories.data ?? []).map((c) => Number(c.total)));

  return (
    <div className="page">
      <div className="page__head">
        <h1 className="page__title">Dashboard</h1>
        <input type="month" value={month} onChange={(e) => setMonth(e.target.value)} />
      </div>

      <SecurityBanner />

      <div className="stat-grid">
        <StatCard label="Spend" value={summary.data ? gbp(summary.data.spend_this_month) : "—"} tone="neg" />
        <StatCard label="Income" value={summary.data ? gbp(summary.data.income_this_month) : "—"} tone="pos" />
        <StatCard label="Net" value={summary.data ? gbp(summary.data.net_this_month) : "—"} />
        <StatCard label="Transactions" value={summary.data ? String(summary.data.total_transactions) : "—"} />
      </div>

      <HeadsUpCard monthDate={monthDate} />
      <TrendsCard monthDate={monthDate} />

      {summary.data && summary.data.total_transactions === 0 && (
        <div className="card">
          <p className="muted">
            No transactions yet. Head to <Link to="/import">Import</Link> to upload a CSV.
          </p>
        </div>
      )}

      <div className="cols">
        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <h2 className="card__title" style={{ margin: 0 }}>Spending by category</h2>
            {categories.data && categories.data.length > 0 && (
              <button className="link-btn" onClick={() => downloadOrAlert(exportCategoriesCsv(monthDate))}>
                ⬇ CSV
              </button>
            )}
          </div>
          {categories.isLoading && <p className="muted">Loading…</p>}
          {categories.data && categories.data.length === 0 && <p className="muted">No spending this month.</p>}
          <ul className="bars">
            {categories.data?.map((c) => (
              <li key={c.category_id ?? "none"}>
                <div className="bars__row">
                  <span className="bars__dot" style={{ background: c.colour ?? "#bbb" }} />
                  <span className="bars__label">{c.name}</span>
                  <span className="bars__value">{gbp(c.total)}</span>
                </div>
                <div className="bars__track">
                  <div
                    className="bars__fill"
                    style={{ width: `${(Number(c.total) / maxCat) * 100}%`, background: c.colour ?? "#bbb" }}
                  />
                </div>
              </li>
            ))}
          </ul>
        </div>

        <div className="card">
          <h2 className="card__title">Top vendors</h2>
          {vendors.data && vendors.data.length === 0 && (
            <p className="muted">
              No vendors yet — set up vendor aliases on the <Link to="/vendors">Vendors</Link> page.
            </p>
          )}
          <ul className="kv">
            {vendors.data?.map((v) => (
              <li key={v.vendor_id}>
                <span>{v.name}</span>
                <span>{gbp(v.total)} <span className="muted">· {v.count}</span></span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      {summary.data && summary.data.uncategorised_transactions > 0 && (
        <div className="card">
          <p className="status status--warn">
            {summary.data.uncategorised_transactions} uncategorised transaction(s).{" "}
            <Link to="/transactions">Categorise them →</Link>
          </p>
        </div>
      )}
    </div>
  );
}

const ARROW: Record<string, string> = { up: "▲", down: "▼", flat: "→" };

function TrendMini({ label, values, metric, currency, key_ }: {
  label: string;
  values: number[];
  metric: TrendMetric | undefined;
  currency: string;
  key_: "spend" | "income" | "net";
}) {
  const current = values.length ? values[values.length - 1] : 0;
  // For spend, going down is good; for income/net, going up is good.
  const dir = metric?.direction ?? "flat";
  const good = dir === "flat" ? null : key_ === "spend" ? dir === "down" : dir === "up";
  const arrowColour = good == null ? "var(--muted, #888)" : good ? "#3aa55a" : "#e05555";
  return (
    <div>
      <div className="stat__label">{label}</div>
      <div style={{ fontSize: "1.1rem", fontWeight: 600 }}>
        {current.toLocaleString(undefined, { style: "currency", currency })}
      </div>
      <Sparkline values={values} />
      {metric && (
        <div style={{ fontSize: "0.8rem", color: arrowColour }}>
          {ARROW[dir]} {metric.pct == null ? "—" : `${Math.abs(metric.pct)}%`}{" "}
          <span className="muted">vs last month</span>
        </div>
      )}
    </div>
  );
}

function TrendsCard({ monthDate }: { monthDate: string }) {
  const q = useQuery({
    queryKey: ["dash-monthly", monthDate],
    queryFn: () => getMonthlySeries(6, monthDate),
  });
  const data = q.data;
  if (!data || data.months.length < 2) return null;
  const num = (m: MonthlyPoint, k: "spend" | "income" | "net") => Number(m[k]);
  const keys: Array<"spend" | "income" | "net"> = ["spend", "income", "net"];
  const labels = { spend: "Spend", income: "Income", net: "Net" };
  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h2 className="card__title" style={{ margin: 0 }}>Trends · last {data.months.length} months</h2>
        <button
          className="link-btn"
          onClick={() => downloadOrAlert(exportMonthlyCsv(data.months.length, monthDate))}
        >
          ⬇ CSV
        </button>
      </div>
      <div className="stat-grid">
        {keys.map((k) => (
          <TrendMini
            key={k}
            key_={k}
            label={labels[k]}
            values={data.months.map((m) => num(m, k))}
            metric={data.trend[k]}
            currency={data.currency}
          />
        ))}
      </div>
    </div>
  );
}

function HeadsUpCard({ monthDate }: { monthDate: string }) {
  const q = useQuery({
    queryKey: ["dash-outliers", monthDate],
    queryFn: () => getOutliers(monthDate),
  });
  const items = q.data?.items ?? [];
  if (items.length === 0) return null; // nothing to flag → no card (non-nagging)
  return (
    <div className="card" style={{ borderLeft: "3px solid #e0a800" }}>
      <h2 className="card__title">Heads-up</h2>
      <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 8 }}>
        {items.map((it, i) => (
          <li
            key={i}
            style={{ borderLeft: `3px solid ${it.severity === "warn" ? "#e05555" : "#e0a800"}`, paddingLeft: 10 }}
          >
            <div><strong>{it.severity === "warn" ? "⚠️" : "💡"} {it.title}</strong></div>
            <div className="muted" style={{ fontSize: "0.85rem" }}>{it.detail}</div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function SecurityBanner() {
  // Owner-only, non-nagging: a single line linking to Settings, shown only when
  // there are active (non-dismissed) security recommendations (#128).
  const me = useQuery({ queryKey: ["me"], queryFn: getMe });
  const isAdmin = me.data?.is_admin === true;
  const health = useQuery({
    queryKey: ["security-health"],
    queryFn: getSecurityHealth,
    enabled: isAdmin,
  });
  const active = health.data?.active_count ?? 0;
  if (!isAdmin || active === 0) return null;

  return (
    <div className="card" style={{ borderLeft: "3px solid #e0a800" }}>
      <p className="status status--warn" style={{ margin: 0 }}>
        ⚠️ {active} security recommendation{active > 1 ? "s" : ""}.{" "}
        <Link to="/settings">Review in Settings →</Link>
      </p>
    </div>
  );
}

function StatCard({ label, value, tone }: { label: string; value: string; tone?: "pos" | "neg" }) {
  return (
    <div className="stat">
      <div className="stat__label">{label}</div>
      <div className={"stat__value" + (tone === "pos" ? " amt--pos" : tone === "neg" ? " amt--neg" : "")}>
        {value}
      </div>
    </div>
  );
}
