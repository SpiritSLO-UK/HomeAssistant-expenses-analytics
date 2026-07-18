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
import { isAmount, parseAmount } from "../lib/num";
import { money } from "../lib/money";
import { useServerState } from "../lib/useServerState";
import { useConfirm, usePrompt } from "../components/dialogs";

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function Savings() {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const [months, setMonths] = useState(12);
  const summary = useQuery({ queryKey: ["savings-summary"], queryFn: getSavingsSummary });
  const history = useQuery({ queryKey: ["savings-history-total", months], queryFn: () => getSavingsHistory(months) });

  // Passed to every child card as onChange/onCreated — i.e. the success callback —
  // so clearing the error here drops any stale banner after a later success (FE-3).
  const invalidate = () => {
    setErr(null);
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

// The compact per-account balance series the savings summary now carries, used
// to draw the collapsed-row sparkline without a per-account fetch. The shared
// SavingsAccount type in client.ts doesn't model it yet (out of scope here), so
// we read it through a local widening view.
function accountBalanceSeries(account: SavingsAccount): number[] {
  const series = (account as SavingsAccount & { balance_series?: (string | number)[] }).balance_series ?? [];
  return series.map((v) => Number(v));
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
  const confirm = useConfirm();
  const [open, setOpen] = useState(false);
  // Full snapshot history drives only the expanded panel (change log), so it's
  // fetched lazily on open. The collapsed sparkline reads the summary-provided
  // series instead, so no per-account request fires on mount.
  const history = useQuery({
    queryKey: ["savings-history", account.id],
    queryFn: () => getBalanceHistory(account.id),
    enabled: open,
  });
  const [date, setDate] = useState(today());
  const [amount, setAmount] = useState("");   // absolute "set balance" (from statement)
  const [delta, setDelta] = useState("");     // deposit / withdraw amount
  const [rate, setRate] = useServerState(account.interest_rate ?? "");

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
  async function doAdjust(direction: "deposit" | "withdraw") {
    const amt = parseAmount(delta);
    if (amt == null || amt === 0) return;
    const current = Number(account.latest_balance ?? 0);
    const next = direction === "deposit" ? current + amt : current - amt;
    const verb = direction === "deposit" ? "Deposit" : "Withdraw";
    if (await confirm({ message: `${verb} ${money(amt, base)}?\nNew balance: ${money(next, base)}.`, confirmLabel: verb })) {
      adjust.mutate(direction);
    }
  }

  // A deposit/withdraw needs a valid, non-zero, non-negative amount (parseAmount
  // rejects blank/NaN/negative; zero is a no-op) to enable the buttons.
  const deltaNum = parseAmount(delta);
  const canAdjust = deltaNum != null && deltaNum !== 0;

  const hist = history.data ?? [];
  // Collapsed-row sparkline reads the summary-batched series (no per-account fetch).
  const sparkPoints = accountBalanceSeries(account);
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
        {sparkPoints.length >= 2 && <Sparkline values={sparkPoints} color="#3aa55a" />}
      </div>

      {open && (
        <div style={{ marginTop: 8 }}>
          {/* Deposit / withdraw — adjusts the latest balance (confirmed). */}
          <div className="form-row" style={{ alignItems: "center", gap: 6 }}>
            <input
              inputMode="decimal"
              placeholder={`Amount (${base})`}
              value={delta}
              style={{ width: 120 }}
              onChange={(e) => setDelta(e.target.value)}
            />
            <button className="btn btn--sm" disabled={!canAdjust || adjust.isPending} onClick={() => doAdjust("deposit")}>
              ＋ Deposit
            </button>
            <button className="btn btn--sm btn--ghost" disabled={!canAdjust || adjust.isPending} onClick={() => doAdjust("withdraw")}>
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
            <button className="btn btn--sm btn--ghost" disabled={(rate.trim() !== "" && !isAmount(rate)) || saveRate.isPending} onClick={() => saveRate.mutate()}>
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
            onSubmit={(e) => { e.preventDefault(); if (isAmount(amount)) setBalance.mutate(); }}
          >
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            <input
              inputMode="decimal"
              placeholder={`Set balance (${base})`}
              value={amount}
              style={{ width: 150 }}
              onChange={(e) => setAmount(e.target.value)}
            />
            <button className="btn btn--sm btn--ghost" type="submit" disabled={!isAmount(amount) || setBalance.isPending}>
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

// The deposit-rate/time-to-goal forecast the API now surfaces on each goal. The
// shared SavingsGoal type in client.ts doesn't model it yet (out of scope here),
// so we read it through a local widening view.
type GoalForecast = {
  state: string;
  monthly_deposit_rate: string | null;
  projected_date: string | null;
  months_remaining: number | null;
  on_track: boolean | null;
};

// Only the states worth a line get a label; no_forecast/achieved stay silent
// (the "achieved" badge already covers the latter).
const FORECAST_LABEL: Record<string, string> = {
  on_track: "on track",
  behind: "behind schedule",
  projected: "projected",
  not_progressing: "not progressing",
};

function goalForecast(goal: SavingsGoal): GoalForecast | undefined {
  return (goal as SavingsGoal & { forecast?: GoalForecast }).forecast;
}

function GoalForecastLine({ forecast, base }: Readonly<{ forecast?: GoalForecast; base: string }>) {
  const label = forecast ? FORECAST_LABEL[forecast.state] : undefined;
  if (!forecast || !label) return null;
  const behind = forecast.state === "behind" || forecast.state === "not_progressing";
  const months = forecast.months_remaining;
  return (
    <div className="muted" style={{ fontSize: "0.82rem", marginTop: 2 }}>
      {forecast.monthly_deposit_rate && <>≈ {forecast.monthly_deposit_rate} {base}/mo · </>}
      <span className={behind ? "amt--neg" : "amt--pos"}>{label}</span>
      {forecast.projected_date && <> · reaches target {forecast.projected_date}</>}
      {months != null && <> (~{months} mo)</>}
    </div>
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
  const confirm = useConfirm();
  const prompt = usePrompt();
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
                      onClick={async () => {
                        const v = await prompt({ title: "Update current amount", message: `Update current amount for "${g.name}" (${base})`, defaultValue: String(g.current_amount ?? ""), confirmLabel: "Update" });
                        const trimmed = v?.trim();
                        if (trimmed && isAmount(trimmed)) setCurrent.mutate({ id: g.id, current_amount: trimmed });
                      }}
                    >
                      update amount
                    </button>
                  </>
                )}
                {" · "}
                <button className="link-btn" onClick={async () => { if (await confirm({ message: `Delete goal "${g.name}"?`, confirmLabel: "Delete", danger: true })) remove.mutate(g.id); }}>
                  delete
                </button>
              </div>
              <GoalForecastLine forecast={goalForecast(g)} base={base} />
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
