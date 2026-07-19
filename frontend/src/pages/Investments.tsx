import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Sparkline from "../components/Sparkline";
import RangeSelector from "../components/RangeSelector";
import {
  adjustAccountValue,
  createHolding,
  createInvestmentAccount,
  deleteHolding,
  getHoldings,
  getInvestmentHistory,
  getInvestmentPriceStatus,
  getInvestmentSummary,
  getMe,
  getValueHistory,
  recordAccountValue,
  syncInvestmentPrices,
  updateHolding,
  updateSettings,
  type Holding,
  type InvestmentAccount,
  type PriceSyncResult,
} from "../api/client";
import { isAmount, parseAmount } from "../lib/num";
import { useServerState } from "../lib/useServerState";
import { formatDate, useDateFormat } from "../lib/date";
import { useConfirm } from "../components/dialogs";
import { useOptimisticSelect } from "../hooks/useOptimisticSelect";

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

// The history endpoint takes a day count, but the range picker speaks in whole
// months. Return the *actual* number of days spanned by going back `months`
// calendar months from today, so the fetched window matches the selection
// instead of overshooting (a flat months*31 pulled ~a week extra per year).
function monthsToDays(months: number): number {
  const end = new Date();
  const start = new Date(end);
  start.setMonth(start.getMonth() - months);
  const dayMs = 24 * 60 * 60 * 1000;
  return Math.max(1, Math.round((end.getTime() - start.getTime()) / dayMs));
}

// A signed-amount span: green when ≥ 0, red below. `pct` is optional.
function Gain({ value, pct, currency }: Readonly<{ value: string | null; pct: number | null; currency: string }>) {
  if (value == null) return null;
  const n = Number(value);
  return (
    <span className={n >= 0 ? "amt--pos" : "amt--neg"}>
      {n >= 0 ? "+" : ""}
      {value} {currency}
      {pct != null && <span style={{ marginLeft: 4 }}>({n >= 0 ? "+" : ""}{pct}%)</span>}
    </span>
  );
}

// A labelled period-change figure (Day/Month/Year), green up / red down.
function PeriodChip({ label, ch, currency }: Readonly<{ label: string; ch: { change: string; pct: number | null }; currency: string }>) {
  const n = Number(ch.change);
  const negOrFlat = n < 0 ? "amt--neg" : "muted";
  const cls = n > 0 ? "amt--pos" : negOrFlat;
  return (
    <div style={{ minWidth: 96 }}>
      <div className="stat__label">{label}</div>
      <div className={cls} style={{ fontWeight: 600 }}>
        {n >= 0 ? "+" : ""}{ch.change} {currency}
        {ch.pct != null && <span> ({n >= 0 ? "+" : ""}{ch.pct}%)</span>}
      </div>
    </div>
  );
}

