import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  getMe,
  listActivityLog,
  listAiRequests,
  listAuditActions,
  type AuditLogRow,
} from "../api/client";

function when(iso: string): string {
  return iso.replace("T", " ").slice(0, 16);
}

// Important consent/privacy decisions are recorded as `decision:<kind>` so each
// kind is its own filterable entry. We group them under a "Decisions" optgroup,
// separate from ordinary CRUD / api_call actions, and give each a friendly label.
function isDecisionAction(action: string): boolean {
  return action === "decision" || action.startsWith("decision:");
}

const DECISION_LABELS: Record<string, string> = {
  "decision:ai_mode": "AI mode change",
  "decision:ocr": "Receipt OCR on/off",
  "decision:fx": "Online exchange rates on/off",
  "decision:image": "Image sent to AI",
};

// Friendly label for a decision action; falls back to a tidied version of any
// unknown / legacy `decision[:kind]` value.
function decisionLabel(action: string): string {
  if (DECISION_LABELS[action]) return DECISION_LABELS[action];
  const kind = action.includes(":") ? action.slice(action.indexOf(":") + 1) : "";
  return kind ? kind.replace(/_/g, " ") : "Decision";
}

// Render an unknown detail value without risking "[object Object]": pass strings
// through, stringify primitives, JSON anything else.
function stringifyVal(v: unknown): string {
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v) ?? "";
}

function describe(row: AuditLogRow): string {
  if (!row.details) return "";
  // Decisions carry a human-readable `summary`; show it first, then any extras.
  const { summary, ...rest } = row.details as Record<string, unknown>;
  const tail = Object.entries(rest)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${k}: ${stringifyVal(v)}`)
    .join(" · ");
  return [summary ? stringifyVal(summary) : "", tail].filter(Boolean).join(" — ");
}

export default function Logs() {
  const me = useQuery({ queryKey: ["me"], queryFn: getMe });
  const [includeArchived, setIncludeArchived] = useState(false);

  if (me.data && !me.data.is_admin) {
    return (
      <div className="page">
        <div className="page__head">
          <h1 className="page__title">Logs</h1>
        </div>
        <div className="card">
          <p className="status status--warn">The activity log is visible to the household owner only.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page__head">
        <h1 className="page__title">Logs</h1>
        <label className="muted" style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "0.85rem" }}>
          <input
            type="checkbox"
            checked={includeArchived}
            onChange={(e) => setIncludeArchived(e.target.checked)}
          />{" "}
          Include archived
        </label>
      </div>
      <p className="muted">
        A record of important actions taken in the app. Low-level runtime and debug logs are written
        to the Home Assistant add-on <strong>Log</strong> panel (set the level with the <code>log_level</code>{" "}
        option), not stored here. Entries aged out by data retention are hidden unless you tick{" "}
        <strong>Include archived</strong>.
      </p>

      <ActivityCard includeArchived={includeArchived} />
      <AiRequestsCard includeArchived={includeArchived} />
    </div>
  );
}

function ActivityCard({ includeArchived }: Readonly<{ includeArchived: boolean }>) {
  const [action, setAction] = useState("");
  const [limit, setLimit] = useState(100);
  const actions = useQuery({ queryKey: ["audit-actions"], queryFn: listAuditActions });
  const log = useQuery({
    queryKey: ["activity-log", action, limit, includeArchived],
    queryFn: () => listActivityLog({ action: action || undefined, limit, includeArchived }),
  });

  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <h2 className="card__title" style={{ margin: 0 }}>Activity</h2>
        <div className="form-row" style={{ gap: 8 }}>
          <button
            className={"btn btn--sm" + (action === "decision" ? "" : " btn--ghost")}
            title="Show only important AI / cloud / privacy decisions"
            onClick={() => setAction(action === "decision" ? "" : "decision")}
          >
            🔑 Decisions
          </button>
          <select value={action} onChange={(e) => setAction(e.target.value)} title="Filter by action">
            <option value="">All actions</option>
            {(() => {
              const all = actions.data ?? [];
              const decisions = all.filter(isDecisionAction);
              const other = all.filter((a) => !isDecisionAction(a));
              return (
                <>
                  {decisions.length > 0 && (
                    <optgroup label="Decisions">
                      {/* "decision" (no kind) prefix-matches every decision — the
                          same value the 🔑 Decisions toggle uses. */}
                      <option value="decision">All decisions</option>
                      {decisions
                        .filter((a) => a !== "decision")
                        .map((a) => <option key={a} value={a}>{decisionLabel(a)}</option>)}
                    </optgroup>
                  )}
                  {other.length > 0 && (
                    <optgroup label="Other actions">
                      {other.map((a) => <option key={a} value={a}>{a}</option>)}
                    </optgroup>
                  )}
                </>
              );
            })()}
          </select>
          <select value={limit} onChange={(e) => setLimit(Number(e.target.value))} title="How many to show">
            {[50, 100, 250, 500].map((n) => (
              <option key={n} value={n}>last {n}</option>
            ))}
          </select>
          <button className="btn btn--sm" onClick={() => log.refetch()} disabled={log.isFetching}>
            {log.isFetching ? "…" : "Refresh"}
          </button>
        </div>
      </div>

      {log.isLoading && <p className="muted">Loading…</p>}
      {log.data?.length === 0 && (
        <p className="muted">No activity recorded yet{action ? " for this action" : ""}.</p>
      )}
      {log.data && log.data.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr><th>When</th><th>Who</th><th>Action</th><th>Item</th><th>Details</th></tr>
            </thead>
            <tbody>
              {log.data.map((row) => {
                const entityRef = row.entity_id == null ? "" : ` #${row.entity_id}`;
                return (
                  <tr key={row.id}>
                    <td style={{ whiteSpace: "nowrap" }}>{when(row.created_at)}</td>
                    <td>{row.actor ?? "system"}</td>
                    <td><code>{row.action}</code></td>
                    <td className="muted">
                      {row.entity_type ? row.entity_type + entityRef : "—"}
                    </td>
                    <td className="muted" style={{ fontSize: "0.82rem" }}>{describe(row)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function AiRequestsCard({ includeArchived }: Readonly<{ includeArchived: boolean }>) {
  const requests = useQuery({
    queryKey: ["ai-requests", includeArchived],
    queryFn: () => listAiRequests({ includeArchived }),
  });
  if (!requests.data || requests.data.length === 0) return null;

  return (
    <div className="card">
      <h2 className="card__title">AI requests</h2>
      <p className="muted" style={{ fontSize: "0.82rem", marginTop: 0 }}>
        Every call to the AI gateway is logged here (spec §22.6) — including what ran locally vs. cloud,
        and whether it was approved.
      </p>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>When</th><th>Task</th><th>Provider</th><th>Mode</th><th>Approval</th><th>Status</th></tr>
          </thead>
          <tbody>
            {requests.data.map((r) => (
              <tr key={r.id}>
                <td style={{ whiteSpace: "nowrap" }}>{when(r.created_at)}</td>
                <td>{r.task_type}</td>
                <td>{r.provider}</td>
                <td>{r.privacy_mode}</td>
                <td>{r.approval_status}</td>
                <td>{r.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
