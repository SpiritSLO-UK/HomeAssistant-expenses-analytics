import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Sparkline from "../components/Sparkline";
import {
  createSavingsAccount,
  createSavingsGoal,
  deleteSavingsGoal,
  getBalanceHistory,
  getSavingsSummary,
  recordBalance,
  updateSavingsGoal,
  type SavingsAccount,
  type SavingsGoal,
} from "../api/client";

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function Savings() {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const summary = useQuery({ queryKey: ["savings-summary"], queryFn: getSavingsSummary });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["savings-summary"] });
    qc.invalidateQueries({ queryKey: ["savings-history"] });
  };
  const fail = (e: unknown) => setErr(String(e));

  const base = summary.data?.currency ?? "GBP";

  return (
    <div className="page">
      <div className="page__head">
        <h1 className="page__title">Savings</h1>
      </div>
      <p className="muted">
        Record what each savings pot holds over time, and track goals. Balances are entered
        manually (point them at the statement they came from) — nothing is auto-pulled.
      </p>
      {err && <p className="status status--error">{err}</p>}

      {summary.data && (
        <div className="card">
          <h2 className="card__title">Total saved</h2>
          <p style={{ fontSize: "1.6rem", margin: 0 }}>
            <strong>{summary.data.total_savings} {base}</strong>{" "}
            <span className="muted">across {summary.data.accounts.length} account(s)</span>
          </p>
        </div>
      )}

      <div className="card">
        <h2 className="card__title">Savings accounts</h2>
        {summary.data && summary.data.accounts.length === 0 && (
          <p className="muted">No savings accounts yet — add one below.</p>
        )}
        {summary.data?.accounts.map((a) => (
          <AccountCard key={a.id} account={a} base={base} onChange={invalidate} onError={fail} />
        ))}
        <NewAccountForm onCreated={invalidate} onError={fail} />
      </div>

      <GoalsCard
        goals={summary.data?.goals ?? []}
        accounts={summary.data?.accounts ?? []}
        base={base}
        onChange={invalidate}
        onError={fail}
      />
    </div>
  );
}

