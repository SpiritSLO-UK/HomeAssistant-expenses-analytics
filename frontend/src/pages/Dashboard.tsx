import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import Sparkline from "../components/Sparkline";
import {
  exportCategoriesCsv,
  exportMonthlyCsv,
  getAllowanceSummary,
  getBudgetSummary,
  getBusinessSummary,
  getCategoryBreakdown,
  getDashboardProjects,
  getMe,
  getMonthlySeries,
  getOutliers,
  getProcessingStats,
  getSavingsSummary,
  getSecurityHealth,
  getSummary,
  getTravelTrips,
  getVendorBreakdown,
  listAccounts,
  listMembers,
  type Member,
  type MonthlyPoint,
  type TrendMetric,
} from "../api/client";
import {
  getDashboardView,
  getHiddenDashboardCards,
  setDashboardView,
  setHiddenDashboardCards,
} from "../prefs";

const VIEWS: { key: string; label: string }[] = [
  { key: "all", label: "All" },
  { key: "mine", label: "Mine" },
  { key: "shared", label: "Shared" },
];

const OPTIONAL_CARDS: { key: string; label: string }[] = [
  { key: "headsup", label: "Heads-up" },
  { key: "trends", label: "Trends" },
  { key: "categories", label: "Spending by category" },
  { key: "vendors", label: "Top vendors" },
  { key: "projects", label: "By project" },
  { key: "savings", label: "Savings" },
  { key: "budgets", label: "Budgets" },
  { key: "business", label: "Business" },
  { key: "travel", label: "Travel" },
  { key: "allowance", label: "Allowance" },
  { key: "processing", label: "Processing" },
];

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

  // Per-device card show/hide (#86).
  const [hidden, setHidden] = useState<Set<string>>(() => getHiddenDashboardCards());
  const [customise, setCustomise] = useState(false);
  const show = (key: string) => !hidden.has(key);
  const toggleCard = (key: string) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      setHiddenDashboardCards(next);
      return next;
    });

  // Mine/Shared/All view toggle (#66/#82) — only meaningful once some account has
  // an owner, so it's hidden in the common all-shared (single-user) case.
  const [view, setView] = useState<string>(() => getDashboardView());
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: listAccounts });
  const hasOwned = (accounts.data ?? []).some((a) => a.owner_user_id !== null);
  const chooseView = (v: string) => {
    setView(v);
    setDashboardView(v);
  };

  // Per-member filter (#66/#82): show one household member's spending. Picking a
  // member overrides the Mine/Shared/All toggle, so that toggle is hidden then.
  const members = useQuery({ queryKey: ["members"], queryFn: listMembers });
  const [memberId, setMemberId] = useState<string>("");
  const mid = memberId ? Number(memberId) : undefined;
  const hasMembers = (members.data?.length ?? 0) > 1;

  const summary = useQuery({
    queryKey: ["summary", monthDate, view, memberId],
    queryFn: () => getSummary(monthDate, view, mid),
  });
  const categories = useQuery({
    queryKey: ["dash-categories", monthDate, view, memberId],
    queryFn: () => getCategoryBreakdown(monthDate, view, mid),
  });
  const vendors = useQuery({
    queryKey: ["dash-vendors", monthDate, view, memberId],
    queryFn: () => getVendorBreakdown(monthDate, view, mid),
  });

  const maxCat = Math.max(1, ...(categories.data ?? []).map((c) => Number(c.total)));

  return (
    <div className="page">
      <div className="page__head">
        <h1 className="page__title">Dashboard</h1>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          {hasMembers && (
            <label className="muted" title="Show one household member's spending">
              Member{" "}
              <select value={memberId} onChange={(e) => setMemberId(e.target.value)}>
                <option value="">All members</option>
                {members.data?.map((m) => (
                  <option key={m.id} value={m.id}>{m.display_name}</option>
                ))}
              </select>
            </label>
          )}
          {hasOwned && !mid && (
            <div className="form-row" style={{ gap: 4 }} title="Whose accounts to include">
              {VIEWS.map((v) => (
                <button
                  key={v.key}
                  className={"btn btn--sm" + (view === v.key ? "" : " btn--ghost")}
                  onClick={() => chooseView(v.key)}
                >
                  {v.label}
                </button>
              ))}
            </div>
          )}
          <input type="month" value={month} onChange={(e) => setMonth(e.target.value)} />
          <button className="btn btn--ghost" onClick={() => setCustomise((v) => !v)}>
            {customise ? "Done" : "⚙ Customise"}
          </button>
        </div>
      </div>

      {customise && (
        <div className="card">
          <h2 className="card__title">Customise dashboard</h2>
          <p className="muted" style={{ marginTop: 0, fontSize: "0.85rem" }}>
            Choose which cards to show. Saved on this device.
          </p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 14 }}>
            {OPTIONAL_CARDS.map((c) => (
              <label key={c.key} className="checkbox">
                <input type="checkbox" checked={show(c.key)} onChange={() => toggleCard(c.key)} /> {c.label}
              </label>
            ))}
          </div>
        </div>
      )}

      <SecurityBanner />

      <div className="stat-grid">
        <StatCard label="Spend" value={summary.data ? gbp(summary.data.spend_this_month) : "—"} tone="neg" />
        <StatCard label="Income" value={summary.data ? gbp(summary.data.income_this_month) : "—"} tone="pos" />
        <StatCard label="Net" value={summary.data ? gbp(summary.data.net_this_month) : "—"} />
        <StatCard label="Transactions" value={summary.data ? String(summary.data.total_transactions) : "—"} />
      </div>

      {show("headsup") && <HeadsUpCard monthDate={monthDate} memberId={mid} />}
      {show("trends") && <TrendsCard monthDate={monthDate} view={view} memberId={mid} />}

      {summary.data && summary.data.total_transactions === 0 && (
        <div className="card">
          <p className="muted">
            No transactions yet. Head to <Link to="/import">Import</Link> to upload a CSV.
          </p>
        </div>
      )}

      {(show("categories") || show("vendors")) && (
        <div className="cols">
          {show("categories") && (
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
          )}

          {show("vendors") && (
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
          )}
        </div>
      )}

      {show("projects") && <ProjectsCard memberId={mid} />}

      {(show("savings") || show("budgets") || show("business") || show("travel") || show("allowance")) && (
        <div className="cols cols--domain">
          {show("savings") && <SavingsCard />}
          {show("budgets") && <BudgetsCard monthDate={monthDate} />}
          {show("business") && <BusinessCard />}
          {show("travel") && <TravelCard />}
          {show("allowance") && <AllowanceCard />}
        </div>
      )}

      {show("processing") && <ProcessingCard />}

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

