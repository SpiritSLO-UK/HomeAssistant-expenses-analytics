import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  PROJECT_STATUSES,
  createProject,
  deleteProject,
  getDashboardProjects,
  getProjectSummary,
  getSettings,
  listTransactions,
  type ProjectTotal,
  type TransactionListResponse,
} from "../api/client";

export default function Projects() {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);

  const projects = useQuery({ queryKey: ["dashboard-projects"], queryFn: () => getDashboardProjects() });
  const settings = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const base = settings.data?.base_currency ?? "GBP";

  const remove = useMutation({
    mutationFn: (id: number) => deleteProject(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["dashboard-projects"] }),
    onError: (e) => setErr(String(e)),
  });

  return (
    <div className="page">
      <h1 className="page__title">Projects</h1>
      {err && <p className="status status--error">{err}</p>}

      <NewProject
        base={base}
        onError={setErr}
        onCreated={() => qc.invalidateQueries({ queryKey: ["dashboard-projects"] })}
      />

      <div className="card">
        <h2 className="card__title">Your projects</h2>
        {projects.isLoading && <p className="muted">Loading…</p>}
        {projects.data && projects.data.length === 0 && (
          <p className="muted">No projects yet. Create one above (e.g. “Bathroom renovation”), then assign transactions to it on the Transactions page.</p>
        )}
        <div className="project-list">
          {projects.data?.map((p) => (
            <Fragment key={p.project_id}>
              <ProjectRow
                p={p}
                base={base}
                open={openId === p.project_id}
                onToggle={() => setOpenId(openId === p.project_id ? null : p.project_id)}
                onDelete={() => {
                  if (confirm(`Delete project "${p.name}"? (transactions are kept, just unlinked)`)) {
                    remove.mutate(p.project_id);
                  }
                }}
              />
              {openId === p.project_id && <ProjectDetail id={p.project_id} base={base} />}
            </Fragment>
          ))}
        </div>
      </div>
    </div>
  );
}

