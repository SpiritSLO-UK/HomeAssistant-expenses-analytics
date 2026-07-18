import { Fragment, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import Sparkline from "../components/Sparkline";
import WorldMap, { colorForIndex, type MapPlot } from "../components/WorldMap";
import CameraCaptureButton from "../components/CameraCaptureButton";
import { money } from "../lib/money";
import { alertAsync } from "../components/dialogs";
import {
  type CountryBreakdownItem,
  exportCategoriesCsv,
  exportMonthlyCsv,
  getAllowanceSummary,
  getBudgetSummary,
  getBusinessSummary,
  getCategoryBreakdown,
  getCountryBreakdown,
  getDashboardProjects,
  getEnergyOffset,
  getInvestmentSummary,
  getMe,
  listAssets,
  getMemberBreakdown,
  getMissingFx,
  getMonthlySeries,
  getOutliers,
  getProcessingStats,
  getReviewCount,
  getSavingsSummary,
  getSecurityHealth,
  getSummary,
  getTravelTrips,
  getVendorBreakdown,
  listAccounts,
  listMembers,
  uploadReceipt,
  type Member,
  type MonthlyPoint,
  type OutlierItem,
  type TrendMetric,
} from "../api/client";
import {
  getDashboardCardOrder,
  getDashboardView,
  getHiddenDashboardCards,
  setDashboardCardOrder,
  setDashboardView,
  getDashboardMonth,
  setDashboardMonth,
  getDashboardMember,
  setDashboardMember,
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
  { key: "geo", label: "Spending by location" },
  { key: "projects", label: "By project" },
  { key: "members", label: "By member" },
  { key: "savings", label: "Savings" },
  { key: "investments", label: "Investments" },
  { key: "assets", label: "Cars & assets" },
  { key: "energy", label: "Energy cost offset" },
  { key: "budgets", label: "Budgets" },
  { key: "business", label: "Business" },
  { key: "travel", label: "Travel" },
  { key: "allowance", label: "Allowance" },
  { key: "processing", label: "Processing" },
];

const DEFAULT_CARD_ORDER = OPTIONAL_CARDS.map((c) => c.key);

// Saved order, filtered to known cards and with any newly-added cards appended,
// so the order pref survives card additions/removals across releases (#84).
function mergeCardOrder(saved: string[]): string[] {
  const known = new Set(DEFAULT_CARD_ORDER);
  const kept = saved.filter((k) => known.has(k));
  return [...kept, ...DEFAULT_CARD_ORDER.filter((k) => !kept.includes(k))];
}

function downloadOrAlert(p: Promise<void>): void {
  p.catch((e) => alertAsync({ message: String(e instanceof Error ? e.message : e) }));
}

function thisMonth(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

// Drill-down (umbrella principle): every dashboard breakdown links through to the
// transactions behind it. The card is scoped to one month, so we carry that month's
// date range (and any selected member) into the Transactions filter, so the drilled
// list matches the figure that was clicked. `monthDate` is "YYYY-MM-01".
function monthRange(monthDate: string): { date_from: string; date_to: string } {
  const month = monthDate.slice(0, 7); // YYYY-MM
  const [y, m] = month.split("-").map(Number);
  const lastDay = new Date(y, m, 0).getDate(); // m is 1-based → day 0 of next month
  return { date_from: `${month}-01`, date_to: `${month}-${String(lastDay).padStart(2, "0")}` };
}

function txnLink(params: Record<string, string | number | null | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
  }
  const qs = sp.toString();
  return qs ? `/transactions?${qs}` : "/transactions";
}

// Per-device dismissed Heads-up items. Mirrors the defensive localStorage pattern
// used by prefs.ts (and the shared `hafi_` prefix, so "Reset UI preferences" in
// Settings clears these too) but is kept local to this page rather than added to
// the shared prefs module. Whole-card enable/disable already lives in ⚙ Customise
// (the "headsup" show/hide); this adds a per-item clear/tidy affordance.
const HEADSUP_DISMISSED_KEY = "hafi_dashboard_headsup_dismissed";

function readDismissedHeadsUp(): Set<string> {
  try {
    const raw = globalThis.localStorage.getItem(HEADSUP_DISMISSED_KEY);
    if (!raw) return new Set();
    const parsed: unknown = JSON.parse(raw);
    return new Set(Array.isArray(parsed) ? parsed.map(String) : []);
  } catch {
    return new Set();
  }
}

function writeDismissedHeadsUp(keys: Set<string>): void {
  try {
    globalThis.localStorage.setItem(HEADSUP_DISMISSED_KEY, JSON.stringify([...keys]));
  } catch {
    /* localStorage unavailable — dismissals just won't persist */
  }
}

// Stable identity for a heads-up item, so a dismissal survives reloads and only
// re-appears if that same alert recurs (not merely because the list re-ordered).
function headsUpKey(it: OutlierItem): string {
  return [it.type, it.transaction_id ?? "", it.category_id ?? "", it.budget_id ?? "", it.title].join("|");
}

// Quick-add (#user): drop a receipt straight from the dashboard — pick a file, or
// on mobile take a photo (a dedicated camera button, reliable across browsers).
// Receipts are processed + matched automatically (one step); bank statements go
// through the Import page (preview + confirm), linked here.
function QuickAddCard() {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [lastReceiptId, setLastReceiptId] = useState<number | null>(null);
  // Upload runs OCR + matching server-side and returns the finished receipt, so
  // report the actual outcome (matched / needs review) + a link — not a stuck
  // "processing…" that leaves the user wondering if anything happened.
  const upload = useMutation({
    mutationFn: (file: File) => uploadReceipt(file),
    onSuccess: (r) => {
      const matched = (r.matches ?? []).some(
        (m) => m.match_status === "confirmed" || m.match_status === "auto_confirmed",
      );
      if (r.already_imported) setMsg("That receipt was already imported.");
      else if (matched) setMsg("Receipt added ✓ and auto-matched to a transaction.");
      else setMsg("Receipt added ✓ — review/match it on the Receipts page.");
      setLastReceiptId(r.id);
      qc.invalidateQueries({ queryKey: ["receipts"] });
      qc.invalidateQueries({ queryKey: ["review"] });
    },
    onError: (e) => { setLastReceiptId(null); setMsg(String(e instanceof Error ? e.message : e)); },
  });
  const send = (f: File) => { setMsg(null); setLastReceiptId(null); upload.mutate(f); };
  return (
    <div className="card">
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <strong>Quick add</strong>
        <input
          ref={fileRef}
          type="file"
          accept="image/*,application/pdf"
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) send(f);
            e.target.value = "";
          }}
        />
        <button className="btn" disabled={upload.isPending} onClick={() => fileRef.current?.click()}>
          {upload.isPending ? "Uploading…" : "🧾 Add receipt (file)"}
        </button>
        <CameraCaptureButton onCapture={send} disabled={upload.isPending} className="btn" />
        <Link className="btn btn--ghost" to="/import">📄 Import bank statement →</Link>
        {msg && <span className="muted">{msg}</span>}
        {lastReceiptId != null && (
          <Link className="link-btn" to={`/receipts?focus=${lastReceiptId}`}>Open on Receipts →</Link>
        )}
      </div>
    </div>
  );
}

