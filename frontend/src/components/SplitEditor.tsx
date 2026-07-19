import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  clearSplits,
  getSplits,
  setSplits,
  type Category,
  type SplitInput,
} from "../api/client";

interface Row {
  key: string; // stable React key — rows are added/removed, so index keys break input focus
  amount: string; // absolute magnitude, e.g. "40.00" — always the source of truth
  categoryId: string;
  pct: string; // percent-of-total the user typed in % mode (display/edit only)
}

interface Props {
  txnId: number;
  amount: string; // signed transaction amount, e.g. "-120.00"
  currency: string;
  isSplit: boolean;
  categories: Category[];
  onDone: () => void;
}

// Work in integer cents to avoid floating-point drift; the backend requires the
// parts to sum to the transaction total to the penny (spec §17.2).
// Accept European-style comma decimals ("12,50") by normalising the separator to
// a dot before parsing — otherwise Number() yields NaN and the value silently
// collapsed to 0.
const parseNum = (v: string): number => Number(String(v).replace(",", ".")) || 0;
const toCents = (v: string): number => Math.round(parseNum(v) * 100);
const fromCents = (c: number): string => (c / 100).toFixed(2);

// Percentages summing to (near) 100 — a millionth of a point of slack so typed
// values like 33.33/33.33/33.34 count as "whole" despite float noise.
const PCT_FULL_EPSILON = 1e-6;

// Apportion `total` cents across parts weighted by `pcts` (which sum to ~100), so
// the parts sum to exactly `total`. Each part rounds to its share; the leftover
// cent(s) land on the largest part — deterministic, and mirrors the penny-exact
// "split evenly" behaviour.
function apportionCents(pcts: number[], total: number): number[] {
  const cents = pcts.map((p) => Math.round((p / 100) * total));
  const diff = total - cents.reduce((acc, c) => acc + c, 0);
  if (diff !== 0 && cents.length > 0) {
    let maxI = 0;
    for (let i = 1; i < cents.length; i++) if (cents[i] > cents[maxI]) maxI = i;
    cents[maxI] += diff;
  }
  return cents;
}

// Derive each part's amount (in cents) from the typed percentages. When the
// percentages add up to 100 we force a penny-exact total (apportionCents);
// otherwise we honour the literal percentages and let "Remaining" show the gap.
function centsFromPercents(pctStrs: string[], total: number): number[] {
  const pcts = pctStrs.map(parseNum);
  const pctSum = pcts.reduce((acc, p) => acc + p, 0);
  if (Math.abs(pctSum - 100) < PCT_FULL_EPSILON) return apportionCents(pcts, total);
  return pcts.map((p) => Math.round((p / 100) * total));
}

// Monotonic source of stable row keys (unique within the editor's lifetime).
let _rowSeq = 0;
const newRow = (amount = "", categoryId = "", pct = ""): Row => ({ key: String(_rowSeq++), amount, categoryId, pct });

