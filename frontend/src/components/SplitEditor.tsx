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
  amount: string; // absolute magnitude, e.g. "40.00"
  categoryId: string;
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
const toCents = (v: string): number => Math.round((Number(String(v).replace(",", ".")) || 0) * 100);
const fromCents = (c: number): string => (c / 100).toFixed(2);

// Monotonic source of stable row keys (unique within the editor's lifetime).
let _rowSeq = 0;
const newRow = (amount = "", categoryId = ""): Row => ({ key: String(_rowSeq++), amount, categoryId });

export default function SplitEditor({ txnId, amount, currency, isSplit, categories, onDone }: Readonly<Props>) {
  const qc = useQueryClient();
  const sign = Number(amount) < 0 ? -1 : 1;
  const totalCents = Math.abs(toCents(amount));

  const [rows, setRows] = useState<Row[]>([newRow(), newRow()]);
  const [error, setError] = useState<string | null>(null);

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

  function updateRow(i: number, field: keyof Row, value: string) {
    setRows((rs) => rs.map((r, idx) => (idx === i ? { ...r, [field]: value } : r)));
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
        return { ...r, amount: fromCents(cents) };
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
            <button className="btn btn--ghost" disabled={clear.isPending} onClick={() => clear.mutate()}>
              {clear.isPending ? "Removing…" : "Remove split"}
            </button>
          )}
          <button className="btn btn--ghost" onClick={onDone}>Close</button>
        </div>
      </div>
    );
  }

  return (
    <div className="split-editor" style={{ padding: "0.75rem 0.25rem" }}>
      <p className="muted" style={{ marginTop: 0 }}>
        Split <strong>{amount} {currency}</strong> across categories. Parts must add up to the total.
      </p>

      <table className="table" style={{ marginBottom: "0.5rem" }}>
        <thead>
          <tr>
            <th className="num">Amount ({currency})</th>
            <th>Category</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.key}>
              <td className="num">
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  value={r.amount}
                  style={{ width: "7rem", textAlign: "right" }}
                  onChange={(e) => updateRow(i, "amount", e.target.value)}
                />
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
        <button className="link-btn" onClick={addRow}>+ Add part</button>
        <button className="link-btn" onClick={splitEvenly} title="Divide the total equally across every part">
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
        <button className="btn" disabled={save.isPending || !balanced} onClick={onSave}>
          {save.isPending ? "Saving…" : "Save split"}
        </button>
        {isSplit && (
          <button className="btn btn--ghost" disabled={clear.isPending} onClick={() => clear.mutate()}>
            {clear.isPending ? "Removing…" : "Remove split"}
          </button>
        )}
        <button className="btn btn--ghost" onClick={onDone}>Cancel</button>
      </div>
    </div>
  );
}