// Each optional dashboard card maps to a module-level card component; the page
// renders them in the user's saved order (#84), filtered by show/hide (#86).
// Cards with no data render null, so the layout stays clean.
function DashboardCard({
  cardKey,
  monthDate,
  view,
  mid,
}: Readonly<{ cardKey: string; monthDate: string; view: string; mid: number | undefined }>) {
  switch (cardKey) {
    case "headsup":
      return <HeadsUpCard monthDate={monthDate} memberId={mid} />;
    case "trends":
      return <TrendsCard monthDate={monthDate} view={view} memberId={mid} />;
    case "categories":
      return <CategoriesCard monthDate={monthDate} view={view} memberId={mid} />;
    case "vendors":
      return <VendorsCard monthDate={monthDate} view={view} memberId={mid} />;
    case "geo":
      return <GeoCard monthDate={monthDate} view={view} memberId={mid} />;
    case "projects":
      return <ProjectsCard memberId={mid} />;
    case "members":
      return <MemberBreakdownCard monthDate={monthDate} />;
    case "savings":
      return <SavingsCard />;
    case "investments":
      return <InvestmentsCard />;
    case "assets":
      return <AssetsCard />;
    case "energy":
      return <EnergyCard monthDate={monthDate} />;
    case "budgets":
      return <BudgetsCard monthDate={monthDate} />;
    case "business":
      return <BusinessCard />;
    case "travel":
      return <TravelCard />;
    case "allowance":
      return <AllowanceCard />;
    case "processing":
      return <ProcessingCard />;
    default:
      return null;
  }
}

