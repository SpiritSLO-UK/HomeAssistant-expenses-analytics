import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  SUBSCRIPTION_STATUSES,
  deleteSubscription,
  detectSubscriptions,
  getDashboardSubscriptions,
  getSettings,
  getSubscriptionAlerts,
  listSubscriptions,
  updateSubscription,
  type Subscription,
} from "../api/client";
import { useConfirm } from "../components/dialogs";
import { formatDate, normaliseDateFormat } from "../lib/date";

// "due in null day(s)" guard: days_until can be null when the next date is
// unknown — show a soft label instead.
function dueLabel(days: number | null): string {
  if (days == null) return "due soon";
  if (days <= 0) return "due now";
  return `due in ${days} day(s)`;
}

const STATUS_LABEL: Record<string, string> = {
  active: "active",
  possible: "possible",
  cancelled: "cancelled",
  ignored: "ignored",
};

type SortKey = "monthly" | "next" | "name";

// null next-due dates sort last regardless of direction.
function nextDueRank(s: Subscription): number {
  if (!s.next_expected_date) return Number.POSITIVE_INFINITY;
  const t = Date.parse(s.next_expected_date);
  return Number.isNaN(t) ? Number.POSITIVE_INFINITY : t;
}

function compareSubs(a: Subscription, b: Subscription, key: SortKey): number {
  if (key === "name") return a.name.localeCompare(b.name);
  if (key === "monthly") return Number(b.monthly_amount) - Number(a.monthly_amount);
  return nextDueRank(a) - nextDueRank(b);
}