export default function SplitEditor({ txnId, amount, currency, isSplit, categories, onDone }: Readonly<Props>) {
  const qc = useQueryClient();
  const sign = Number(amount) < 0 ? -1 : 1;
  const totalCents = Math.abs(toCents(amount));

  const [rows, setRows] = useState<Row[]>([newRow(), newRow()]);
  const [error, setError] = useState<string | null>(null);
  // "amount" (default) = user types absolute amounts; "percent" = user types a % of
  // the total and we derive penny-exact amounts. Amount is always the source of
  // truth stored on each row, so toggling modes preserves the current split.
  const [mode, setMode] = useState<"amount" | "percent">("amount");

  // Prefill from existing splits when editing an already-split transaction.
  // Fetch in the query, but seed the rows in an effect that runs ONCE (ref guard)
  // — doing it inside queryFn meant a refetch (e.g. on window focus) re-ran the
  // setRows and wiped the user's in-progress edits.
  const existing = useQuery({
    queryKey: ["splits", txnId],
    queryFn: () => getSplits(txnId),
    enabled: isSplit,
    staleTime: 0,
  });
  const prefilled = useRef(false);
  useEffect(() => {
    if (prefilled.current || !existing.data) return;
    if (existing.data.splits.length > 0) {
      setRows(
        existing.data.splits.map((s) =>
          newRow(fromCents(Math.abs(toCents(s.amount))), s.category_id ? String(s.category_id) : ""),
        ),
      );
    }
    prefilled.current = true;
  }, [existing.data]);

  // A zero-amount transaction has nothing to divide — every part would have to be
  // zero, which the penny-exact validation rejects — so it can never balance into a
  // valid split. Treat it as a distinct, clearly-messaged state instead of letting
  // the user get stuck on a perpetually-invalid "off by 0.00" form.
  const zeroTotal = totalCents === 0;

  const sumCents = rows.reduce((acc, r) => acc + toCents(r.amount), 0);
  const remainingCents = totalCents - sumCents;
  const balanced = remainingCents === 0;

  const save = useMutation({
    mutationFn: () => {
      const splits: SplitInput[] = rows.map((r) => ({
        amount: fromCents(sign * Math.abs(toCents(r.amount))),
        category_id: r.categoryId ? Number(r.categoryId) : null,
      }));
      return setSplits(txnId, splits);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      qc.invalidateQueries({ queryKey: ["splits", txnId] });
      onDone();
    },
    onError: (e) => setError(String(e instanceof Error ? e.message : e)),
  });

  const clear = useMutation({
    mutationFn: () => clearSplits(txnId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      onDone();
    },
    onError: (e) => setError(String(e instanceof Error ? e.message : e)),
  });

  // Percent-of-total for a given amount (cents), for seeding the % inputs.
  const pctFromCents = (c: number): string => (totalCents > 0 ? ((c / totalCents) * 100).toFixed(2) : "0");

  function updateRow(i: number, field: keyof Row, value: string) {
    setRows((rs) => rs.map((r, idx) => (idx === i ? { ...r, [field]: value } : r)));
  }
  // In % mode the user edits percentages; amounts (the source of truth) are
  // recomputed from every row's percentage so the parts stay penny-exact.
  function updatePercent(i: number, pct: string) {
    setRows((rs) => {
      const next = rs.map((r, idx) => (idx === i ? { ...r, pct } : r));
      const cents = centsFromPercents(next.map((r) => r.pct), totalCents);
      return next.map((r, idx) => ({ ...r, amount: fromCents(cents[idx]) }));
    });
  }
  // Switch input mode. Amounts are always the stored value, so the current split
  // is preserved; entering % mode just seeds each row's % from its amount.
  function switchMode(next: "amount" | "percent") {
    if (next === mode) return;
    setMode(next);
    if (next === "percent") setRows((rs) => rs.map((r) => ({ ...r, pct: pctFromCents(toCents(r.amount)) })));
  }
  function addRow() {
    setRows((rs) => [...rs, newRow()]);
  }
  function removeRow(i: number) {
    setRows((rs) => (rs.length > 2 ? rs.filter((_, idx) => idx !== i) : rs));
  }
  // Split the total evenly across every part (spec §17.3). Pennies that don't
  // divide cleanly are spread one-each across the first rows so the parts still
  // sum to the total exactly.
  function splitEvenly() {
    setRows((rs) => {
      const n = rs.length;
      if (n === 0) return rs;
      const base = Math.floor(totalCents / n);
      let extra = totalCents - base * n;
      return rs.map((r) => {
        const cents = base + (extra > 0 ? 1 : 0);
        if (extra > 0) extra -= 1;
        return { ...r, amount: fromCents(cents), pct: pctFromCents(cents) };
      });
    });
  }

  function onSave() {
    setError(null);
    if (rows.length < 2) return setError("A split needs at least two parts.");
    if (rows.some((r) => toCents(r.amount) <= 0)) return setError("Every part needs an amount greater than zero.");
    if (rows.some((r) => !r.categoryId)) return setError("Every part needs a category.");
    if (!balanced) return setError(`Parts must total ${fromCents(totalCents)} ${currency} (off by ${fromCents(remainingCents)}).`);
    save.mutate();
  }

  if (zeroTotal) {
    return (
      <div className="split-editor" style={{ padding: "0.75rem 0.25rem" }}>
        <p className="muted" style={{ marginTop: 0 }}>
          This transaction has a total of <strong>{amount} {currency}</strong>, so there is
          nothing to split across categories. Assign a single category to it instead.
        </p>
        <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.75rem" }}>
          {isSplit && (
            <button type="button" className="btn btn--ghost" disabled={clear.isPending} onClick={() => clear.mutate()}>
              {clear.isPending ? "Removing…" : "Remove split"}
            </button>
          )}
          <button type="button" className="btn btn--ghost" onClick={onDone}>Close</button>
        </div>
      </div>
    );
  }

  return (
    <div className="split-editor" style={{ padding: "0.75rem 0.25rem" }}>
      <p className="muted" style={{ marginTop: 0 }}>
        Split <strong>{amount} {currency}</strong> across categories. Parts must add up to the total.
      </p>

      <fieldset
        className="form-row"
        style={{ gap: 4, alignItems: "center", marginBottom: "0.5rem", border: 0, marginInline: 0, padding: 0, minInlineSize: 0 }}
        aria-label="Enter parts by"
      >
        <span className="muted" style={{ marginRight: 4 }}>Enter by:</span>
        <button
          type="button"
          aria-pressed={mode === "amount"}
          className={"btn btn--sm" + (mode === "amount" ? "" : " btn--ghost")}
          onClick={() => switchMode("amount")}
        >
          Amount
        </button>
        <button
          type="button"
          aria-pressed={mode === "percent"}
          className={"btn btn--sm" + (mode === "percent" ? "" : " btn--ghost")}
          onClick={() => switchMode("percent")}
        >
          %
        </button>
      </fieldset>

      <table className="table" style={{ marginBottom: "0.5rem" }}>
        <thead>
          <tr>
            <th className="num">{mode === "percent" ? "Share (%)" : `Amount (${currency})`}</th>
            <th>Category</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.key}>
              <td className="num">
                {mode === "percent" ? (
                  <span style={{ display: "inline-flex", alignItems: "center", justifyContent: "flex-end", gap: 6 }}>
                    <input
                      type="number"
                      step="0.01"
                      min="0"
                      max="100"
                      value={r.pct}
                      aria-label="Percentage of total"
                      style={{ width: "5rem", textAlign: "right" }}
                      onChange={(e) => updatePercent(i, e.target.value)}
                    />
                    <span aria-hidden="true">%</span>
                    <span className="muted" style={{ minWidth: "6rem", textAlign: "right" }}>
                      = {fromCents(toCents(r.amount))} {currency}
                    </span>
                  </span>
                ) : (
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    value={r.amount}
                    aria-label={`Amount (${currency})`}
                    style={{ width: "7rem", textAlign: "right" }}
                    onChange={(e) => updateRow(i, "amount", e.target.value)}
                  />
                )}
              </td>
              <td>
                <select
                  className={r.categoryId ? "" : "select--empty"}
                  value={r.categoryId}
                  onChange={(e) => updateRow(i, "categoryId", e.target.value)}
                >
                  <option value="">— choose category —</option>
                  {categories.map((c) => (
                    <option key={c.id} value={c.id}>{c.name}</option>
                  ))}
                </select>
              </td>
              <td>
                <button
                  type="button"
                  className="link-btn"
                  disabled={rows.length <= 2}
                  title={rows.length <= 2 ? "A split needs at least two parts" : "Remove this part"}
                  onClick={() => removeRow(i)}
                >
                  remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
        <button type="button" className="link-btn" onClick={addRow}>+ Add part</button>
        <button type="button" className="link-btn" onClick={splitEvenly} title="Divide the total equally across every part">
          Split evenly
        </button>
        <span className={"muted"} style={{ marginLeft: "auto" }}>
          {balanced ? (
            <span className="tag">balanced ✓</span>
          ) : (
            <>Remaining: <strong>{fromCents(remainingCents)} {currency}</strong></>
          )}
        </span>
      </div>

      {error && <p className="status status--error" style={{ marginTop: "0.5rem" }}>{error}</p>}

      <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.75rem" }}>
        <button type="button" className="btn" disabled={save.isPending || !balanced} onClick={onSave}>
          {save.isPending ? "Saving…" : "Save split"}
        </button>
        {isSplit && (
          <button type="button" className="btn btn--ghost" disabled={clear.isPending} onClick={() => clear.mutate()}>
            {clear.isPending ? "Removing…" : "Remove split"}
          </button>
        )}
        <button type="button" className="btn btn--ghost" onClick={onDone}>Cancel</button>
      </div>
    </div>
  );
}
