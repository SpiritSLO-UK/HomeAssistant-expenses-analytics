import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createAllocation,
  createBudget,
  deleteAllocation,
  getAllowanceSummary,
  getMe,
  listCategories,
  listUsers,
  type AllowanceSummary,
  type ChildBudgetStatus,
} from "../api/client";

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function BudgetBars({ budgets, base }: { budgets: ChildBudgetStatus[]; base: string }) {
  if (budgets.length === 0) return <p className="muted">No budgets set yet.</p>;
  const colour = (s: string) => (s === "over" ? "#e05555" : s === "warn" ? "#e0a800" : "#3aa55a");
  return (
    <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 12 }}>
      {budgets.map((b) => (
        <li key={b.budget_id}>
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <strong>{b.name}</strong>
            <span className="muted">{b.spent} / {b.amount} {base}</span>
          </div>
          <div style={{ background: "#2a2a2a", borderRadius: 4, height: 8, marginTop: 4, overflow: "hidden" }}>
            <div style={{ width: `${Math.min(100, b.percent)}%`, height: "100%", background: colour(b.status) }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

function AllowanceView({ data, base }: { data: AllowanceSummary; base: string }) {
  return (
    <>
      <div className="card">
        <h2 className="card__title">Budgets</h2>
        <BudgetBars budgets={data.budgets} base={base} />
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
  if (!q.data) return <p className="status status--error">{String(q.error)}</p>;
  return (
    <>
      <p className="muted">Here's your money — your budgets, savings, and what you've spent.</p>
      <AllowanceView data={q.data} base={q.data.currency} />
    </>
  );
}

/** Parent management: pick a child, review/manage their allowance. */
function ParentManager() {
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
  const invalidate = () => qc.invalidateQueries({ queryKey: ["allowance", selected] });
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

  if (children.length === 0) {
    return (
      <div className="card">
        <p className="muted">
          No child accounts yet. On the <strong>Users</strong> page, set a household member's role to
          <strong> child</strong> — then come back here to set up their allowance.
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
          Assigning a purchase to a child shows it here without changing your own expenses. Use the
          <strong> "Assign to child"</strong> link on the Transactions page to draw from a real purchase
          (whole or part), or add a manual item below.
        </p>
      </div>

      {err && <p className="status status--error">{err}</p>}

      {summary.data && <AllowanceView data={summary.data} base={base} />}

      <div className="card">
        <h2 className="card__title">Add a budget</h2>
        <form
          className="form-row"
          style={{ flexWrap: "wrap", gap: 8 }}
          onSubmit={(e) => { e.preventDefault(); if (bName && bAmount) addBudget.mutate(); }}
        >
          <input placeholder="Name (e.g. Candy)" value={bName} onChange={(e) => setBName(e.target.value)} />
          <input placeholder={`Amount/month (${base})`} value={bAmount} style={{ width: 140 }} onChange={(e) => setBAmount(e.target.value)} />
          <select value={bCat} onChange={(e) => setBCat(e.target.value)}>
            <option value="">All categories</option>
            {categories.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <button className="btn" type="submit" disabled={!bName || !bAmount || addBudget.isPending}>
            {addBudget.isPending ? "Adding…" : "Add budget"}
          </button>
        </form>
      </div>

      <div className="card">
        <h2 className="card__title">Add a manual item</h2>
        <form
          className="form-row"
          style={{ flexWrap: "wrap", gap: 8 }}
          onSubmit={(e) => { e.preventDefault(); if (amount) addItem.mutate(); }}
        >
          <input placeholder="Description (e.g. Pocket money)" value={description} onChange={(e) => setDescription(e.target.value)} />
          <input placeholder={`Amount (${base})`} value={amount} style={{ width: 120 }} onChange={(e) => setAmount(e.target.value)} />
          <select value={catId} onChange={(e) => setCatId(e.target.value)}>
            <option value="">No category</option>
            {categories.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <button className="btn" type="submit" disabled={!amount || addItem.isPending}>
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
                  <button className="link-btn" onClick={() => removeItem.mutate(it.id)}>remove</button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}

export default function Allowance() {
  const me = useQuery({ queryKey: ["me"], queryFn: getMe });
  const isChild = me.data?.role === "child";
  return (
    <div className="page">
      <div className="page__head">
        <h1 className="page__title">{isChild ? "My money" : "Allowance"}</h1>
      </div>
      {me.isLoading ? <p className="muted">Loading…</p> : isChild ? <ChildHome /> : <ParentManager />}
    </div>
  );
}
