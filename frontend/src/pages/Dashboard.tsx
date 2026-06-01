import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  getCategoryBreakdown,
  getMe,
  getSecurityHealth,
  getSummary,
  getVendorBreakdown,
} from "../api/client";

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

      {summary.data && summary.data.total_transactions === 0 && (
        <div className="card">
          <p className="muted">
            No transactions yet. Head to <Link to="/import">Import</Link> to upload a CSV.
          </p>
        </div>
      )}

      <div className="cols">
        <div className="card">
          <h2 className="card__title">Spending by category</h2>
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
