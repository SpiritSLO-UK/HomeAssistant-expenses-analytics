import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createAllocation,
  createBudget,
  deleteAllocation,
  deleteBudget,
  getAllowanceSummary,
  getMe,
  listCategories,
  listUsers,
  updateBudget,
  type AllowanceSummary,
  type ChildBudgetStatus,
} from "../api/client";
import { isAmount } from "../lib/num";
import { useServerState } from "../lib/useServerState";

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

// When provided (parent/admin view), each budget gets an editable amount + remove.
interface BudgetManage {
  onSave: (id: number, amount: string) => void;
  onDelete: (id: number) => void;
  busy: boolean;
}

const budgetColour = (s: string) => {
  if (s === "over") return "#e05555";
  if (s === "warn") return "#e0a800";
  return "#3aa55a";
};

function BudgetBar({ b, base, manage }: Readonly<{ b: ChildBudgetStatus; base: string; manage?: BudgetManage }>) {
  // Re-sync from the server value so the Save disabled-compare stays accurate after a
  // refetch, rather than comparing against a baseline captured only at mount (FE-7).
  const [amount, setAmount] = useServerState(b.amount);
  return (
    <li>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <strong>{b.name}</strong>
        {manage ? (
          <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
            <input
              inputMode="decimal"
              value={amount}
              style={{ width: 90 }}
              aria-label={`${b.name} budget amount`}
              onChange={(e) => setAmount(e.target.value)}
            />
            <span className="muted">{base}/mo</span>
            <button
              className="btn btn--sm btn--ghost"
              disabled={manage.busy || amount === b.amount || !isAmount(amount)}
              onClick={() => { if (isAmount(amount)) manage.onSave(b.budget_id, amount); }}
            >
              Save
            </button>
            <button
              className="link-btn"
              title="Remove this budget"
              onClick={() => { if (globalThis.confirm(`Remove the "${b.name}" budget?`)) manage.onDelete(b.budget_id); }}
            >
              ✕
            </button>
          </span>
        ) : (
          <span className="muted">{b.spent} / {b.amount} {base}</span>
        )}
      </div>
      <div style={{ background: "#2a2a2a", borderRadius: 4, height: 8, marginTop: 4, overflow: "hidden" }}>
        <div style={{ width: `${Math.min(100, b.percent)}%`, height: "100%", background: budgetColour(b.status) }} />
      </div>
      {manage && <div className="muted" style={{ fontSize: "0.78rem", marginTop: 2 }}>Spent {b.spent} of {b.amount} {base}</div>}
    </li>
  );
}

function BudgetBars({ budgets, base, manage }: Readonly<{ budgets: ChildBudgetStatus[]; base: string; manage?: BudgetManage }>) {
  if (budgets.length === 0) return <p className="muted">No budgets set yet.</p>;
  return (
    <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 12 }}>
      {budgets.map((b) => (
        <BudgetBar key={b.budget_id} b={b} base={base} manage={manage} />
      ))}
    </ul>
  );
}

