import { Fragment, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  PROJECT_STATUSES,
  createProject,
  deleteProject,
  getDashboardProjects,
  getProjectSummary,
  getProjectsHistory,
  getSettings,
  listProjects,
  listTransactions,
  updateProject,
  type Project,
  type ProjectTotal,
  type TransactionListResponse,
} from "../api/client";
import { useConfirm } from "../components/dialogs";
import OverTimeChart from "../components/OverTimeChart";
import ListRow from "../components/ListRow";
import ProgressBar from "../components/ProgressBar";

export default function Projects() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [err, setErr] = useState<string | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [editId, setEditId] = useState<number | null>(null);
  const [months, setMonths] = useState(12);

  const projects = useQuery({ queryKey: ["dashboard-projects"], queryFn: () => getDashboardProjects() });
  const history = useQuery({ queryKey: ["projects-history", months], queryFn: () => getProjectsHistory(months) });
  const settings = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const base = settings.data?.base_currency ?? "GBP";

  const invalidateProjects = () => {
    qc.invalidateQueries({ queryKey: ["dashboard-projects"] });
    qc.invalidateQueries({ queryKey: ["projects-history"] });
    qc.invalidateQueries({ queryKey: ["project-summary"] });
    qc.invalidateQueries({ queryKey: ["project-txns"] });
    qc.invalidateQueries({ queryKey: ["projects-full"] });
  };

  const remove = useMutation({
    mutationFn: (id: number) => deleteProject(id),
    onSuccess: () => {
      setErr(null);
      invalidateProjects();
    },
    onError: (e) => setErr(String(e)),
  });

  return (
    <div className="page">
      <h1 className="page__title">Projects</h1>
      {err && <p className="status status--error">{err}</p>}

      <NewProject
        base={base}
        onError={setErr}
        onCreated={invalidateProjects}
      />

      <OverTimeChart
        title="Project spend over time"
        series={history.data}
        months={months}
        onMonths={setMonths}
        color="#a371f7"
        emptyHint="No project spend yet — assign transactions to a project to see it build up here."
      />

      <div className="card">
        <h2 className="card__title">Your projects</h2>
        {projects.isLoading && <p className="muted">Loading…</p>}
        {projects.data?.length === 0 && (
          <p className="muted">No projects yet. Create one above (e.g. “Bathroom renovation”), then assign transactions to it on the Transactions page.</p>
        )}
        <div className="project-list">
          {projects.data?.map((p) => (
            <Fragment key={p.project_id}>
              <ProjectRow
                p={p}
                base={base}
                open={openId === p.project_id}
                editing={editId === p.project_id}
                onToggle={() => setOpenId(openId === p.project_id ? null : p.project_id)}
                onEdit={() => setEditId(editId === p.project_id ? null : p.project_id)}
                onDelete={async () => {
                  if (await confirm({ message: `Delete project "${p.name}"? (transactions are kept, just unlinked)`, confirmLabel: "Delete", danger: true })) {
                    remove.mutate(p.project_id);
                  }
                }}
              />
              {editId === p.project_id && (
                <EditProject
                  projectId={p.project_id}
                  base={base}
                  onError={setErr}
                  onSaved={() => {
                    setErr(null);
                    setEditId(null);
                    invalidateProjects();
                  }}
                  onCancel={() => setEditId(null)}
                />
              )}
              {openId === p.project_id && <ProjectDetail id={p.project_id} base={base} onError={setErr} onLoaded={() => setErr(null)} />}
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
  editing,
  onToggle,
  onEdit,
  onDelete,
}: Readonly<{
  p: ProjectTotal;
  base: string;
  open: boolean;
  editing: boolean;
  onToggle: () => void;
  onEdit: () => void;
  onDelete: () => void;
}>) {
  const pct = p.percent ?? null;
  const over = pct != null && pct > 100;
  const warnColour = pct != null && pct >= 80 ? "#d8930a" : "#3a9b5c";
  const colour = over ? "#c0392b" : warnColour;
  return (
    <ListRow>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <div>
          <button className="link-btn" onClick={onToggle} style={{ fontWeight: 600 }}>
            {open ? "▾ " : "▸ "}{p.name}
          </button>{" "}
          <span className="tag">{p.status}</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span className="muted">{p.spent} {base} spent{p.budget ? ` / ${p.budget}` : ""}</span>
          <button className="link-btn" onClick={onEdit} aria-expanded={editing}>{editing ? "close" : "edit"}</button>
          <button className="link-btn" onClick={onDelete}>delete</button>
        </div>
      </div>
      {p.budget && pct != null && (
        <ProgressBar percent={pct} color={colour} title={`${pct}%`} />
      )}
    </ListRow>
  );
}

// Local widening view over the forecast the API now surfaces on the summary
// (money fields arrive as strings). The shared ProjectSummary type in client.ts
// doesn't declare it yet, so read it through this intersection rather than there.
type ProjectForecastView = {
  budget: string;
  remaining: string;
  run_rate_per_day: string | null;
  forecast_total: string | null;
  on_track: boolean;
  exhaustion_date: string | null;
};

function Forecast({ forecast, base }: Readonly<{ forecast: ProjectForecastView | null; base: string }>) {
  if (!forecast) return null;
  return (
    <p className="muted" style={{ margin: "4px 0", fontSize: "0.85rem" }}>
      Forecast: <span style={{ color: forecast.on_track ? "#3a9b5c" : "#c0392b" }}>{forecast.on_track ? "on track" : "over budget"}</span>
      {forecast.run_rate_per_day && ` · ${forecast.run_rate_per_day} ${base}/day`}
      {forecast.forecast_total && ` · projected ${forecast.forecast_total} ${base}`}
      {forecast.exhaustion_date && ` · budget spent by ${forecast.exhaustion_date}`}
    </p>
  );
}

function ProjectDetail({ id, base, onError, onLoaded }: Readonly<{ id: number; base: string; onError: (e: string) => void; onLoaded: () => void }>) {
  const summary = useQuery({ queryKey: ["project-summary", id], queryFn: () => getProjectSummary(id) });
  const txns = useQuery({
    queryKey: ["project-txns", id],
    queryFn: () => listTransactions({ project_id: id, limit: 200 }),
  });
  const s = summary.data;
  useEffect(() => {
    if (summary.error) onError(String(summary.error));
    else if (s) onLoaded();
  }, [summary.error, s, onError, onLoaded]);
  if (summary.isError) return <p className="status status--error" style={{ padding: "6px 0 12px 16px" }}>Couldn’t load project details. {String(summary.error)}</p>;
  if (summary.isLoading || !s) return <p className="muted" style={{ padding: "6px 0 12px 16px" }}>Loading…</p>;
  const forecast = (s as typeof s & { forecast?: ProjectForecastView | null }).forecast ?? null;
  return (
    <div style={{ padding: "6px 0 14px 16px", background: "rgba(127,127,127,0.05)" }}>
      <p className="muted" style={{ margin: "4px 0" }}>
        {s.transaction_count} transaction(s)
        {s.first_transaction && ` · ${s.first_transaction} → ${s.last_transaction}`}
        {s.budget && ` · budget ${s.budget} ${base} (${s.percent}% used, ${s.remaining} left)`}
      </p>
      <Forecast forecast={forecast} base={base} />
      <div style={{ display: "flex", gap: 32, flexWrap: "wrap" }}>
        <Breakdown title="By category" rows={s.by_category} base={base} />
        <Breakdown title="By vendor" rows={s.by_vendor} base={base} />
      </div>
      <ProjectTxns data={txns.data} projectId={id} isError={txns.isError} />
    </div>
  );
}

function ProjectTxns({ data, projectId, isError }: Readonly<{ data?: TransactionListResponse; projectId: number; isError?: boolean }>) {
  if (isError) return <p className="status status--error" style={{ margin: "8px 0 0" }}>Couldn’t load transactions.</p>;
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
        budget_amount: budget || null,
      }),
    onSuccess: () => {
      setName("");
      setBudget("");
      onCreated();
    },
    onError: (e) => onError(String(e)),
  });

  const submit = () => {
    const trimmed = budget.trim();
    if (trimmed) {
      const value = Number(trimmed);
      if (!Number.isFinite(value) || value < 0) {
        onError("Budget must be a number of 0 or more, or left empty.");
        return;
      }
    }
    onError("");
    create.mutate();
  };

  return (
    <div className="card">
      <h2 className="card__title">New project</h2>
      <p className="muted">A project collects spend toward a goal (renovation, holiday, car…). Optionally set a budget; assign transactions on the Transactions page.</p>
      <div className="form-row" style={{ flexWrap: "wrap", gap: 8 }}>
        <input name="new-project-name" autoComplete="off" placeholder="Name (e.g. Bathroom renovation)" value={name} onChange={(e) => setName(e.target.value)} style={{ minWidth: 200 }} />
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
        <button className="btn" disabled={!name.trim() || create.isPending} onClick={submit}>
          {create.isPending ? "Adding…" : "Add project"}
        </button>
      </div>
    </div>
  );
}