function ProjectRow({
  p,
  base,
  open,
  onToggle,
  onDelete,
}: Readonly<{
  p: ProjectTotal;
  base: string;
  open: boolean;
  onToggle: () => void;
  onDelete: () => void;
}>) {
  const pct = p.percent ?? null;
  const over = pct != null && pct > 100;
  const colour = over ? "#c0392b" : pct != null && pct >= 80 ? "#d8930a" : "#3a9b5c";
  return (
    <div style={{ padding: "10px 0", borderBottom: "1px solid rgba(127,127,127,0.2)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <div>
          <button className="link-btn" onClick={onToggle} style={{ fontWeight: 600 }}>
            {open ? "▾ " : "▸ "}{p.name}
          </button>{" "}
          <span className="tag">{p.status}</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span className="muted">{p.spent} {base} spent{p.budget ? ` / ${p.budget}` : ""}</span>
          <button className="link-btn" onClick={onDelete}>delete</button>
        </div>
      </div>
      {p.budget && pct != null && (
        <div style={{ marginTop: 6, height: 8, borderRadius: 4, background: "rgba(127,127,127,0.22)", overflow: "hidden" }} title={`${pct}%`}>
          <div style={{ width: `${Math.min(pct, 100)}%`, height: "100%", background: colour }} />
        </div>
      )}
    </div>
  );
}

function ProjectDetail({ id, base }: Readonly<{ id: number; base: string }>) {
  const summary = useQuery({ queryKey: ["project-summary", id], queryFn: () => getProjectSummary(id) });
  const txns = useQuery({
    queryKey: ["project-txns", id],
    queryFn: () => listTransactions({ project_id: id, limit: 200 }),
  });
  const s = summary.data;
  if (summary.isLoading || !s) return <p className="muted" style={{ padding: "6px 0 12px 16px" }}>Loading…</p>;
  return (
    <div style={{ padding: "6px 0 14px 16px", background: "rgba(127,127,127,0.05)" }}>
      <p className="muted" style={{ margin: "4px 0" }}>
        {s.transaction_count} transaction(s)
        {s.first_transaction && ` · ${s.first_transaction} → ${s.last_transaction}`}
        {s.budget && ` · budget ${s.budget} ${base} (${s.percent}% used, ${s.remaining} left)`}
      </p>
      <div style={{ display: "flex", gap: 32, flexWrap: "wrap" }}>
        <Breakdown title="By category" rows={s.by_category} base={base} />
        <Breakdown title="By vendor" rows={s.by_vendor} base={base} />
      </div>
      <ProjectTxns data={txns.data} projectId={id} />
    </div>
  );
}

function ProjectTxns({ data, projectId }: Readonly<{ data?: TransactionListResponse; projectId: number }>) {
  if (!data) return <p className="muted" style={{ margin: "8px 0 0" }}>Loading transactions…</p>;
  if (data.items.length === 0) {
    return (
      <p className="muted" style={{ margin: "8px 0 0" }}>
        No transactions assigned yet — assign some on the Transactions page.
      </p>
    );
  }
  return (
    <div style={{ marginTop: 10 }}>
      <h4 style={{ margin: "6px 0", fontSize: "0.85rem" }}>Transactions</h4>
      <ul className="kv" style={{ maxWidth: 520 }}>
        {data.items.map((t) => (
          <li key={t.id}>
            <span>
              <span className="muted">{t.transaction_date}</span> ·{" "}
              <Link to={`/transactions?focus=${t.id}`} title="Open this transaction">
                {t.merchant_raw || t.description_raw}
              </Link>
            </span>
            <span style={{ whiteSpace: "nowrap" }}>{t.amount} {t.currency}</span>
          </li>
        ))}
      </ul>
      <p style={{ fontSize: "0.8rem", margin: "4px 0 0" }}>
        {data.total > data.items.length && (
          <span className="muted">Showing {data.items.length} of {data.total}. </span>
        )}
        <Link className="link-btn" to={`/transactions?project_id=${projectId}`}>Open all in Transactions →</Link>
      </p>
    </div>
  );
}

function Breakdown({ title, rows, base }: Readonly<{ title: string; rows: { id: number | null; name: string; total: string }[]; base: string }>) {
  if (rows.length === 0) return null;
  return (
    <div>
      <h4 style={{ margin: "6px 0", fontSize: "0.85rem" }}>{title}</h4>
      <ul className="kv" style={{ minWidth: 220 }}>
        {rows.map((r) => (
          <li key={`${r.id}-${r.name}`}><span>{r.name}</span><span>{r.total} {base}</span></li>
        ))}
      </ul>
    </div>
  );
}

function NewProject({
  base,
  onCreated,
  onError,
}: Readonly<{
  base: string;
  onCreated: () => void;
  onError: (e: string) => void;
}>) {
  const [name, setName] = useState("");
  const [status, setStatus] = useState("active");
  const [budget, setBudget] = useState("");

  const create = useMutation({
    mutationFn: () =>
      createProject({
        name,
        status,
        budget_amount: budget ? budget : null,
      }),
    onSuccess: () => {
      setName("");
      setBudget("");
      onCreated();
    },
    onError: (e) => onError(String(e)),
  });

  return (
    <div className="card">
      <h2 className="card__title">New project</h2>
      <p className="muted">A project collects spend toward a goal (renovation, holiday, car…). Optionally set a budget; assign transactions on the Transactions page.</p>
      <div className="form-row" style={{ flexWrap: "wrap", gap: 8 }}>
        <input placeholder="Name (e.g. Bathroom renovation)" value={name} onChange={(e) => setName(e.target.value)} style={{ minWidth: 200 }} />
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          {PROJECT_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <input
          type="number" step="0.01" min="0"
          placeholder={`Budget (${base}, optional)`}
          value={budget}
          onChange={(e) => setBudget(e.target.value)}
          style={{ width: 170 }}
        />
        <button className="btn" disabled={!name.trim() || create.isPending} onClick={() => create.mutate()}>
          {create.isPending ? "Adding…" : "Add project"}
        </button>
      </div>
    </div>
  );
}
