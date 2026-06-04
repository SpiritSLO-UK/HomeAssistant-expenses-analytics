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
  listCategories,
  type BudgetSummaryItem,
} from "../api/client";

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

export default function Budgets() {
  const qc = useQueryClient();
  const [month, setMonth] = useState(thisMonth());
  const [annual, setAnnual] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const categories = useQuery({ queryKey: ["categories"], queryFn: listCategories });
  const settings = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const summary = useQuery({
    queryKey: ["budget-summary", month, annual],
    queryFn: () => getBudgetSummary(`${month}-01`, annual),
  });
  const base = settings.data?.base_currency ?? "GBP";

  const catName = (id: number | null) =>
    id == null ? null : categories.data?.find((c) => c.id === id)?.name ?? `#${id}`;

  const remove = useMutation({
    mutationFn: (id: number) => deleteBudget(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["budget-summary"] }),
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
        onCreated={() => qc.invalidateQueries({ queryKey: ["budget-summary"] })}
      />

      <div className="card">
        <h2 className="card__title">Your budgets</h2>
        {summary.isLoading && <p className="muted">Loading…</p>}
        {summary.data && summary.data.length === 0 && (
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
              onDelete={() => {
                if (confirm(`Delete budget "${b.name}"?`)) remove.mutate(b.budget_id);
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
  onDelete,
}: Readonly<{
  b: BudgetSummaryItem;
  base: string;
  month: string;
  annual: boolean;
  categoryName: string | null;
  onDelete: () => void;
}>) {
  const [open, setOpen] = useState(false);
  const colour = STATUS_COLOUR[b.status] ?? "#3a9b5c";
  const scope =
    b.category_id != null ? (categoryName ?? "Category")
    : b.project_id != null ? "Project"
    : "All spending";
  const txns = useQuery({
    queryKey: ["budget-txns", b.budget_id, month, annual],
    queryFn: () => getBudgetTransactions(b.budget_id, { month, annual }),
    enabled: open,
  });
  return (
    <div className="budget-row" style={{ padding: "10px 0", borderBottom: "1px solid var(--border, #2222)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <div>
          <button className="link-btn" style={{ fontWeight: 700 }} onClick={() => setOpen((v) => !v)}>
            {open ? "▾ " : "▸ "}{b.name}
          </button>{" "}
          <span className="muted">· {scope} · {annual ? "yearly" : b.period}</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span className="tag" style={{ background: colour, color: "#fff" }}>
            {b.status === "over" ? "over budget" : b.status === "warn" ? "near limit" : "on track"}
          </span>
          <button className="link-btn" onClick={onDelete}>delete</button>
        </div>
      </div>
      <div
        style={{
          marginTop: 6, height: 10, borderRadius: 5, background: "rgba(127,127,127,0.22)",
          overflow: "hidden",
        }}
        title={`${b.percent}%`}
      >
        <div style={{ width: `${Math.min(b.percent, 100)}%`, height: "100%", background: colour }} />
      </div>
      <div className="muted" style={{ marginTop: 4, fontSize: "0.85rem" }}>
        {b.spent} / {b.amount} {base} spent · {b.remaining} {base} {Number(b.remaining) < 0 ? "over" : "left"} · {b.percent}%
        {annual && <span> · annual cap</span>}
      </div>
      {open && (
        <div style={{ marginTop: 8, paddingLeft: 12 }}>
          {txns.isLoading || !txns.data ? (
            <p className="muted" style={{ margin: 0 }}>Loading…</p>
          ) : txns.data.length === 0 ? (
            <p className="muted" style={{ margin: 0 }}>No transactions counted toward this budget {annual ? "this year" : "this period"}.</p>
          ) : (
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
    </div>
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
        <input placeholder="Name (e.g. Groceries)" value={name} onChange={(e) => setName(e.target.value)} style={{ minWidth: 160 }} />
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
          />
          %
        </label>
        <button className="btn" disabled={!valid || create.isPending} onClick={() => create.mutate()}>
          {create.isPending ? "Adding…" : "Add budget"}
        </button>
      </div>
    </div>
  );
}