export default function Investments() {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const summary = useQuery({ queryKey: ["investment-summary"], queryFn: getInvestmentSummary });
  const [months, setMonths] = useState(12);
  const history = useQuery({
    queryKey: ["investment-history", months],
    queryFn: () => getInvestmentHistory(monthsToDays(months)),
  });

  // Passed to every child card as the success callback (onChange/onCreated), so
  // clearing the error here drops a stale banner after a later success (FE-3).
  const invalidate = () => {
    setErr(null);
    qc.invalidateQueries({ queryKey: ["investment-summary"] });
    qc.invalidateQueries({ queryKey: ["investment-holdings"] });
    qc.invalidateQueries({ queryKey: ["investment-values"] });
    qc.invalidateQueries({ queryKey: ["investment-history"] });
  };
  const fail = (e: unknown) => setErr(String(e));

  const base = summary.data?.currency ?? "GBP";
  const [showNew, setShowNew] = useState(false);

  return (
    <div className="page">
      <div className="page__head">
        <h1 className="page__title">Investments &amp; pensions</h1>
      </div>
      <p className="muted">
        Two tracking models, by account type. <strong>Investments</strong> (shares / ISA) hold{" "}
        <strong>holdings</strong> — a ticker with a number of shares and a cost per share — for market
        value, gain, and value over time with day/month/year change; prices are kept current by the
        optional price feed (Settings → price source, below). <strong>Pensions</strong> track a{" "}
        <strong>value</strong> from a statement, with contributions and withdrawals.
      </p>
      {err && <p className="status status--error">{err}</p>}

      <PricesCard onSynced={invalidate} onError={fail} />

      {summary.data && summary.data.accounts.length > 0 && (
        <div className="card">
          <h2 className="card__title">Portfolio</h2>
          <p style={{ fontSize: "1.6rem", margin: 0 }}>
            <strong>{summary.data.total_value} {base}</strong>{" "}
            <span className="muted">across {summary.data.accounts.length} account(s)</span>
          </p>
          {summary.data.total_gain != null && (
            <p style={{ margin: "4px 0 0" }}>
              Unrealised{" "}
              <Gain value={summary.data.total_gain} pct={summary.data.total_gain_pct} currency={base} />
            </p>
          )}
          <div className="form-row" style={{ gap: 4, marginTop: 10 }} title="Time range">
            <RangeSelector months={months} onChange={setMonths} />
          </div>
          {history.data && history.data.points.length >= 2 && (
            <div style={{ margin: "10px 0" }}>
              <Sparkline values={history.data.points.map((p) => Number(p.value))} color="#6aa9ff" width={560} height={120} />
            </div>
          )}
          {history.data && (
            <div className="form-row" style={{ gap: 18, marginTop: 8, flexWrap: "wrap" }}>
              <PeriodChip label="Day" ch={history.data.change_day} currency={base} />
              <PeriodChip label="Month" ch={history.data.change_month} currency={base} />
              <PeriodChip label="Year" ch={history.data.change_year} currency={base} />
            </div>
          )}
          <ul className="kv" style={{ marginTop: 8, maxWidth: 360 }}>
            <li><span>Investments</span><span>{summary.data.by_type.investment} {base}</span></li>
            <li><span>Pensions</span><span>{summary.data.by_type.pension} {base}</span></li>
          </ul>
        </div>
      )}

      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
          <h2 className="card__title" style={{ margin: 0 }}>Accounts</h2>
          <button className="btn btn--sm" onClick={() => setShowNew((v) => !v)}>
            {showNew ? "Cancel" : "＋ New account"}
          </button>
        </div>
        {showNew && (
          <NewAccountForm onCreated={() => { invalidate(); setShowNew(false); }} onError={fail} />
        )}
        {summary.data?.accounts.length === 0 && !showNew && (
          <p className="muted">No investment or pension accounts yet — add one with ＋ New account.</p>
        )}
        {summary.data?.accounts.map((a) => (
          <AccountCard key={a.id} account={a} base={base} onChange={invalidate} onError={fail} />
        ))}
      </div>
    </div>
  );
}

function NewAccountForm({ onCreated, onError }: Readonly<{ onCreated: () => void; onError: (e: unknown) => void }>) {
  const [name, setName] = useState("");
  const [type, setType] = useState<"investment" | "pension">("investment");
  const [institution, setInstitution] = useState("");
  const create = useMutation({
    mutationFn: () =>
      createInvestmentAccount({ name, account_type: type, institution: institution || undefined }),
    onSuccess: () => { setName(""); setInstitution(""); onCreated(); },
    onError,
  });
  return (
    <form
      className="form-row"
      style={{ marginTop: 12, flexWrap: "wrap" }}
      onSubmit={(e) => { e.preventDefault(); if (name) create.mutate(); }}
    >
      <input placeholder="Account name (e.g. Trading 212, Aviva pension)" value={name} onChange={(e) => setName(e.target.value)} />
      <select value={type} onChange={(e) => setType(e.target.value as "investment" | "pension")}>
        <option value="investment">Investment (shares / ISA — track holdings)</option>
        <option value="pension">Pension (track a statement value)</option>
      </select>
      <input placeholder="Provider (optional)" value={institution} onChange={(e) => setInstitution(e.target.value)} />
      <button className="btn" type="submit" disabled={!name || create.isPending}>
        {create.isPending ? "Adding…" : "Add account"}
      </button>
    </form>
  );
}

const PRICE_SOURCES: { value: string; label: string }[] = [
  { value: "manual", label: "Manual (off) — type prices yourself" },
  { value: "stooq", label: "Stooq — free, no API key (suffix tickers: aapl.us, vwrl.uk)" },
  { value: "alphavantage", label: "Alpha Vantage — needs HAFI_INVESTMENT_API_KEY" },
];

