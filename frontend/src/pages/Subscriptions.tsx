import { useState } from "react";
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

const STATUS_LABEL: Record<string, string> = {
  active: "active",
  possible: "possible",
  cancelled: "cancelled",
  ignored: "ignored",
};

export default function Subscriptions() {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);

  const subs = useQuery({ queryKey: ["subscriptions"], queryFn: listSubscriptions });
  const dash = useQuery({ queryKey: ["dashboard-subscriptions"], queryFn: () => getDashboardSubscriptions() });
  const alerts = useQuery({ queryKey: ["subscription-alerts"], queryFn: () => getSubscriptionAlerts(7) });
  const settings = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const base = settings.data?.base_currency ?? "GBP";

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
    onSuccess: () => invalidate(),
    onError: (e) => setErr(String(e)),
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
        <button className="btn btn--ghost" disabled={detect.isPending} onClick={() => detect.mutate()}>
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
                  {s.days_until != null && s.days_until <= 0 ? "due now" : `due in ${s.days_until} day(s)`}
                  {" "}({s.next_expected_date}) — {s.amount} {base}
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
                {subs.data.map((s: Subscription) => (
                  <tr key={s.id} style={{ opacity: s.status === "cancelled" || s.status === "ignored" ? 0.55 : 1 }}>
                    <td>{s.name}</td>
                    <td className="num">{s.amount} {s.currency === base ? "" : s.currency}</td>
                    <td>{s.frequency}</td>
                    <td className="num">{s.monthly_amount} {base}</td>
                    <td>{s.next_expected_date ?? "—"}</td>
                    <td>{s.confidence_score == null ? "—" : `${Math.round(s.confidence_score * 100)}%`}</td>
                    <td>
                      <select value={s.status} disabled={setStatus.isPending} onChange={(e) => setStatus.mutate({ id: s.id, status: e.target.value })}>
                        {SUBSCRIPTION_STATUSES.map((st) => (
                          <option key={st} value={st}>{STATUS_LABEL[st]}</option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <button className="link-btn" onClick={() => { if (confirm(`Delete "${s.name}"?`)) remove.mutate(s.id); }}>
                        delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