function AccountCard({
  account,
  base,
  onChange,
  onError,
}: {
  account: SavingsAccount;
  base: string;
  onChange: () => void;
  onError: (e: unknown) => void;
}) {
  const history = useQuery({
    queryKey: ["savings-history", account.id],
    queryFn: () => getBalanceHistory(account.id),
  });
  const [date, setDate] = useState(today());
  const [amount, setAmount] = useState("");
  const add = useMutation({
    mutationFn: () => recordBalance(account.id, { as_of_date: date, balance: amount }),
    onSuccess: () => {
      setAmount("");
      onChange();
      history.refetch();
    },
    onError,
  });
  const points = (history.data ?? []).map((b) => Number(b.balance));

  return (
    <div style={{ borderTop: "1px solid #2a2a2a", padding: "10px 0" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
        <div>
          <strong>{account.name}</strong>{" "}
          {account.institution && <span className="muted">· {account.institution}</span>}
          <div style={{ fontSize: "1.2rem" }}>
            {account.latest_balance ? `${account.latest_balance} ${account.currency}` : <span className="muted">no balance yet</span>}
          </div>
        </div>
        {points.length >= 2 && <Sparkline values={points} color="#3aa55a" />}
      </div>
      <form
        className="form-row"
        style={{ marginTop: 6 }}
        onSubmit={(e) => { e.preventDefault(); if (amount) add.mutate(); }}
      >
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        <input
          placeholder={`Balance (${base})`}
          value={amount}
          style={{ width: 140 }}
          onChange={(e) => setAmount(e.target.value)}
        />
        <button className="btn btn--sm" type="submit" disabled={!amount || add.isPending}>
          {add.isPending ? "Saving…" : "Record balance"}
        </button>
      </form>
    </div>
  );
}

function NewAccountForm({ onCreated, onError }: { onCreated: () => void; onError: (e: unknown) => void }) {
  const [name, setName] = useState("");
  const [institution, setInstitution] = useState("");
  const create = useMutation({
    mutationFn: () => createSavingsAccount({ name, institution: institution || undefined }),
    onSuccess: () => { setName(""); setInstitution(""); onCreated(); },
    onError,
  });
  return (
    <form
      className="form-row"
      style={{ marginTop: 12 }}
      onSubmit={(e) => { e.preventDefault(); if (name) create.mutate(); }}
    >
      <input placeholder="New account name (e.g. Cash ISA)" value={name} onChange={(e) => setName(e.target.value)} />
      <input placeholder="Institution (optional)" value={institution} onChange={(e) => setInstitution(e.target.value)} />
      <button className="btn" type="submit" disabled={!name || create.isPending}>
        {create.isPending ? "Adding…" : "Add account"}
      </button>
    </form>
  );
}

function GoalsCard({
  goals,
  accounts,
  base,
  onChange,
  onError,
}: {
  goals: SavingsGoal[];
  accounts: SavingsAccount[];
  base: string;
  onChange: () => void;
  onError: (e: unknown) => void;
}) {
  const [name, setName] = useState("");
  const [target, setTarget] = useState("");
  const [targetDate, setTargetDate] = useState("");
  const [accountId, setAccountId] = useState("");

  const create = useMutation({
    mutationFn: () =>
      createSavingsGoal({
        name,
        target_amount: target,
        target_date: targetDate || null,
        account_id: accountId ? Number(accountId) : null,
      }),
    onSuccess: () => { setName(""); setTarget(""); setTargetDate(""); setAccountId(""); onChange(); },
    onError,
  });
  const setCurrent = useMutation({
    mutationFn: (v: { id: number; current_amount: string }) =>
      updateSavingsGoal(v.id, { current_amount: v.current_amount }),
    onSuccess: () => onChange(),
    onError,
  });
  const remove = useMutation({
    mutationFn: (id: number) => deleteSavingsGoal(id),
    onSuccess: () => onChange(),
    onError,
  });

  return (
    <div className="card">
      <h2 className="card__title">Goals</h2>
      {goals.length === 0 && <p className="muted">No goals yet. Set a target below.</p>}
      <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 12 }}>
        {goals.map((g) => {
          const done = g.percent >= 100;
          return (
            <li key={g.id}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span>
                  <strong>{g.name}</strong>{" "}
                  {done && <span className="status status--ok" style={{ padding: "0 6px" }}>achieved</span>}
                  {g.target_date && <span className="muted"> · by {g.target_date}</span>}
                  {g.account_id && <span className="muted"> · linked</span>}
                </span>
                <span className="muted">{g.current} / {g.target_amount} {base}</span>
              </div>
              <div style={{ background: "#2a2a2a", borderRadius: 4, height: 8, marginTop: 4, overflow: "hidden" }}>
                <div style={{ width: `${Math.min(100, g.percent)}%`, height: "100%", background: done ? "#3aa55a" : "#6aa9ff" }} />
              </div>
              <div style={{ marginTop: 4, fontSize: "0.85rem" }}>
                <span className="muted">{g.percent}%</span>
                {!g.account_id && (
                  <>
                    {" · "}
                    <button
                      className="link-btn"
                      onClick={() => {
                        const v = window.prompt(`Update current amount for "${g.name}" (${base})`, g.current_amount);
                        if (v != null && v.trim()) setCurrent.mutate({ id: g.id, current_amount: v.trim() });
                      }}
                    >
                      update amount
                    </button>
                  </>
                )}
                {" · "}
                <button className="link-btn" onClick={() => { if (window.confirm(`Delete goal "${g.name}"?`)) remove.mutate(g.id); }}>
                  delete
                </button>
              </div>
            </li>
          );
        })}
      </ul>

      <form
        className="form-row"
        style={{ marginTop: 14, flexWrap: "wrap" }}
        onSubmit={(e) => { e.preventDefault(); if (name && target) create.mutate(); }}
      >
        <input placeholder="Goal name" value={name} onChange={(e) => setName(e.target.value)} />
        <input placeholder={`Target (${base})`} value={target} style={{ width: 120 }} onChange={(e) => setTarget(e.target.value)} />
        <input type="date" value={targetDate} onChange={(e) => setTargetDate(e.target.value)} title="Target date (optional)" />
        <select value={accountId} onChange={(e) => setAccountId(e.target.value)} title="Link to a savings account (optional)">
          <option value="">Track manually</option>
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>Link: {a.name}</option>
          ))}
        </select>
        <button className="btn" type="submit" disabled={!name || !target || create.isPending}>
          {create.isPending ? "Adding…" : "Add goal"}
        </button>
      </form>
    </div>
  );
}
