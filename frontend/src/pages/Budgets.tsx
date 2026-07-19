import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BUDGET_PERIODS,
  createBudget,
  deleteBudget,
  getBudgetSummary,
  getBudgetTransactions,
  getSettings,
  listBudgets,
  listCategories,
  listProjects,
  updateBudget,
  type Budget,
  type BudgetSummaryItem,
} from "../api/client";
import { useConfirm } from "../components/dialogs";
import ListRow from "../components/ListRow";
import ProgressBar from "../components/ProgressBar";

function thisMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

// Year choices for the annual ("This year") view: next year down to a few past
// years. Computed once at module load.
const YEARS = (() => {
  const y = new Date().getFullYear();
  return [y + 1, y, y - 1, y - 2, y - 3, y - 4];
})();

const STATUS_COLOUR: Record<string, string> = {
  ok: "#3a9b5c",
  warn: "#d8930a",
  over: "#c0392b",
};

// Prorated "pace" signal surfaced by the summary API (additive to over/warn/ok).
// Direction-explicit wording: the API's "ahead" means spending FASTER than the
// prorated budget (i.e. over pace), so "over pace"/"under pace" read correctly
// and never contradict the over/under-budget status.
const PACE_LABEL: Record<string, string> = {
  ahead: "over pace",
  behind: "under pace",
  on_track: "on pace",
};

// The summary response also carries a prorated-pace status; it isn't yet on the
// shared BudgetSummaryItem type, so read it via this local view.
type WithPace = BudgetSummaryItem & {
  pace_status?: "ahead" | "behind" | "on_track";
};

