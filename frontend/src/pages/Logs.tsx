import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  getMe,
  exportAuditLogCsv,
  listActivityLog,
  listAiRequests,
  listAuditActions,
  type ActivityLogFilters,
  type AuditLogRow,
} from "../api/client";
import { alertAsync } from "../components/dialogs";

// Kick off a file download and surface any failure in a modal rather than
// swallowing it (mirrors the Dashboard CSV-export handler).
function downloadOrAlert(p: Promise<void>): void {
  p.catch((e) => alertAsync({ message: String(e instanceof Error ? e.message : e) }));
}

function when(iso: string): string {
  return iso.replace("T", " ").slice(0, 16);
}

// Wait ~300ms after the last keystroke before a text filter hits the API, so
// typing fires one request instead of one per character (mirrors Transactions).
function useDebounced(value: string, ms = 300): string {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = globalThis.setTimeout(() => setDebounced(value), ms);
    return () => globalThis.clearTimeout(id);
  }, [value, ms]);
  return debounced;
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
  // `details` is usually an object, but legacy / malformed rows may carry a
  // string or other primitive — spreading those would crash the render, so
  // stringify anything non-object rather than destructuring it.
  if (typeof row.details !== "object") return stringifyVal(row.details);
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
  // Only fire the admin-only log queries once `me` has loaded AND the caller is
  // the owner/admin — otherwise the pre-guard render (me still loading) would
  // trigger forbidden requests for a non-admin/unauthenticated visitor.
  const authorized = me.data?.is_admin === true;

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

      <ActivityCard includeArchived={includeArchived} authorized={authorized} />
      <AiRequestsCard includeArchived={includeArchived} authorized={authorized} />
    </div>
  );
}

function ActivityCard({ includeArchived, authorized }: Readonly<{ includeArchived: boolean; authorized: boolean }>) {
  const [action, setAction] = useState("");
  const [limit, setLimit] = useState(100);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [text, setText] = useState("");
  const [actor, setActor] = useState("");
  const actions = useQuery({ queryKey: ["audit-actions"], queryFn: listAuditActions, enabled: authorized });

  // All filters are applied server-side (in SQL), so results and the CSV export
  // always agree. The text inputs are debounced to avoid a request per keystroke.
  const q = useDebounced(text.trim());
  const actorQ = useDebounced(actor.trim());
  const filters: ActivityLogFilters = {
    action: action || undefined,
    limit,
    includeArchived,
    q: q || undefined,
    actor: actorQ || undefined,
    dateFrom: dateFrom || undefined,
    dateTo: dateTo || undefined,
  };
  const log = useQuery({
    queryKey: ["activity-log", filters],
    queryFn: () => listActivityLog(filters),
    enabled: authorized,
  });
  const rows = log.data ?? [];
  const filtered = Boolean(action || dateFrom || dateTo || q || actorQ);

  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <h2 className="card__title" style={{ margin: 0 }}>Activity</h2>
        <div className="form-row" style={{ gap: 8 }}>
          <button
            type="button"
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
          <button className="btn btn--sm" type="button" onClick={() => log.refetch()} disabled={log.isFetching}>
            {log.isFetching ? "…" : "Refresh"}
          </button>
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            title="Download the activity log as CSV (honours all the filters above)"
            onClick={() => downloadOrAlert(exportAuditLogCsv(filters))}
          >
            ⬇ Download CSV
          </button>
        </div>
      </div>

      <div className="form-row" style={{ gap: 8, marginTop: 8, flexWrap: "wrap" }}>
        <label className="muted" style={{ display: "flex", alignItems: "center", gap: 4, fontSize: "0.82rem" }}>
          From{" "}
          <input
            type="date"
            value={dateFrom}
            max={dateTo || undefined}
            onChange={(e) => setDateFrom(e.target.value)}
            title="Only show entries on or after this date"
            aria-label="Only show entries on or after this date"
          />
        </label>
        <label className="muted" style={{ display: "flex", alignItems: "center", gap: 4, fontSize: "0.82rem" }}>
          To{" "}
          <input
            type="date"
            value={dateTo}
            min={dateFrom || undefined}
            onChange={(e) => setDateTo(e.target.value)}
            title="Only show entries on or before this date"
            aria-label="Only show entries on or before this date"
          />
        </label>
        <input
          type="search"
          value={actor}
          placeholder="Who (actor)…"
          onChange={(e) => setActor(e.target.value)}
          title="Filter by who did it (name substring, any case)"
          aria-label="Filter by actor name"
          style={{ flex: "0 1 9rem", minWidth: "7rem" }}
        />
        <input
          type="search"
          value={text}
          placeholder="Search action / details…"
          onChange={(e) => setText(e.target.value)}
          title="Free-text search over the action name and details"
          aria-label="Search the activity log"
          style={{ flex: "1 1 12rem", minWidth: "10rem" }}
        />
      </div>

      {log.isLoading && <p className="muted">Loading…</p>}
      {log.isError && (
        <p className="status status--error">Couldn’t load the activity log. {String(log.error)}</p>
      )}
      {log.data && rows.length === 0 && (
        <p className="muted">No matching activity{filtered ? " for these filters" : ""}.</p>
      )}
      {rows.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr><th>When</th><th>Who</th><th>Action</th><th>Item</th><th>Details</th></tr>
            </thead>
            <tbody>
              {rows.map((row) => {
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

function AiRequestsCard({ includeArchived, authorized }: Readonly<{ includeArchived: boolean; authorized: boolean }>) {
  const requests = useQuery({
    queryKey: ["ai-requests", includeArchived],
    queryFn: () => listAiRequests({ includeArchived }),
    enabled: authorized,
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