// Energy-cost offset (HA). Null unless a source is configured, so it only shows
// for users who've wired up their HA energy sensors. Links through to the page.
function EnergyCard({ monthDate }: Readonly<{ monthDate: string }>) {
  const q = useQuery({ queryKey: ["energy-offset", monthDate], queryFn: () => getEnergyOffset(monthDate) });
  const o = q.data;
  if (!o?.configured) return null;
  return (
    <div className="card">
      <h2 className="card__title"><Link to="/energy">⚡ Energy cost offset</Link></h2>
      <ul className="kv">
        <li><span>Produced</span><span>{o.produced_kwh} kWh</span></li>
        <li><span>Saving</span><span><strong>{money(o.saving)}</strong></span></li>
        <li><span>Net energy cost</span><span>{money(o.net_cost)}</span></li>
      </ul>
    </div>
  );
}

export default function Dashboard() {
  const [month, setMonth] = useState(() => getDashboardMonth() || thisMonth());
  const monthDate = `${month}-01`;

  // Per-device card show/hide (#86) + order (#84).
  const [hidden, setHidden] = useState<Set<string>>(() => getHiddenDashboardCards());
  const [order, setOrder] = useState<string[]>(() => mergeCardOrder(getDashboardCardOrder()));
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
  const moveCard = (key: string, dir: -1 | 1) =>
    setOrder((prev) => {
      const i = prev.indexOf(key);
      const j = i + dir;
      if (i < 0 || j < 0 || j >= prev.length) return prev;
      const next = [...prev];
      [next[i], next[j]] = [next[j], next[i]];
      setDashboardCardOrder(next);
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
  const [memberId, setMemberId] = useState<string>(() => getDashboardMember());
  const mid = memberId ? Number(memberId) : undefined;
  const hasMembers = (members.data?.length ?? 0) > 1;

  // Remember the chosen month + member across reloads (view already persists via
  // chooseView), so a refresh doesn't snap back to the current month.
  useEffect(() => {
    setDashboardMonth(month);
  }, [month]);
  useEffect(() => {
    setDashboardMember(memberId);
  }, [memberId]);

  // Picking a member scopes to that member's accounts and the server ignores the
  // Mine/Shared/All view (member takes precedence). So drop `view` from both the
  // request and the query key when a member is selected — otherwise the key would
  // vary by a `view` the response doesn't actually reflect. `mid` (number) also
  // keeps the member portion of the key the same shape as the other cards.
  const effectiveView = mid ? undefined : view;
  const summary = useQuery({
    queryKey: ["summary", monthDate, effectiveView, mid],
    queryFn: () => getSummary(monthDate, effectiveView, mid),
  });

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
          <input type="month" aria-label="Month to view" value={month} onChange={(e) => setMonth(e.target.value)} />
          <button className="btn btn--ghost" onClick={() => setCustomise((v) => !v)}>
            {customise ? "Done" : "⚙ Customise"}
          </button>
        </div>
      </div>

      {customise && (
        <div className="card">
          <h2 className="card__title">Customise dashboard</h2>
          <p className="muted" style={{ marginTop: 0, fontSize: "0.85rem" }}>
            Show or hide cards and set their order with the arrows. Saved on this device.
          </p>
          <ul className="card-customise">
            {order.map((key, i) => {
              const label = OPTIONAL_CARDS.find((c) => c.key === key)?.label ?? key;
              return (
                <li key={key}>
                  <label className="checkbox">
                    <input type="checkbox" checked={show(key)} onChange={() => toggleCard(key)} /> {label}
                  </label>
                  <span className="card-customise__move">
                    <button
                      className="btn btn--sm btn--ghost"
                      disabled={i === 0}
                      aria-label={`Move ${label} up`}
                      onClick={() => moveCard(key, -1)}
                    >
                      ▲
                    </button>
                    <button
                      className="btn btn--sm btn--ghost"
                      disabled={i === order.length - 1}
                      aria-label={`Move ${label} down`}
                      onClick={() => moveCard(key, 1)}
                    >
                      ▼
                    </button>
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      <SecurityBanner />

      <QuickAddCard />

      <div className="stat-grid">
        <StatCard label="Spend" value={summary.data ? money(summary.data.spend_this_month) : "—"} tone="neg" />
        <StatCard label="Income" value={summary.data ? money(summary.data.income_this_month) : "—"} tone="pos" />
        <StatCard label="Net" value={summary.data ? money(summary.data.net_this_month) : "—"} />
        <StatCard label="Transactions" value={summary.data ? String(summary.data.total_transactions) : "—"} />
      </div>

      {summary.data?.total_transactions === 0 && (
        <div className="card">
          <p className="muted">
            No transactions yet. Head to <Link to="/import">Import</Link> to upload a CSV.
          </p>
        </div>
      )}

      {summary.data && (
        <NeedsAttentionCard
          uncategorised={summary.data.uncategorised_transactions}
          scoped={effectiveView !== undefined && effectiveView !== "all"}
          memberScoped={mid !== undefined}
        />
      )}

      {order.filter(show).map((key) => (
        <Fragment key={key}><DashboardCard cardKey={key} monthDate={monthDate} view={view} mid={mid} /></Fragment>
      ))}
    </div>
  );
}

// One place for "things waiting on you": the review queue, uncategorised
// transactions, and any foreign rows missing an FX rate. Each row links to where
// you clear it. The whole card hides when there's nothing outstanding (it never
// nags). The counts mix scopes: `uncategorised` follows the dashboard view/member
// (it comes from the scoped summary), whereas review + FX are account-wide. When a
// scope is active we label that difference so the numbers aren't read as one total.
function NeedsAttentionCard({
  uncategorised,
  scoped,
  memberScoped,
}: Readonly<{ uncategorised: number; scoped: boolean; memberScoped: boolean }>) {
  const review = useQuery({ queryKey: ["review", "count"], queryFn: getReviewCount });
  const fx = useQuery({ queryKey: ["fx-missing"], queryFn: getMissingFx });
  const reviewOpen = review.data?.open ?? 0;
  const needsRate = fx.data?.needs_rate ?? 0;

  const rows: { key: string; label: string; to: string }[] = [];
  if (reviewOpen > 0)
    rows.push({ key: "review", label: `${reviewOpen} item(s) to review`, to: "/review" });
  if (uncategorised > 0)
    rows.push({ key: "uncat", label: `${uncategorised} uncategorised transaction(s)`, to: "/review?tab=uncategorised" });
  if (needsRate > 0)
    rows.push({ key: "fx", label: `${needsRate} transaction(s) need an exchange rate`, to: "/settings" });

  if (rows.length === 0) return null;
  return (
    <div className="card">
      <h2 className="card__title">Needs attention</h2>
      <ul className="kv">
        {rows.map((r) => (
          <li key={r.key}>
            <span>⚠️ {r.label}</span>
            <Link to={r.to}>Open →</Link>
          </li>
        ))}
      </ul>
      {(scoped || memberScoped) && (
        <p className="muted" style={{ margin: "8px 0 0", fontSize: "0.8rem" }}>
          Uncategorised follows the current {memberScoped ? "member" : "view"}; review and
          exchange-rate counts are across all accounts.
        </p>
      )}
    </div>
  );
}

function ProjectsCard({ memberId }: Readonly<{ memberId?: number }>) {
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
                <Link to={txnLink({ project_id: p.project_id, member_id: memberId })} title={`See ${p.name} transactions`}>
                  {p.name}
                </Link>{" "}
                <span className="tag">{p.status}</span>
              </span>
              <span>
                {money(p.spent)}
                {p.budget ? <span className="muted"> / {money(p.budget)}{pct == null ? "" : ` · ${pct}%`}</span> : ""}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function CategoriesCard({ monthDate, view, memberId }: Readonly<{ monthDate: string; view: string; memberId?: number }>) {
  const q = useQuery({
    queryKey: ["dash-categories", monthDate, view, memberId],
    queryFn: () => getCategoryBreakdown(monthDate, view, memberId),
  });
  const data = q.data ?? [];
  if (data.length === 0) return null; // non-nagging: hide until there's spending (matches the other cards)
  const max = Math.max(1, ...data.map((c) => Number(c.total)));
  const { date_from, date_to } = monthRange(monthDate);
  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h2 className="card__title" style={{ margin: 0 }}>Spending by category</h2>
        <button className="link-btn" onClick={() => downloadOrAlert(exportCategoriesCsv(monthDate))}>
          ⬇ CSV
        </button>
      </div>
      <ul className="bars">
        {data.map((c) => (
          <li key={c.category_id ?? "none"}>
            <div className="bars__row">
              <span className="bars__dot" style={{ background: c.colour ?? "#bbb" }} />
              <Link
                className="bars__label"
                title={`See ${c.name} transactions this month`}
                to={txnLink(
                  c.category_id == null
                    ? { uncategorised: "true", date_from, date_to, member_id: memberId }
                    : { category_id: c.category_id, date_from, date_to, member_id: memberId },
                )}
              >
                {c.name}
              </Link>
              <span className="bars__value">{money(c.total)}</span>
            </div>
            <div className="bars__track">
              <div
                className="bars__fill"
                style={{ width: `${(Number(c.total) / max) * 100}%`, background: c.colour ?? "#bbb" }}
              />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function VendorsCard({ monthDate, view, memberId }: Readonly<{ monthDate: string; view: string; memberId?: number }>) {
  const q = useQuery({
    queryKey: ["dash-vendors", monthDate, view, memberId],
    queryFn: () => getVendorBreakdown(monthDate, view, memberId),
  });
  const data = q.data ?? [];
  if (data.length === 0) return null; // non-nagging: hide until there are vendors (matches the other cards)
  const { date_from, date_to } = monthRange(monthDate);
  return (
    <div className="card">
      <h2 className="card__title">Top vendors</h2>
      <ul className="kv">
        {data.map((v) => (
          <li key={v.vendor_id}>
            <Link
              title={`See ${v.name} transactions this month`}
              to={txnLink({ vendor_id: v.vendor_id, date_from, date_to, member_id: memberId })}
            >
              {v.name}
            </Link>
            <span>{money(v.total)} <span className="muted">· {v.count}</span></span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function GeoCard({ monthDate, view, memberId }: Readonly<{ monthDate: string; view: string; memberId?: number }>) {
  const q = useQuery({
    queryKey: ["dash-geo", monthDate, view, memberId],
    queryFn: () => getCountryBreakdown(monthDate, view, memberId),
  });
  const data = q.data ?? [];
  if (data.length === 0) return null; // non-nagging: nothing to map yet
  const max = Math.max(1, ...data.map((c) => Number(c.total)));
  const { date_from, date_to } = monthRange(monthDate);
  // Top-N by spend, each given a stable colour so its map point matches its legend
  // row. The "Unknown" bucket (no country) gets a neutral swatch and no map point.
  const top: { item: CountryBreakdownItem; color: string }[] = data
    .slice(0, 12)
    .map((item, i) => ({ item, color: item.country_code ? colorForIndex(i) : "var(--muted)" }));
  const plots: MapPlot[] = top
    .filter(({ item }) => item.country_code)
    .map(({ item, color }) => ({
      code: item.country_code as string,
      name: item.name,
      total: Number(item.total),
      count: item.count,
      color,
      href: "#" + txnLink({ country: item.country_code, date_from, date_to, member_id: memberId }),
    }));
  return (
    <div className="card">
      <h2 className="card__title">Spending by location</h2>
      <p className="muted" style={{ marginTop: 0, fontSize: "0.82rem" }}>
        By country — a transaction's own country (tag a trip on Travel), else its vendor's country
        (Vendors page), else inferred from the currency. Bubble size is the amount; click a point or
        a row to see the transactions behind it.
      </p>
      {plots.length > 0 && <WorldMap plots={plots} maxTotal={max} money={(n) => money(String(n))} />}
      <ul className="bars">
        {top.map(({ item: c, color }) => (
          <li key={c.country_code ?? "unknown"}>
            <div className="bars__row">
              <span className="bars__legend">
                <span className="worldmap__swatch" style={{ background: color }} aria-hidden="true" />
                {c.country_code ? (
                  <Link
                    className="bars__label"
                    title={`See ${c.name} transactions this month`}
                    to={txnLink({ country: c.country_code, date_from, date_to, member_id: memberId })}
                  >
                    {c.flag} {c.name}
                  </Link>
                ) : (
                  <span className="bars__label">{c.flag} {c.name}</span>
                )}
              </span>
              <span className="bars__value">{money(c.total)} <span className="muted">· {c.count}</span></span>
            </div>
            <div className="bars__track">
              <div className="bars__fill" style={{ width: `${(Number(c.total) / max) * 100}%`, background: color }} />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function MemberBreakdownCard({ monthDate }: Readonly<{ monthDate: string }>) {
  const q = useQuery({ queryKey: ["dash-by-member", monthDate], queryFn: () => getMemberBreakdown(monthDate) });
  const rows = (q.data?.members ?? []).filter((r) => Number(r.spend) > 0);
  if (rows.length < 2) return null; // a breakdown only makes sense with ≥2 spenders
  rows.sort((a, b) => Number(b.spend) - Number(a.spend));
  const max = Math.max(1, ...rows.map((r) => Number(r.spend)));
  const { date_from, date_to } = monthRange(monthDate);
  return (
    <div className="card">
      <h2 className="card__title">Spending by member</h2>
      <ul className="bars">
        {rows.map((r) => {
          // One nullish check drives the key, the label branch, and the colour, so
          // the "Shared / unassigned" row can't render one way but be coloured the
          // other (the member_id shape was previously compared inconsistently).
          const isShared = r.member_id == null;
          return (
            <li key={isShared ? "shared" : r.member_id}>
              <div className="bars__row">
                {isShared ? (
                  <span className="bars__label">
                    {r.display_name}
                    {r.role && <span className="muted"> · {r.role}</span>}
                  </span>
                ) : (
                  <Link
                    className="bars__label"
                    title={`See ${r.display_name}'s transactions this month`}
                    to={txnLink({ member_id: r.member_id, date_from, date_to })}
                  >
                    {r.display_name}
                    {r.role && <span className="muted"> · {r.role}</span>}
                  </Link>
                )}
                <span className="bars__value">{money(r.spend)}</span>
              </div>
              <div className="bars__track">
                <div
                  className="bars__fill"
                  style={{
                    width: `${(Number(r.spend) / max) * 100}%`,
                    background: isShared ? "var(--muted, #6b7280)" : "var(--sidebar-active, #3b82f6)",
                  }}
                />
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// --- Per-domain summary cards (#83). Each is self-contained and renders null
// until that area has data, so the dashboard only shows domains in use. ---

function CardHead({ title, to }: Readonly<{ title: string; to: string }>) {
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
        <li><span>Total saved</span><span>{money(s.total_savings)}</span></li>
        <li><span>Accounts</span><span>{s.accounts.length}</span></li>
        {topGoal && (
          <li><span>Top goal · {topGoal.name}</span><span>{topGoal.percent}%</span></li>
        )}
      </ul>
    </div>
  );
}

function InvestmentsCard() {
  const q = useQuery({ queryKey: ["dash-investments"], queryFn: getInvestmentSummary });
  const s = q.data;
  if (!s || s.accounts.length === 0) return null; // non-nagging: no card until there's an account
  const gain = s.total_gain == null ? null : Number(s.total_gain);
  return (
    <div className="card">
      <CardHead title="Investments" to="/investments" />
      <ul className="kv">
        <li><span>Portfolio value</span><span>{money(s.total_value)}</span></li>
        {gain != null && (
          <li>
            <span>Unrealised gain</span>
            <span className={gain >= 0 ? "amt--pos" : "amt--neg"}>
              {gain >= 0 ? "+" : ""}{money(s.total_gain!)}
              {s.total_gain_pct != null && ` · ${gain >= 0 ? "+" : ""}${s.total_gain_pct}%`}
            </span>
          </li>
        )}
        <li><span>Accounts</span><span>{s.accounts.length}</span></li>
      </ul>
    </div>
  );
}

function AssetsCard() {
  const q = useQuery({ queryKey: ["dash-assets"], queryFn: () => listAssets() });
  const items = q.data ?? [];
  if (items.length === 0) return null; // non-nagging: no card until there's an asset
  const icon: Record<string, string> = { car: "🚗", home: "🏠", other: "📦" };
  return (
    <div className="card">
      <CardHead title="Cars & assets" to="/assets" />
      <ul className="kv">
        {items.slice(0, 5).map((a) => (
          <li key={a.id}>
            <span>{icon[a.kind] ?? "📦"} {a.name}</span>
            <span>
              {a.car?.avg_economy == null ? (
                money(a.total_cost)
              ) : (
                <>{a.car.avg_economy} {a.car.economy_unit} <span className="muted">· {money(a.total_cost)}</span></>
              )}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function BudgetsCard({ monthDate }: Readonly<{ monthDate: string }>) {
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
              {money(b.spent)} <span className="muted">/ {money(b.amount)} · {b.percent}%</span>
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
        <li><span>Business spend</span><span>{money(s.total)}</span></li>
        <li><span>Reclaimable VAT</span><span>{money(s.vat)}</span></li>
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
        <li><span>Spend abroad</span><span>{money(String(total))}</span></li>
        {trips.slice(0, 3).map((t) => (
          <li key={t.transaction_ids[0] ?? t.label}>
            <span>{t.label}</span>
            <span>{money(t.base_total)}</span>
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

function ChildAllowanceRow({ child }: Readonly<{ child: Member }>) {
  const q = useQuery({ queryKey: ["dash-allowance", child.id], queryFn: () => getAllowanceSummary(child.id) });
  const s = q.data;
  const budget = s?.budgets[0];
  const fallback = s ? <span className="muted">{s.items.length} item(s)</span> : <span className="muted">…</span>;
  return (
    <li>
      <span>{child.display_name}</span>
      <span>
        {budget ? (
          <>
            {money(budget.spent)} <span className="muted">/ {money(budget.amount)}</span>
          </>
        ) : (
          fallback
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

type TrendKey = "spend" | "income" | "net";

function TrendMini({ label, values, metric, currency, key_ }: Readonly<{
  label: string;
  values: number[];
  metric: TrendMetric | undefined;
  currency: string;
  key_: TrendKey;
}>) {
  const current = values.length ? values[values.length - 1] : 0;
  // For spend, going down is good; for income/net, going up is good.
  const dir = metric?.direction ?? "flat";
  const trendIsGood = key_ === "spend" ? dir === "down" : dir === "up";
  const good = dir === "flat" ? null : trendIsGood;
  const goodColour = good ? "#3aa55a" : "#e05555";
  const arrowColour = good == null ? "var(--muted, #888)" : goodColour;
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

function TrendsCard({ monthDate, view, memberId }: Readonly<{ monthDate: string; view: string; memberId?: number }>) {
  const q = useQuery({
    queryKey: ["dash-monthly", monthDate, view, memberId],
    queryFn: () => getMonthlySeries(6, monthDate, view, memberId),
  });
  const data = q.data;
  if (!data || data.months.length < 2) return null;
  const num = (m: MonthlyPoint, k: TrendKey) => Number(m[k]);
  const keys: Array<TrendKey> = ["spend", "income", "net"];
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

function HeadsUpCard({ monthDate, memberId }: Readonly<{ monthDate: string; memberId?: number }>) {
  const q = useQuery({
    queryKey: ["dash-outliers", monthDate, memberId],
    queryFn: () => getOutliers(monthDate, memberId),
  });
  // Per-item dismiss (#user): a handled heads-up can be cleared and stays cleared
  // across reloads (persisted per-device). Enable/disable of the whole card lives
  // in ⚙ Customise (the "headsup" show/hide toggle) — this adds the tidy/clear.
  const [dismissed, setDismissed] = useState<Set<string>>(() => readDismissedHeadsUp());
  const items = q.data?.items ?? [];
  if (items.length === 0) return null; // nothing to flag → no card (non-nagging)

  const visible = items.filter((it) => !dismissed.has(headsUpKey(it)));
  const clearedCount = items.length - visible.length;

  const dismiss = (it: OutlierItem) =>
    setDismissed((prev) => {
      const next = new Set(prev);
      next.add(headsUpKey(it));
      writeDismissedHeadsUp(next);
      return next;
    });
  const clearAll = () =>
    setDismissed((prev) => {
      const next = new Set(prev);
      for (const it of items) next.add(headsUpKey(it));
      writeDismissedHeadsUp(next);
      return next;
    });
  const restore = () =>
    setDismissed((prev) => {
      const next = new Set(prev);
      for (const it of items) next.delete(headsUpKey(it));
      writeDismissedHeadsUp(next);
      return next;
    });

  return (
    <div className="card" style={{ borderLeft: "3px solid #e0a800" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <h2 className="card__title" style={{ margin: 0 }}>Heads-up</h2>
        <span style={{ display: "flex", gap: 12, alignItems: "baseline" }}>
          {visible.length > 0 && (
            <button className="link-btn" onClick={clearAll}>Clear all</button>
          )}
          {clearedCount > 0 && (
            <button className="link-btn" onClick={restore}>Restore ({clearedCount})</button>
          )}
        </span>
      </div>
      {visible.length === 0 ? (
        <p className="muted" style={{ margin: "8px 0 0", fontSize: "0.85rem" }}>
          All caught up — {clearedCount} dismissed.
        </p>
      ) : (
        <ul style={{ listStyle: "none", padding: 0, margin: "8px 0 0", display: "flex", flexDirection: "column", gap: 8 }}>
          {visible.map((it) => (
            <li
              key={headsUpKey(it)}
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: 8,
                borderLeft: `3px solid ${it.severity === "warn" ? "#e05555" : "#e0a800"}`,
                paddingLeft: 10,
              }}
            >
              <div style={{ flex: 1 }}>
                <div><strong>{it.severity === "warn" ? "⚠️" : "💡"} {it.title}</strong></div>
                <div className="muted" style={{ fontSize: "0.85rem" }}>{it.detail}</div>
              </div>
              <button
                className="link-btn"
                title="Dismiss this heads-up"
                aria-label={`Dismiss: ${it.title}`}
                onClick={() => dismiss(it)}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
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

function StatCard({ label, value, tone }: Readonly<{ label: string; value: string; tone?: "pos" | "neg" }>) {
  const negClass = tone === "neg" ? " amt--neg" : "";
  const valueClass = tone === "pos" ? " amt--pos" : negClass;
  return (
    <div className="stat">
      <div className="stat__label">{label}</div>
      <div className={"stat__value" + valueClass}>
        {value}
      </div>
    </div>
  );
}