export default function Budgets() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [month, setMonth] = useState(thisMonth());
  const [annual, setAnnual] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const categories = useQuery({ queryKey: ["categories"], queryFn: listCategories });
  const projects = useQuery({ queryKey: ["projects"], queryFn: listProjects });
  const settings = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const summary = useQuery({
    queryKey: ["budget-summary", month, annual],
    queryFn: () => getBudgetSummary(`${month}-01`, annual),
  });
  const base = settings.data?.base_currency ?? "GBP";

  const catName = (id: number | null) =>
    id == null ? null : categories.data?.find((c) => c.id === id)?.name ?? `#${id}`;

  // Create, edit and delete all touch the same summary + drill-down caches.
  const invalidateBudgets = () => {
    setErr(null);
    qc.invalidateQueries({ queryKey: ["budget-summary"] });
    qc.invalidateQueries({ queryKey: ["budget-txns"] });
  };

  const remove = useMutation({
    mutationFn: (id: number) => deleteBudget(id),
    onSuccess: invalidateBudgets,
    onError: (e) => setErr(String(e)),
  });

  return (
    <div className="page">
      <div className="page__head">
        <h1 className="page__title">Budgets</h1>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <div className="form-row" style={{ gap: 4 }} title="Evaluate the budget's own period, or the whole year vs an annualised cap">
            <button className={"btn btn--sm" + (annual ? " btn--ghost" : "")} onClick={() => setAnnual(false)}>This period</button>
            <button className={"btn btn--sm" + (annual ? "" : " btn--ghost")} onClick={() => setAnnual(true)}>This year</button>
          </div>
          <label className="muted">
            {annual ? "Year " : "Month "}
            {annual ? (
              <select value={month.slice(0, 4)} onChange={(e) => setMonth(`${e.target.value}-01`)}>
                {YEARS.map((y) => <option key={y} value={y}>{y}</option>)}
              </select>
            ) : (
              <input type="month" value={month} onChange={(e) => setMonth(e.target.value)} />
            )}
          </label>
        </div>
      </div>

      {err && <p className="status status--error">{err}</p>}

      <NewBudget
        base={base}
        categories={categories.data ?? []}
        onError={setErr}
        onCreated={invalidateBudgets}
      />

      <div className="card">
        <h2 className="card__title">Your budgets</h2>
        {summary.isLoading && <p className="muted">Loading…</p>}
        {summary.data?.length === 0 && (
          <p className="muted">No budgets yet. Add one above to track spending against a limit.</p>
        )}
        <div className="budget-list">
          {summary.data?.map((b) => (
            <BudgetRow
              key={b.budget_id}
              b={b}
              base={base}
              month={`${month}-01`}
              annual={annual}
              categoryName={catName(b.category_id)}
              categories={categories.data ?? []}
              projects={projects.data ?? []}
              onError={setErr}
              onSaved={invalidateBudgets}
              onDelete={async () => {
                if (await confirm({ message: `Delete budget "${b.name}"?`, confirmLabel: "Delete", danger: true })) remove.mutate(b.budget_id);
              }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function BudgetRow({
  b,
  base,
  month,
  annual,
  categoryName,
  categories,
  projects,
  onError,
  onSaved,
  onDelete,
}: Readonly<{
  b: BudgetSummaryItem;
  base: string;
  month: string;
  annual: boolean;
  categoryName: string | null;
  categories: { id: number; name: string }[];
  projects: { id: number; name: string }[];
  onError: (e: string) => void;
  onSaved: () => void;
  onDelete: () => void;
}>) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const colour = STATUS_COLOUR[b.status] ?? "#3a9b5c";
  const noCategoryScope = b.project_id == null ? "All spending" : "Project";
  const scope = b.category_id == null ? noCategoryScope : (categoryName ?? "Category");
  const warnOrOnTrack = b.status === "warn" ? "near limit" : "on track";
  const statusLabel = b.status === "over" ? "over budget" : warnOrOnTrack;
  const pace = b as WithPace;
  const paceLabel = pace.pace_status ? (PACE_LABEL[pace.pace_status] ?? pace.pace_status) : null;
  // Show a single concise pace hint on EVERY budget for a consistent list (some
  // rows having it and others not read as a bug). The direction-explicit label
  // ("over pace") is unambiguous, so it doesn't contradict the over-budget tag.
  const showPace = paceLabel != null;
  const txns = useQuery({
    queryKey: ["budget-txns", b.budget_id, month, annual],
    queryFn: () => getBudgetTransactions(b.budget_id, { month, annual }),
    enabled: open,
  });
  return (
    <ListRow className="budget-row">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <div>
          <button className="link-btn" style={{ fontWeight: 700 }} onClick={() => setOpen((v) => !v)}>
            {open ? "▾ " : "▸ "}{b.name}
          </button>{" "}
          <span className="muted">· {scope} · {annual ? "yearly" : b.period}</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span className="tag" style={{ background: colour, color: "#fff" }}>
            {statusLabel}
          </span>
          <button className="link-btn" onClick={() => setEditing((v) => !v)}>
            {editing ? "close" : "edit"}
          </button>
          <button className="link-btn" onClick={onDelete}>delete</button>
        </div>
      </div>
      <ProgressBar percent={b.percent} color={colour} title={`${b.percent}%`} />
      <div className="muted" style={{ marginTop: 4, fontSize: "0.85rem" }}>
        {b.spent} / {b.amount} {base} spent · {b.remaining} {base} {Number(b.remaining) < 0 ? "over" : "left"} · {b.percent}%
        {annual && <span> · annual cap</span>}
      </div>
      {showPace && (
        <div className="muted" style={{ marginTop: 2, fontSize: "0.8rem" }}>
          {paceLabel}
        </div>
      )}
      {editing && (
        <EditBudget
          budgetId={b.budget_id}
          fallback={b}
          base={base}
          categories={categories}
          projects={projects}
          onError={onError}
          onSaved={() => {
            setEditing(false);
            onSaved();
          }}
        />
      )}
      {open && (
        <div style={{ marginTop: 8, paddingLeft: 12 }}>
          {txns.isError && (
            <p className="status status--error" style={{ margin: 0 }}>
              Couldn’t load transactions: {String(txns.error)}
            </p>
          )}
          {!txns.isError && (txns.isLoading || !txns.data) && (
            <p className="muted" style={{ margin: 0 }}>Loading…</p>
          )}
          {txns.data?.length === 0 && (
            <p className="muted" style={{ margin: 0 }}>No transactions counted toward this budget {annual ? "this year" : "this period"}.</p>
          )}
          {txns.data && txns.data.length > 0 && (
            <ul className="kv" style={{ margin: 0, maxWidth: 560 }}>
              {txns.data.map((t) => (
                <li key={t.id}>
                  <span>
                    <span className="muted">{t.transaction_date}</span> ·{" "}
                    <Link to={`/transactions?focus=${t.id}`} title="Open this transaction">
                      {t.description}
                    </Link>
                  </span>
                  <span style={{ whiteSpace: "nowrap" }}>{t.amount} {base}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </ListRow>
  );
}

function NewBudget({
  base,
  categories,
  onCreated,
  onError,
}: Readonly<{
  base: string;
  categories: { id: number; name: string }[];
  onCreated: () => void;
  onError: (e: string) => void;
}>) {
  const [name, setName] = useState("");
  const [amount, setAmount] = useState("");
  const [period, setPeriod] = useState("monthly");
  const [categoryId, setCategoryId] = useState("");
  const [threshold, setThreshold] = useState("80");

  const create = useMutation({
    mutationFn: () =>
      createBudget({
        name,
        amount,
        period,
        category_id: categoryId ? Number(categoryId) : null,
        alert_threshold_percent: threshold ? Number(threshold) : null,
      }),
    onSuccess: () => {
      setName("");
      setAmount("");
      setCategoryId("");
      onCreated();
    },
    onError: (e) => onError(String(e)),
  });

  const valid = name.trim() && Number(amount) > 0;

  return (
    <div className="card">
      <h2 className="card__title">New budget</h2>
      <p className="muted">
        Cap spending for a category (or leave the category blank for a total budget) over a period.
        Spend is in your base currency ({base}); split transactions count per category.
      </p>
      <div className="form-row" style={{ flexWrap: "wrap", gap: 8 }}>
        <input name="new-budget-name" autoComplete="off" placeholder="Name (e.g. Groceries)" value={name} onChange={(e) => setName(e.target.value)} style={{ minWidth: 160 }} />
        <input
          type="number" step="0.01" min="0"
          placeholder={`Amount (${base})`}
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          style={{ width: 140 }}
        />
        <select value={categoryId} onChange={(e) => setCategoryId(e.target.value)}>
          <option value="">All spending (total)</option>
          {categories.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
        <select value={period} onChange={(e) => setPeriod(e.target.value)}>
          {BUDGET_PERIODS.map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
        <label className="muted">
          Alert at{" "}
          <input
            type="number" min="0" max="100"
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
            style={{ width: 64 }}
          />{" "}
          %
        </label>
        <button className="btn" disabled={!valid || create.isPending} onClick={() => create.mutate()}>
          {create.isPending ? "Adding…" : "Add budget"}
        </button>
      </div>
    </div>
  );
}

// Loads the budget's full record (start/end/rollover aren't on the summary item)
// and, once available, renders the pre-filled edit form. Keyed by budget id so the
// form state is fresh per budget.
function EditBudget({
  budgetId,
  fallback,
  base,
  categories,
  projects,
  onError,
  onSaved,
}: Readonly<{
  budgetId: number;
  fallback: BudgetSummaryItem;
  base: string;
  categories: { id: number; name: string }[];
  projects: { id: number; name: string }[];
  onError: (e: string) => void;
  onSaved: () => void;
}>) {
  const budgets = useQuery({ queryKey: ["budgets"], queryFn: listBudgets });
  const full = budgets.data?.find((x) => x.id === budgetId);
  if (budgets.isError) {
    return (
      <p className="status status--error" style={{ marginTop: 8 }}>
        Couldn’t load budget details: {String(budgets.error)}
      </p>
    );
  }
  if (!full) {
    return <p className="muted" style={{ marginTop: 8 }}>Loading…</p>;
  }
  return (
    <EditBudgetForm
      key={budgetId}
      budget={full}
      fallback={fallback}
      base={base}
      categories={categories}
      projects={projects}
      onError={onError}
      onSaved={onSaved}
    />
  );
}

function EditBudgetForm({
  budget,
  fallback,
  base,
  categories,
  projects,
  onError,
  onSaved,
}: Readonly<{
  budget: Budget;
  fallback: BudgetSummaryItem;
  base: string;
  categories: { id: number; name: string }[];
  projects: { id: number; name: string }[];
  onError: (e: string) => void;
  onSaved: () => void;
}>) {
  const [name, setName] = useState(budget.name);
  const [amount, setAmount] = useState(budget.amount);
  const [period, setPeriod] = useState(budget.period);
  const [categoryId, setCategoryId] = useState(budget.category_id != null ? String(budget.category_id) : "");
  const [projectId, setProjectId] = useState(budget.project_id != null ? String(budget.project_id) : "");
  const [currency, setCurrency] = useState(budget.currency || fallback.currency || base);
  const [startDate, setStartDate] = useState(budget.start_date ?? "");
  const [endDate, setEndDate] = useState(budget.end_date ?? "");
  const [rollover, setRollover] = useState(budget.rollover_enabled);
  const [threshold, setThreshold] = useState(
    budget.alert_threshold_percent != null ? String(budget.alert_threshold_percent) : "",
  );

  const save = useMutation({
    mutationFn: () =>
      updateBudget(budget.id, {
        name,
        amount,
        period,
        category_id: categoryId ? Number(categoryId) : null,
        project_id: projectId ? Number(projectId) : null,
        currency: currency.trim() || base,
        start_date: startDate || null,
        end_date: endDate || null,
        rollover_enabled: rollover,
        alert_threshold_percent: threshold ? Number(threshold) : null,
      }),
    onSuccess: onSaved,
    onError: (e) => onError(String(e)),
  });

  const valid = name.trim() && Number(amount) > 0;

  return (
    <div className="card" style={{ marginTop: 8 }}>
      <h3 className="card__title" style={{ fontSize: "0.95rem" }}>Edit budget</h3>
      <div className="form-row" style={{ flexWrap: "wrap", gap: 8 }}>
        <input aria-label="Name" autoComplete="off" placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} style={{ minWidth: 160 }} />
        <input
          aria-label="Amount"
          type="number" step="0.01" min="0"
          placeholder={`Amount (${base})`}
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          style={{ width: 140 }}
        />
        <input
          aria-label="Currency"
          value={currency}
          onChange={(e) => setCurrency(e.target.value.toUpperCase())}
          maxLength={3}
          style={{ width: 72 }}
        />
        <select aria-label="Category" value={categoryId} onChange={(e) => setCategoryId(e.target.value)}>
          <option value="">All spending (total)</option>
          {categories.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
        <select aria-label="Project" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
          <option value="">No project</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
        <select aria-label="Period" value={period} onChange={(e) => setPeriod(e.target.value)}>
          {BUDGET_PERIODS.map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
      </div>
      <div className="form-row" style={{ flexWrap: "wrap", gap: 8, marginTop: 8 }}>
        <label className="muted">
          Start{" "}
          <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
        </label>
        <label className="muted">
          End{" "}
          <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
        </label>
        <label className="muted">
          Alert at{" "}
          <input
            type="number" min="0" max="100"
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
            style={{ width: 64 }}
          />{" "}
          %
        </label>
        <label className="muted" style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <input type="checkbox" checked={rollover} onChange={(e) => setRollover(e.target.checked)} />
          Roll over unused
        </label>
        <button className="btn" disabled={!valid || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? "Saving…" : "Save changes"}
        </button>
      </div>
    </div>
  );
}