function PricesCard({ onSynced, onError }: Readonly<{ onSynced: () => void; onError: (e: unknown) => void }>) {
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: getMe });
  const status = useQuery({ queryKey: ["investment-price-status"], queryFn: getInvestmentPriceStatus });
  const [result, setResult] = useState<PriceSyncResult | null>(null);
  const canManage = me.data?.can_manage_settings === true || me.data?.is_admin === true;

  const setSource = useMutation({
    mutationFn: (source: string) => updateSettings({ investment_price_source: source }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["investment-price-status"] });
      onSynced(); // parent's success callback — also clears any stale error banner (FE-3)
    },
    onError,
  });
  const sync = useMutation({
    mutationFn: () => syncInvestmentPrices(),
    onSuccess: (r) => { setResult(r); onSynced(); },
    onError,
  });

  // Optimistic overlay for the price-source select (FE-8): show the chosen source
  // at once and revert to the server value if the mutation fails. setSource keeps
  // its own onError, so the overlay reverts silently. Singleton, keyed by a constant.
  const sourceSelect = useOptimisticSelect<string, string>();

  const source = status.data?.source ?? "manual";
  const ready = status.data?.ready ?? false;
  const failedSuffix = result?.failed ? ` · ${result.failed} not found` : "";

  return (
    <div className="card">
      <h2 className="card__title">Price updates</h2>
      <p className="muted" style={{ marginTop: 0, fontSize: "0.85rem" }}>
        Keep holding prices current automatically. Only the ticker symbol is ever sent —
        never balances or holdings. Off by default; also runs on startup when enabled.
      </p>
      <div className="form-row" style={{ alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <label className="muted" style={{ fontSize: "0.85rem" }}>
          Source{" "}
          <select
            value={sourceSelect.valueFor("source", source)}
            disabled={!canManage}
            onChange={(e) => {
              const next = e.target.value;
              sourceSelect.choose("source", next, () => setSource.mutateAsync(next));
            }}
          >
            {PRICE_SOURCES.map((s) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>
        </label>
        <button className="btn btn--sm" disabled={!ready || sync.isPending} onClick={() => sync.mutate()}>
          {sync.isPending ? "Syncing…" : "Sync prices now"}
        </button>
      </div>
      {!canManage && (
        <p className="muted" style={{ fontSize: "0.8rem", margin: "6px 0 0" }}>
          Only a settings manager can change the source. You can still sync when it's enabled.
        </p>
      )}
      {source === "alphavantage" && status.data && !status.data.api_key_present && (
        <p className="status status--warn" style={{ marginTop: 6 }}>
          Set <code>HAFI_INVESTMENT_API_KEY</code> in the add-on/env to use Alpha Vantage.
        </p>
      )}
      {result && (
        <p className="muted" style={{ marginTop: 6, fontSize: "0.85rem" }}>
          {result.ran
            ? `Updated ${result.updated} of ${result.total} holding(s)${failedSuffix}.`
            : "Nothing to sync (source is manual)."}
        </p>
      )}
    </div>
  );
}

function AccountCard({
  account,
  base,
  onChange,
  onError,
}: Readonly<{
  account: InvestmentAccount;
  base: string;
  onChange: () => void;
  onError: (e: unknown) => void;
}>) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ borderTop: "1px solid #2a2a2a", padding: "12px 0" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
        <div>
          <button className="link-btn" style={{ fontWeight: 700 }} onClick={() => setOpen((v) => !v)}>
            {open ? "▾ " : "▸ "}{account.name}
          </button>{" "}
          <span className="tag">{account.account_type}</span>
          {account.institution && <span className="muted"> · {account.institution}</span>}
          <div style={{ fontSize: "1.2rem" }}>
            {account.current_value == null
              ? <span className="muted">no value yet</span>
              : `${account.current_value} ${account.currency}`}
            {account.gain != null && (
              <span style={{ marginLeft: 8, fontSize: "0.9rem" }}>
                <Gain value={account.gain} pct={account.gain_pct} currency={account.currency} />
              </span>
            )}
          </div>
        </div>
      </div>

      {open && (
        <div style={{ marginTop: 8 }}>
          {account.account_type === "pension" ? (
            // Pensions are statement-valued: record a value / log contributions.
            <ValueSection account={account} base={base} onChange={onChange} onError={onError} />
          ) : (
            // Investments (shares/ISA) are valued by holdings × price — no cash controls.
            <HoldingsSection account={account} onChange={onChange} onError={onError} />
          )}
        </div>
      )}
    </div>
  );
}

function HoldingsSection({
  account,
  onChange,
  onError,
}: Readonly<{
  account: InvestmentAccount;
  onChange: () => void;
  onError: (e: unknown) => void;
}>) {
  const holdings = useQuery({
    queryKey: ["investment-holdings", account.id],
    queryFn: () => getHoldings(account.id),
  });
  const refresh = () => { onChange(); holdings.refetch(); };

  const [symbol, setSymbol] = useState("");
  const [units, setUnits] = useState("");
  const [avgCost, setAvgCost] = useState("");

  const add = useMutation({
    mutationFn: () =>
      createHolding(account.id, {
        symbol,
        units,
        avg_cost: avgCost || undefined,
        // No last price here — it's set by the price feed (Settings → price source),
        // so it stays current instead of being hand-entered once and going stale.
      }),
    onSuccess: () => { setSymbol(""); setUnits(""); setAvgCost(""); refresh(); },
    onError,
  });

  const rows = holdings.data ?? [];
  return (
    <div style={{ marginBottom: 14 }}>
      <div className="muted" style={{ fontSize: "0.72rem", textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 4 }}>
        Holdings
      </div>
      {rows.length === 0 ? (
        <p className="muted" style={{ margin: "0 0 6px" }}>No holdings yet — add a ticker below to track market value and gain.</p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Ticker</th><th># of shares</th><th>Cost per share</th><th>Last price</th><th>Value</th><th>Gain</th><th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((h) => (
                <HoldingRow key={h.id} holding={h} onChange={refresh} onError={onError} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      <form
        className="form-row"
        style={{ marginTop: 6, flexWrap: "wrap", gap: 6 }}
        onSubmit={(e) => { e.preventDefault(); if (symbol && isAmount(units) && (avgCost === "" || isAmount(avgCost))) add.mutate(); }}
      >
        <input placeholder="Ticker (AAPL)" value={symbol} style={{ width: 110 }} onChange={(e) => setSymbol(e.target.value)} />
        <input inputMode="decimal" placeholder="# of shares" value={units} style={{ width: 100 }} onChange={(e) => setUnits(e.target.value)} />
        <input inputMode="decimal" placeholder="Cost per share" value={avgCost} style={{ width: 120 }} onChange={(e) => setAvgCost(e.target.value)} />
        <button className="btn btn--sm" type="submit" disabled={!symbol || !isAmount(units) || (avgCost !== "" && !isAmount(avgCost)) || add.isPending}>
          {add.isPending ? "Adding…" : "＋ Add holding"}
        </button>
      </form>
    </div>
  );
}

function HoldingRow({
  holding,
  onChange,
  onError,
}: Readonly<{
  holding: Holding;
  onChange: () => void;
  onError: (e: unknown) => void;
}>) {
  const confirm = useConfirm();
  const [units, setUnits] = useServerState(holding.units);
  const [avgCost, setAvgCost] = useServerState(holding.avg_cost ?? "");

  const save = useMutation({
    mutationFn: () =>
      updateHolding(holding.id, {
        units,
        avg_cost: avgCost.trim() === "" ? null : avgCost.trim(),
      }),
    onSuccess: () => onChange(),
    onError,
  });
  const remove = useMutation({
    mutationFn: () => deleteHolding(holding.id),
    onSuccess: () => onChange(),
    onError,
  });

  const dirty =
    units !== holding.units ||
    avgCost !== (holding.avg_cost ?? "");

  return (
    <tr>
      <td>
        <strong>{holding.symbol}</strong>
        {holding.name && <div className="muted" style={{ fontSize: "0.78rem" }}>{holding.name}</div>}
      </td>
      <td><input inputMode="decimal" value={units} style={{ width: 70 }} onChange={(e) => setUnits(e.target.value)} /></td>
      <td><input inputMode="decimal" value={avgCost} placeholder="—" style={{ width: 70 }} onChange={(e) => setAvgCost(e.target.value)} /></td>
      <td style={{ whiteSpace: "nowrap" }} title="Set automatically by the price feed (Settings → price source)">
        {holding.last_price ?? "—"}
      </td>
      <td style={{ whiteSpace: "nowrap" }}>{holding.market_value ?? "—"}</td>
      <td style={{ whiteSpace: "nowrap" }}>
        {holding.gain == null
          ? "—"
          : <Gain value={holding.gain} pct={holding.gain_pct} currency={holding.currency} />}
      </td>
      <td style={{ whiteSpace: "nowrap" }}>
        <button className="btn btn--sm btn--ghost" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
          Save
        </button>{" "}
        <button
          className="link-btn"
          onClick={async () => { if (await confirm({ message: `Remove holding ${holding.symbol}?`, confirmLabel: "Remove", danger: true })) remove.mutate(); }}
        >
          ✕
        </button>
      </td>
    </tr>
  );
}

function ValueSection({
  account,
  base,
  onChange,
  onError,
}: Readonly<{
  account: InvestmentAccount;
  base: string;
  onChange: () => void;
  onError: (e: unknown) => void;
}>) {
  const dateFmt = useDateFormat();
  const history = useQuery({
    queryKey: ["investment-values", account.id],
    queryFn: () => getValueHistory(account.id),
  });
  const refresh = () => { onChange(); history.refetch(); };

  const [date, setDate] = useState(today());
  const [value, setValue] = useState("");
  const [delta, setDelta] = useState("");

  const setValueM = useMutation({
    mutationFn: () => recordAccountValue(account.id, { as_of_date: date, value }),
    onSuccess: () => { setValue(""); refresh(); },
    onError,
  });
  const adjust = useMutation({
    mutationFn: (direction: "contribution" | "withdrawal") =>
      adjustAccountValue(account.id, { amount: delta, direction }),
    onSuccess: () => { setDelta(""); refresh(); },
    onError,
  });

  function doAdjust(direction: "contribution" | "withdrawal") {
    const amt = parseAmount(delta);
    if (amt == null || amt === 0) return;
    adjust.mutate(direction);
  }

  const hist = history.data ?? [];
  const points = hist.map((b) => Number(b.value));
  const log = hist
    .map((b, i) => ({ ...b, delta: i > 0 ? Number(b.value) - Number(hist[i - 1].value) : null }))
    .reverse();

  // Only meaningful when the account is tracked as a lump value (no holdings).
  const note = account.has_holdings
    ? "This account's value comes from its holdings above. Snapshots below are an optional manual record."
    : "Record this account's total value from a statement, or log a contribution/withdrawal.";

  return (
    <div>
      <div className="muted" style={{ fontSize: "0.72rem", textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 4 }}>
        Value snapshots
      </div>
      <p className="muted" style={{ margin: "0 0 6px", fontSize: "0.82rem" }}>{note}</p>

      <div className="form-row" style={{ alignItems: "center", gap: 6 }}>
        <input inputMode="decimal" placeholder={`Amount (${base})`} value={delta} style={{ width: 120 }} onChange={(e) => setDelta(e.target.value)} />
        <button className="btn btn--sm" disabled={!isAmount(delta) || adjust.isPending} onClick={() => doAdjust("contribution")}>
          ＋ Contribution
        </button>
        <button className="btn btn--sm btn--ghost" disabled={!isAmount(delta) || adjust.isPending} onClick={() => doAdjust("withdrawal")}>
          － Withdrawal
        </button>
      </div>

      <form
        className="form-row"
        style={{ marginTop: 6 }}
        onSubmit={(e) => { e.preventDefault(); if (isAmount(value)) setValueM.mutate(); }}
      >
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        <input inputMode="decimal" placeholder={`Set value (${base})`} value={value} style={{ width: 150 }} onChange={(e) => setValue(e.target.value)} />
        <button className="btn btn--sm btn--ghost" type="submit" disabled={!isAmount(value) || setValueM.isPending}>
          {setValueM.isPending ? "Saving…" : "Set from statement"}
        </button>
      </form>

      <div style={{ marginTop: 10 }}>
        {points.length >= 2 && <Sparkline values={points} color="#6aa9ff" />}
        {log.length === 0 ? (
          <p className="muted" style={{ margin: 0 }}>No values recorded yet.</p>
        ) : (
          <ul className="kv" style={{ margin: 0, maxWidth: 460 }}>
            {log.map((b) => (
              <li key={b.id}>
                <span className="muted">{formatDate(b.as_of_date, dateFmt)}{b.note ? ` · ${b.note}` : ""}</span>
                <span style={{ whiteSpace: "nowrap" }}>
                  {b.value} {account.currency}
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
  );
}