export default function Subscriptions() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [err, setErr] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("monthly");
  const [statusFilter, setStatusFilter] = useState<string>("all");

  const subs = useQuery({ queryKey: ["subscriptions"], queryFn: listSubscriptions });
  const dash = useQuery({ queryKey: ["dashboard-subscriptions"], queryFn: () => getDashboardSubscriptions() });
  const alerts = useQuery({ queryKey: ["subscription-alerts"], queryFn: () => getSubscriptionAlerts(7) });
  const settings = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const base = settings.data?.base_currency ?? "GBP";
  const dateFmt = normaliseDateFormat(settings.data?.date_format);

  // Client-side filter + sort over the already-loaded list (keeps it simple —
  // no extra requests). The annualized total reflects what's shown.
  const visible = useMemo(() => {
    const list = subs.data ?? [];
    const filtered = statusFilter === "all" ? list : list.filter((s) => s.status === statusFilter);
    return [...filtered].sort((a, b) => compareSubs(a, b, sortKey));
  }, [subs.data, statusFilter, sortKey]);

  const annualTotal = useMemo(
    () => visible.reduce((sum, s) => sum + Number(s.monthly_amount) * 12, 0),
    [visible],
  );

  // Every mutation's onSuccess calls this, so clearing the error here drops a stale
  // banner after a later success (FE-3).
  const invalidate = () => {
    setErr(null);
    qc.invalidateQueries({ queryKey: ["subscriptions"] });
    qc.invalidateQueries({ queryKey: ["dashboard-subscriptions"] });
    qc.invalidateQueries({ queryKey: ["subscription-alerts"] });
  };

  const detect = useMutation({
    mutationFn: detectSubscriptions,
    onSuccess: () => invalidate(),
    onError: (e) => setErr(String(e)),
  });
  const setStatus = useMutation({
    mutationFn: (v: { id: number; status: string }) => updateSubscription(v.id, { status: v.status }),
    // Optimistic status change with snapshot + rollback (mirrors Accounts.tsx).
    onMutate: async (v: { id: number; status: string }) => {
      setErr(null);
      await qc.cancelQueries({ queryKey: ["subscriptions"] });
      const previous = qc.getQueryData<Subscription[]>(["subscriptions"]);
      qc.setQueryData<Subscription[]>(["subscriptions"], (list) =>
        list?.map((s) => (s.id === v.id ? { ...s, status: v.status } : s)),
      );
      return { previous };
    },
    onError: (e, _v, ctx) => {
      if (ctx?.previous) qc.setQueryData(["subscriptions"], ctx.previous);
      setErr(String(e));
    },
    onSettled: () => invalidate(),
  });
  const remove = useMutation({
    mutationFn: (id: number) => deleteSubscription(id),
    onSuccess: () => invalidate(),
    onError: (e) => setErr(String(e)),
  });

  return (
    <div className="page">
      <div className="page__head">
        <h1 className="page__title">Subscriptions</h1>
        <button className="btn btn--ghost" type="button" disabled={detect.isPending} onClick={() => detect.mutate()}>
          {detect.isPending ? "Detecting…" : "Detect now"}
        </button>
      </div>
      <p className="muted">
        Recurring payments detected from your transactions (same vendor, regular interval, steady amount).
        Detection also runs automatically after each import.
      </p>
      {err && <p className="status status--error">{err}</p>}

      {dash.data && (
        <div className="card">
          <h2 className="card__title">Monthly cost</h2>
          <p style={{ fontSize: "1.4rem", margin: 0 }}>
            <strong>{dash.data.monthly_total} {base}</strong>{" "}
            <span className="muted">/ month · {dash.data.count} active subscription(s)</span>
          </p>
        </div>
      )}

      {alerts.data && (alerts.data.upcoming.length > 0 || alerts.data.overdue.length > 0) && (
        <div className="card" style={{ borderLeft: "3px solid #e0a800" }}>
          <h2 className="card__title">Alerts</h2>
          <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 6 }}>
            {alerts.data.overdue.map((s) => (
              <li key={`o-${s.id}`}>
                ⚠️ <strong>{s.name}</strong>{" "}
                <span className="muted">
                  expected {s.expected_date} ({s.days_overdue} day(s) ago) — {s.amount} {base}. Missed, or cancel it?
                </span>
              </li>
            ))}
            {alerts.data.upcoming.map((s) => (
              <li key={`u-${s.id}`}>
                🔔 <strong>{s.name}</strong>{" "}
                <span className="muted">
                  {dueLabel(s.days_until)}
                  {" "}({formatDate(s.next_expected_date, dateFmt)}) — {s.amount} {base}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="card">
        {subs.isLoading && <p className="muted">Loading…</p>}
        {subs.data?.length === 0 && (
          <p className="muted">
            No subscriptions detected yet. Import a few months of statements (or click <strong>Detect now</strong>) — recurring charges show up here.
          </p>
        )}
        {subs.data && subs.data.length > 0 && (
          <>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end", marginBottom: 12 }}>
              <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <span className="muted">Sort by</span>
                <select value={sortKey} onChange={(e) => setSortKey(e.target.value as SortKey)}>
                  <option value="monthly">Per month (high→low)</option>
                  <option value="next">Next due (soonest)</option>
                  <option value="name">Name (A→Z)</option>
                </select>
              </label>
              <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <span className="muted">Status</span>
                <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
                  <option value="all">all</option>
                  {SUBSCRIPTION_STATUSES.map((st) => (
                    <option key={st} value={st}>{STATUS_LABEL[st]}</option>
                  ))}
                </select>
              </label>
              <p style={{ margin: 0, marginLeft: "auto" }}>
                <span className="muted">Annualized (shown): </span>
                <strong>{annualTotal.toFixed(2)} {base}</strong>{" "}
                <span className="muted">/ year</span>
              </p>
            </div>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Subscription</th>
                    <th className="num">Amount</th>
                    <th>Frequency</th>
                    <th className="num">Per month</th>
                    <th>Next due</th>
                    <th>Confidence</th>
                    <th>Status</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((s: Subscription) => (
                    <tr key={s.id} style={{ opacity: s.status === "cancelled" || s.status === "ignored" ? 0.55 : 1 }}>
                      <td>{s.name}</td>
                      <td className="num">{s.amount} {s.currency === base ? "" : s.currency}</td>
                      <td>{s.frequency}</td>
                      <td className="num">{s.monthly_amount} {base}</td>
                      <td>{s.next_expected_date ? formatDate(s.next_expected_date, dateFmt) : "—"}</td>
                      <td>{s.confidence_score == null ? "—" : `${Math.round(s.confidence_score * 100)}%`}</td>
                      <td>
                        <select value={s.status} onChange={(e) => setStatus.mutate({ id: s.id, status: e.target.value })}>
                          {SUBSCRIPTION_STATUSES.map((st) => (
                            <option key={st} value={st}>{STATUS_LABEL[st]}</option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <button className="link-btn" type="button" onClick={async () => { if (await confirm({ message: `Delete "${s.name}"?`, confirmLabel: "Delete", danger: true })) remove.mutate(s.id); }}>
                          delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