// Inline edit form for an existing project. The dashboard row only carries the
// computed totals (ProjectTotal), so we pull the full record — description,
// budget_amount, start/end dates — from the projects list to pre-fill every
// editable field before saving via PATCH.
function EditProject({
  projectId,
  base,
  onSaved,
  onCancel,
  onError,
}: Readonly<{
  projectId: number;
  base: string;
  onSaved: () => void;
  onCancel: () => void;
  onError: (e: string) => void;
}>) {
  const all = useQuery({ queryKey: ["projects-full"], queryFn: listProjects });
  if (all.isLoading) {
    return <p className="muted" style={{ padding: "6px 0 12px 16px" }}>Loading…</p>;
  }
  const project = all.data?.find((p) => p.id === projectId);
  if (!project) {
    return (
      <p className="status status--error" style={{ padding: "6px 0 12px 16px" }}>
        Couldn’t load this project to edit.
      </p>
    );
  }
  return (
    <EditProjectForm
      project={project}
      base={base}
      onSaved={onSaved}
      onCancel={onCancel}
      onError={onError}
    />
  );
}

function EditProjectForm({
  project,
  base,
  onSaved,
  onCancel,
  onError,
}: Readonly<{
  project: Project;
  base: string;
  onSaved: () => void;
  onCancel: () => void;
  onError: (e: string) => void;
}>) {
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description ?? "");
  const [status, setStatus] = useState(project.status);
  const [budget, setBudget] = useState(project.budget_amount ?? "");
  const [startDate, setStartDate] = useState(project.start_date ?? "");
  const [endDate, setEndDate] = useState(project.end_date ?? "");

  const save = useMutation({
    mutationFn: () =>
      updateProject(project.id, {
        name: name.trim(),
        description: description.trim() || null,
        status,
        budget_amount: budget.trim() || null,
        start_date: startDate || null,
        end_date: endDate || null,
      }),
    onSuccess: () => onSaved(),
    onError: (e) => onError(String(e)),
  });

  const submit = () => {
    const trimmed = budget.trim();
    if (trimmed) {
      const value = Number(trimmed);
      if (!Number.isFinite(value) || value < 0) {
        onError("Budget must be a number of 0 or more, or left empty.");
        return;
      }
    }
    onError("");
    save.mutate();
  };

  return (
    <div style={{ padding: "6px 0 14px 16px", background: "rgba(127,127,127,0.05)" }}>
      <h4 style={{ margin: "6px 0", fontSize: "0.9rem" }}>Edit project</h4>
      <div className="form-row" style={{ flexWrap: "wrap", gap: 8 }}>
        <input
          aria-label="Project name"
          autoComplete="off"
          placeholder="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          style={{ minWidth: 200 }}
        />
        <select aria-label="Project status" value={status} onChange={(e) => setStatus(e.target.value)}>
          {PROJECT_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <input
          type="number" step="0.01" min="0"
          aria-label="Project budget"
          placeholder={`Budget (${base}, optional)`}
          value={budget}
          onChange={(e) => setBudget(e.target.value)}
          style={{ width: 170 }}
        />
      </div>
      <div className="form-row" style={{ flexWrap: "wrap", gap: 8, marginTop: 8 }}>
        <input
          aria-label="Project description"
          autoComplete="off"
          placeholder="Description (optional)"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          style={{ minWidth: 260, flex: 1 }}
        />
        <label className="muted" style={{ display: "flex", flexDirection: "column", fontSize: "0.75rem", gap: 2 }}>
          Start date
          <input type="date" aria-label="Project start date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
        </label>
        <label className="muted" style={{ display: "flex", flexDirection: "column", fontSize: "0.75rem", gap: 2 }}>
          End date
          <input type="date" aria-label="Project end date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
        </label>
      </div>
      <div className="form-row" style={{ gap: 8, marginTop: 10 }}>
        <button className="btn" disabled={!name.trim() || save.isPending} onClick={submit}>
          {save.isPending ? "Saving…" : "Save changes"}
        </button>
        <button className="btn btn--ghost" onClick={onCancel} disabled={save.isPending}>Cancel</button>
      </div>
    </div>
  );
}