function ProjectsCard({ memberId }: { memberId?: number }) {
  const q = useQuery({ queryKey: ["dashboard-projects", memberId], queryFn: () => getDashboardProjects(memberId) });
  const items = q.data ?? [];
  if (items.length === 0) return null; // non-nagging: no card until there are projects
  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h2 className="card__title" style={{ margin: 0 }}>By project</h2>
        <Link className="link-btn" to="/projects">Manage →</Link>
      </div>
      <ul className="kv">
        {items.map((p) => {
          const pct = p.percent ?? null;
          return (
            <li key={p.project_id}>
              <span>
                <Link to="/projects">{p.name}</Link> <span className="tag">{p.status}</span>
              </span>
              <span>
                {gbp(p.spent)}
                {p.budget ? <span className="muted"> / {gbp(p.budget)}{pct != null ? ` · ${pct}%` : ""}</span> : ""}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// --- Per-domain summary cards (#83). Each is self-contained and renders null
// until that area has data, so the dashboard only shows domains in use. ---

function CardHead({ title, to }: { title: string; to: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
      <h2 className="card__title" style={{ margin: 0 }}>{title}</h2>
      <Link className="link-btn" to={to}>Open →</Link>
    </div>
  );
}

function SavingsCard() {
  const q = useQuery({ queryKey: ["dash-savings"], queryFn: getSavingsSummary });
  const s = q.data;
  if (!s || (s.accounts.length === 0 && s.goals.length === 0)) return null;
  const topGoal = s.goals.length ? [...s.goals].sort((a, b) => b.percent - a.percent)[0] : null;
  return (
    <div className="card">
      <CardHead title="Savings" to="/savings" />
      <ul className="kv">
        <li><span>Total saved</span><span>{gbp(s.total_savings)}</span></li>
        <li><span>Accounts</span><span>{s.accounts.length}</span></li>
        {topGoal && (
          <li><span>Top goal · {topGoal.name}</span><span>{topGoal.percent}%</span></li>
        )}
      </ul>
    </div>
  );
}

function BudgetsCard({ monthDate }: { monthDate: string }) {
  const q = useQuery({ queryKey: ["dash-budgets", monthDate], queryFn: () => getBudgetSummary(monthDate) });
  const items = q.data ?? [];
  if (items.length === 0) return null;
  const over = items.filter((b) => b.status === "over").length;
  const near = items.filter((b) => b.status === "warn").length;
  return (
    <div className="card">
      <CardHead title="Budgets" to="/budgets" />
      <ul className="kv">
        <li>
          <span>Active budgets</span>
          <span>
            {items.length}
            {over > 0 && <span className="status status--warn"> · {over} over</span>}
            {near > 0 && <span className="muted"> · {near} near</span>}
          </span>
        </li>
        {items.slice(0, 4).map((b) => (
          <li key={b.budget_id}>
            <span>{b.name}</span>
            <span>
              {gbp(b.spent)} <span className="muted">/ {gbp(b.amount)} · {b.percent}%</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function BusinessCard() {
  const q = useQuery({ queryKey: ["dash-business"], queryFn: () => getBusinessSummary("month") });
  const s = q.data;
  if (!s || s.transaction_count === 0) return null;
  return (
    <div className="card">
      <CardHead title="Business" to="/business" />
      <ul className="kv">
        <li><span>Business spend</span><span>{gbp(s.total)}</span></li>
        <li><span>Reclaimable VAT</span><span>{gbp(s.vat)}</span></li>
        <li><span>Transactions</span><span>{s.transaction_count}</span></li>
      </ul>
    </div>
  );
}

function TravelCard() {
  const q = useQuery({ queryKey: ["dash-travel"], queryFn: () => getTravelTrips() });
  const trips = q.data ?? [];
  if (trips.length === 0) return null;
  const total = trips.reduce((sum, t) => sum + Number(t.base_total), 0);
  return (
    <div className="card">
      <CardHead title="Travel" to="/travel" />
      <ul className="kv">
        <li><span>Trips</span><span>{trips.length}</span></li>
        <li><span>Spend abroad</span><span>{gbp(String(total))}</span></li>
        {trips.slice(0, 3).map((t) => (
          <li key={t.transaction_ids[0] ?? t.label}>
            <span>{t.label}</span>
            <span>{gbp(t.base_total)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function AllowanceCard() {
  const members = useQuery({ queryKey: ["members"], queryFn: listMembers });
  const children = (members.data ?? []).filter((m) => m.role === "child");
  if (children.length === 0) return null;
  return (
    <div className="card">
      <CardHead title="Allowance" to="/allowance" />
      <ul className="kv">
        {children.map((c) => (
          <ChildAllowanceRow key={c.id} child={c} />
        ))}
      </ul>
    </div>
  );
}

function ChildAllowanceRow({ child }: { child: Member }) {
  const q = useQuery({ queryKey: ["dash-allowance", child.id], queryFn: () => getAllowanceSummary(child.id) });
  const s = q.data;
  const budget = s?.budgets[0];
  return (
    <li>
      <span>{child.display_name}</span>
      <span>
        {budget ? (
          <>
            {gbp(budget.spent)} <span className="muted">/ {gbp(budget.amount)}</span>
          </>
        ) : s ? (
          <span className="muted">{s.items.length} item(s)</span>
        ) : (
          <span className="muted">…</span>
        )}
      </span>
    </li>
  );
}

function ProcessingCard() {
  const q = useQuery({ queryKey: ["processing-stats"], queryFn: getProcessingStats });
  const s = q.data;
  if (!s) return null;
  if (s.transactions_imported === 0 && s.receipts_total === 0 && s.ai_total === 0) {
    return null; // non-nagging: no card until something has been processed
  }
  const tasks = Object.entries(s.ai_by_task).sort((a, b) => b[1] - a[1]);
  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h2 className="card__title" style={{ margin: 0 }}>Processing</h2>
        <Link className="link-btn" to="/logs">Logs →</Link>
      </div>
      <p className="muted" style={{ marginTop: 4, fontSize: "0.85rem" }}>
        How much has been imported and enriched — and how much went through AI vs locally.
      </p>
      <ul className="kv">
        <li><span>Statements imported</span><span>{s.statements_imported}</span></li>
        <li><span>Transactions</span><span>{s.transactions_imported}</span></li>
        {s.receipts_total > 0 && (
          <li>
            <span>Receipts processed</span>
            <span>
              {s.receipts_processed} / {s.receipts_total}
              {s.receipts_failed > 0 && <span className="muted"> · {s.receipts_failed} failed</span>}
              {s.receipts_pending > 0 && <span className="muted"> · {s.receipts_pending} pending</span>}
            </span>
          </li>
        )}
        <li>
          <span>AI enrichment calls</span>
          <span>
            {s.ai_total === 0 ? (
              <span className="muted">none — all processed locally</span>
            ) : (
              <>
                {s.ai_total} <span className="muted">· {s.ai_cloud} cloud / {s.ai_local} local</span>
              </>
            )}
          </span>
        </li>
        {s.ai_total > 0 && (s.ai_failed > 0 || s.ai_pending > 0) && (
          <li>
            <span>AI call status</span>
            <span className="muted">
              {s.ai_completed} done
              {s.ai_failed > 0 ? ` · ${s.ai_failed} failed` : ""}
              {s.ai_pending > 0 ? ` · ${s.ai_pending} pending` : ""}
            </span>
          </li>
        )}
        {s.ai_avg_seconds != null && (
          <li><span>Average AI turnaround</span><span>{s.ai_avg_seconds}s</span></li>
        )}
      </ul>
      {tasks.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
          {tasks.map(([task, n]) => (
            <span key={task} className="tag">{task.replace(/_/g, " ")} · {n}</span>
          ))}
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

function TrendsCard({ monthDate, view, memberId }: { monthDate: string; view: string; memberId?: number }) {
  const q = useQuery({
    queryKey: ["dash-monthly", monthDate, view, memberId],
    queryFn: () => getMonthlySeries(6, monthDate, view, memberId),
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

function HeadsUpCard({ monthDate, memberId }: { monthDate: string; memberId?: number }) {
  const q = useQuery({
    queryKey: ["dash-outliers", monthDate, memberId],
    queryFn: () => getOutliers(monthDate, memberId),
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