function AllowanceView({ data, base, manage }: Readonly<{ data: AllowanceSummary; base: string; manage?: BudgetManage }>) {
  return (
    <>
      <div className="card">
        <h2 className="card__title">Budgets</h2>
        <BudgetBars budgets={data.budgets} base={base} manage={manage} />
      </div>

      <div className="card">
        <h2 className="card__title">Savings</h2>
        <p style={{ fontSize: "1.4rem", margin: 0 }}>
          <strong>{data.savings.total_savings} {base}</strong>{" "}
          <span className="muted">across {data.savings.accounts.length} pot(s)</span>
        </p>
      </div>

      <div className="card">
        <h2 className="card__title">My purchases</h2>
        {data.items.length === 0 && <p className="muted">Nothing yet.</p>}
        {data.items.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th>When</th><th>Item</th><th>Category</th><th className="num">Amount</th></tr>
              </thead>
              <tbody>
                {data.items.map((it) => (
                  <tr key={it.id}>
                    <td style={{ whiteSpace: "nowrap" }}>{it.as_of_date}</td>
                    <td>{it.description ?? "—"}</td>
                    <td className="muted">{it.category_name ?? "—"}</td>
                    <td className="num">{it.amount} {it.currency}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}

/** Child's own read-only allowance. */
function ChildHome() {
  const q = useQuery({ queryKey: ["allowance", "me"], queryFn: () => getAllowanceSummary() });
  if (q.isLoading) return <p className="muted">Loading…</p>;
  if (!q.data) return <p className="status status--error">{q.error instanceof Error ? q.error.message : "Couldn't load your money."}</p>;
  return (
    <>
      <p className="muted">Here's your money — your budgets, savings, and what you've spent.</p>
      <AllowanceView data={q.data} base={q.data.currency} />
    </>
  );
}

/** Parent management: pick a child, review/manage their allowance.
 *  `canManage` (owner/admin) gates the budget + manual-item editing — non-admin
 *  adults see the allowance read-only. The backend enforces the same. */
function ParentManager({ canManage }: Readonly<{ canManage: boolean }>) {
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: listUsers });
  const categories = useQuery({ queryKey: ["categories"], queryFn: listCategories });
  const [childId, setChildId] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const children = (users.data ?? []).filter((u) => u.role === "child");
  const selected = childId ?? children[0]?.id ?? null;

  const summary = useQuery({
    queryKey: ["allowance", selected],
    queryFn: () => getAllowanceSummary(selected ?? undefined),
    enabled: selected != null,
  });
  const base = summary.data?.currency ?? "GBP";
  // Every mutation's onSuccess calls this, so clearing the error here drops a stale
  // banner after a later success (FE-3).
  const invalidate = () => {
    setErr(null);
    qc.invalidateQueries({ queryKey: ["allowance", selected] });
    // A budget/manual-item change also moves the household budget + savings figures,
    // so refresh those views too (they key off separate queries).
    qc.invalidateQueries({ queryKey: ["budget-summary"] });
    qc.invalidateQueries({ queryKey: ["savings-summary"] });
  };
  const fail = (e: unknown) => setErr(String(e instanceof Error ? e.message : e));

  // Manual item
  const [amount, setAmount] = useState("");
  const [description, setDescription] = useState("");
  const [catId, setCatId] = useState("");
  const addItem = useMutation({
    mutationFn: () =>
      createAllocation({
        child_id: selected as number,
        amount,
        description: description || undefined,
        category_id: catId ? Number(catId) : null,
        as_of: today(),
      }),
    onSuccess: () => { setAmount(""); setDescription(""); invalidate(); },
    onError: fail,
  });

  // Child budget
  const [bName, setBName] = useState("");
  const [bAmount, setBAmount] = useState("");
  const [bCat, setBCat] = useState("");
  const addBudget = useMutation({
    mutationFn: () =>
      createBudget({
        name: bName,
        amount: bAmount,
        period: "monthly",
        category_id: bCat ? Number(bCat) : null,
        owner_user_id: selected,
      }),
    onSuccess: () => { setBName(""); setBAmount(""); setBCat(""); invalidate(); },
    onError: fail,
  });

  const removeItem = useMutation({
    mutationFn: (id: number) => deleteAllocation(id),
    onSuccess: invalidate,
    onError: fail,
  });

  // Edit / remove an existing budget (owner/admin only — see canManage).
  const editBudget = useMutation({
    mutationFn: (v: { id: number; amount: string }) => updateBudget(v.id, { amount: v.amount }),
    onSuccess: invalidate,
    onError: fail,
  });
  const removeBudget = useMutation({
    mutationFn: (id: number) => deleteBudget(id),
    onSuccess: invalidate,
    onError: fail,
  });
  const manage: BudgetManage | undefined = canManage
    ? {
        onSave: (id, amount) => editBudget.mutate({ id, amount }),
        onDelete: (id) => removeBudget.mutate(id),
        busy: editBudget.isPending || removeBudget.isPending,
      }
    : undefined;

  if (children.length === 0) {
    return (
      <div className="card">
        <p className="muted">
          No child accounts yet. On the <strong>Users</strong> page, set a household member's role to{" "}
          <strong>child</strong> — then come back here to set up their allowance.
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="card">
        <label>
          Child{" "}
          <select value={selected ?? ""} onChange={(e) => setChildId(Number(e.target.value))}>
            {children.map((c) => (
              <option key={c.id} value={c.id}>{c.display_name}</option>
            ))}
          </select>
        </label>
        <p className="muted" style={{ fontSize: "0.82rem", marginBottom: 0 }}>
          Assigning a purchase to a child shows it here without changing your own expenses. Use the{" "}
          <strong>"Assign to child"</strong> link on the Transactions page to draw from a real purchase
          (whole or part), or add a manual item below.
        </p>
      </div>

      {err && <p className="status status--error">{err}</p>}

      {!canManage && (
        <p className="muted">Allowance budgets and pocket money are managed by an owner/admin. You're seeing it read-only.</p>
      )}

      {summary.data && <AllowanceView data={summary.data} base={base} manage={manage} />}

      {canManage && (
      <>
      <div className="card">
        <h2 className="card__title">Add a budget</h2>
        <form
          className="form-row"
          style={{ flexWrap: "wrap", gap: 8 }}
          onSubmit={(e) => { e.preventDefault(); if (bName && isAmount(bAmount)) addBudget.mutate(); }}
        >
          <input placeholder="Name (e.g. Candy)" value={bName} onChange={(e) => setBName(e.target.value)} />
          <input inputMode="decimal" placeholder={`Amount/month (${base})`} value={bAmount} style={{ width: 140 }} onChange={(e) => setBAmount(e.target.value)} />
          <select value={bCat} onChange={(e) => setBCat(e.target.value)}>
            <option value="">All categories</option>
            {categories.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <button className="btn" type="submit" disabled={!bName || !isAmount(bAmount) || addBudget.isPending}>
            {addBudget.isPending ? "Adding…" : "Add budget"}
          </button>
        </form>
      </div>

      <div className="card">
        <h2 className="card__title">Add a manual item</h2>
        <form
          className="form-row"
          style={{ flexWrap: "wrap", gap: 8 }}
          onSubmit={(e) => { e.preventDefault(); if (isAmount(amount)) addItem.mutate(); }}
        >
          <input placeholder="Description (e.g. Pocket money)" value={description} onChange={(e) => setDescription(e.target.value)} />
          <input inputMode="decimal" placeholder={`Amount (${base})`} value={amount} style={{ width: 120 }} onChange={(e) => setAmount(e.target.value)} />
          <select value={catId} onChange={(e) => setCatId(e.target.value)}>
            <option value="">No category</option>
            {categories.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <button className="btn" type="submit" disabled={!isAmount(amount) || addItem.isPending}>
            {addItem.isPending ? "Adding…" : "Add item"}
          </button>
        </form>
        {summary.data && summary.data.items.length > 0 && (
          <ul className="kv" style={{ marginTop: 10 }}>
            {summary.data.items.map((it) => (
              <li key={it.id}>
                <span>{it.as_of_date} · {it.description ?? "—"} <span className="muted">({it.category_name ?? "—"})</span></span>
                <span>
                  {it.amount} {it.currency}{" "}
                  <button
                    className="link-btn"
                    onClick={() => { if (globalThis.confirm(`Remove "${it.description ?? "this item"}"?`)) removeItem.mutate(it.id); }}
                  >
                    remove
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
      </>
      )}
    </>
  );
}

export default function Allowance() {
  const me = useQuery({ queryKey: ["me"], queryFn: getMe });
  const isChild = me.data?.role === "child";
  const childOrParent = isChild ? <ChildHome /> : <ParentManager canManage={!!me.data?.is_admin} />;
  return (
    <div className="page">
      <div className="page__head">
        <h1 className="page__title">{isChild ? "My money" : "Allowance"}</h1>
      </div>
      {me.isLoading ? <p className="muted">Loading…</p> : childOrParent}
    </div>
  );
}
