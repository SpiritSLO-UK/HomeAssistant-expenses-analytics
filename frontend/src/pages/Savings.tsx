import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Sparkline from "../components/Sparkline";
import OverTimeChart from "../components/OverTimeChart";
import {
  adjustSavingsBalance,
  createSavingsAccount,
  createSavingsGoal,
  deleteSavingsGoal,
  getBalanceHistory,
  getSavingsHistory,
  getSavingsSummary,
  recordBalance,
  updateSavingsAccount,
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
  const [months, setMonths] = useState(12);
  const summary = useQuery({ queryKey: ["savings-summary"], queryFn: getSavingsSummary });
  const history = useQuery({ queryKey: ["savings-history-total", months], queryFn: () => getSavingsHistory(months) });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["savings-summary"] });
    qc.invalidateQueries({ queryKey: ["savings-history"] });
    qc.invalidateQueries({ queryKey: ["savings-history-total"] });
  };
  const fail = (e: unknown) => setErr(String(e));

  const base = summary.data?.currency ?? "GBP";
  const [showNew, setShowNew] = useState(false);

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

      <OverTimeChart
        title="Total savings over time"
        series={history.data}
        months={months}
        onMonths={setMonths}
        color="#3aa55a"
        emptyHint="Record a balance on a couple of months to see your savings trend here."
      />

      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
          <h2 className="card__title" style={{ margin: 0 }}>Savings accounts</h2>
          <button className="btn btn--sm" onClick={() => setShowNew((v) => !v)}>
            {showNew ? "Cancel" : "＋ New account"}
          </button>
        </div>
        {showNew && (
          <NewAccountForm onCreated={() => { invalidate(); setShowNew(false); }} onError={fail} />
        )}
        {summary.data?.accounts.length === 0 && !showNew && (
          <p className="muted">No savings accounts yet — add one with ＋ New account.</p>
        )}
        {summary.data?.accounts.map((a) => (
          <AccountCard key={a.id} account={a} base={base} onChange={invalidate} onError={fail} />
        ))}
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
}: Readonly<{
  account: SavingsAccount;
  base: string;
  onChange: () => void;
  onError: (e: unknown) => void;
}>) {
  const [open, setOpen] = useState(false);
  const history = useQuery({
    queryKey: ["savings-history", account.id],
    queryFn: () => getBalanceHistory(account.id),
  });
  const [date, setDate] = useState(today());
  const [amount, setAmount] = useState("");   // absolute "set balance" (from statement)
  const [delta, setDelta] = useState("");     // deposit / withdraw amount
  const [rate, setRate] = useState(account.interest_rate ?? "");

  const refresh = () => {
    onChange();
    history.refetch();
  };
  const setBalance = useMutation({
    mutationFn: () => recordBalance(account.id, { as_of_date: date, balance: amount }),
    onSuccess: () => { setAmount(""); refresh(); },
    onError,
  });
  const adjust = useMutation({
    mutationFn: (direction: "deposit" | "withdraw") =>
      adjustSavingsBalance(account.id, { amount: delta, direction }),
    onSuccess: () => { setDelta(""); refresh(); },
    onError,
  });
  const saveRate = useMutation({
    mutationFn: () =>
      updateSavingsAccount(account.id, { interest_rate: rate.trim() === "" ? null : rate.trim() }),
    onSuccess: () => refresh(),
    onError,
  });

  // Confirm a deposit/withdraw, showing the resulting balance, before applying.
  function doAdjust(direction: "deposit" | "withdraw") {
    const amt = Number(delta);
    if (!amt) return;
    const current = Number(account.latest_balance ?? 0);
    const next = direction === "deposit" ? current + amt : current - amt;
    const verb = direction === "deposit" ? "Deposit" : "Withdraw";
    if (globalThis.confirm(`${verb} ${amt.toFixed(2)} ${base}?\nNew balance: ${next.toFixed(2)} ${base}.`)) {
      adjust.mutate(direction);
    }
  }

  const hist = history.data ?? [];
  const points = hist.map((b) => Number(b.balance));
  // Balance-change log (newest first) with the delta vs the previous snapshot.
  const log = hist
    .map((b, i) => ({ ...b, delta: i > 0 ? Number(b.balance) - Number(hist[i - 1].balance) : null }))
    .reverse();

  return (
    <div style={{ borderTop: "1px solid #2a2a2a", padding: "12px 0" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
        <div>
          <button className="link-btn" style={{ fontWeight: 700 }} onClick={() => setOpen((v) => !v)}>
            {open ? "▾ " : "▸ "}{account.name}
          </button>{" "}
          {account.institution && <span className="muted">· {account.institution}</span>}
          {account.interest_rate && <span className="muted"> · {account.interest_rate}%</span>}
          <div style={{ fontSize: "1.2rem" }}>
            {account.latest_balance ? `${account.latest_balance} ${account.currency}` : <span className="muted">no balance yet</span>}
          </div>
        </div>
        {points.length >= 2 && <Sparkline values={points} color="#3aa55a" />}
      </div>

      {open && (
        <div style={{ marginTop: 8 }}>
          {/* Deposit / withdraw — adjusts the latest balance (confirmed). */}
          <div className="form-row" style={{ alignItems: "center", gap: 6 }}>
            <input
              placeholder={`Amount (${base})`}
              value={delta}
              style={{ width: 120 }}
              onChange={(e) => setDelta(e.target.value)}
            />
            <button className="btn btn--sm" disabled={!delta || adjust.isPending} onClick={() => doAdjust("deposit")}>
              ＋ Deposit
            </button>
            <button className="btn btn--sm btn--ghost" disabled={!delta || adjust.isPending} onClick={() => doAdjust("withdraw")}>
              － Withdraw
            </button>
          </div>

          {/* Interest rate + projected annual interest. */}
          <div className="form-row" style={{ marginTop: 6, alignItems: "center", gap: 6 }}>
            <label className="muted" style={{ fontSize: "0.85rem" }}>
              Interest{" "}
              <input
                type="number" step="0.01" min="0"
                placeholder="0.00"
                value={rate}
                style={{ width: 70 }}
                onChange={(e) => setRate(e.target.value)}
              />{" "}
              %
            </label>
            <button className="btn btn--sm btn--ghost" disabled={saveRate.isPending} onClick={() => saveRate.mutate()}>
              Save rate
            </button>
            {account.interest_rate && account.projected_annual_interest && (
              <span className="muted" style={{ fontSize: "0.82rem" }}>
                ≈ {account.projected_annual_interest} {account.currency}/yr
              </span>
            )}
          </div>

          {/* Set an absolute balance (e.g. from a statement). */}
          <form
            className="form-row"
            style={{ marginTop: 6 }}
            onSubmit={(e) => { e.preventDefault(); if (amount) setBalance.mutate(); }}
          >
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            <input
              placeholder={`Set balance (${base})`}
              value={amount}
              style={{ width: 150 }}
              onChange={(e) => setAmount(e.target.value)}
            />
            <button className="btn btn--sm btn--ghost" type="submit" disabled={!amount || setBalance.isPending}>
              {setBalance.isPending ? "Saving…" : "Set from statement"}
            </button>
          </form>

          {/* Balance-change history (the account's deposits/withdrawals). */}
          <div style={{ marginTop: 10 }}>
            <div className="muted" style={{ fontSize: "0.72rem", textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 4 }}>
              History
            </div>
            {log.length === 0 ? (
              <p className="muted" style={{ margin: 0 }}>No balances recorded yet.</p>
            ) : (
              <ul className="kv" style={{ margin: 0, maxWidth: 460 }}>
                {log.map((b) => (
                  <li key={b.id}>
                    <span className="muted">{b.as_of_date}{b.note ? ` · ${b.note}` : ""}</span>
                    <span style={{ whiteSpace: "nowrap" }}>
                      {b.balance} {account.currency}
                      {b.delta != null && b.delta !== 0 && (
                        <span className={b.delta > 0 ? "amt--pos" : "amt--neg"} style={{ marginLeft: 6, fontSize: "0.82rem" }}>
                          {b.delta > 0 ? "+" : ""}{b.delta.toFixed(2)}
                        </span>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function NewAccountForm({ onCreated, onError }: Readonly<{ onCreated: () => void; onError: (e: unknown) => void }>) {
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
}: Readonly<{
  goals: SavingsGoal[];
  accounts: SavingsAccount[];
  base: string;
  onChange: () => void;
  onError: (e: unknown) => void;
}>) {
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
                        const v = globalThis.prompt(`Update current amount for "${g.name}" (${base})`, g.current_amount);
                        if (v?.trim()) setCurrent.mutate({ id: g.id, current_amount: v.trim() });
                      }}
                    >
                      update amount
                    </button>
                  </>
                )}
                {" · "}
                <button className="link-btn" onClick={() => { if (globalThis.confirm(`Delete goal "${g.name}"?`)) remove.mutate(g.id); }}>
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
