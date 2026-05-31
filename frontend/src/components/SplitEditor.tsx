import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  clearSplits,
  getSplits,
  setSplits,
  type Category,
  type SplitInput,
} from "../api/client";

interface Row {
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
const toCents = (v: string): number => Math.round((Number(v) || 0) * 100);
const fromCents = (c: number): string => (c / 100).toFixed(2);

export default function SplitEditor({ txnId, amount, currency, isSplit, categories, onDone }: Props) {
  const qc = useQueryClient();
  const sign = Number(amount) < 0 ? -1 : 1;
  const totalCents = Math.abs(toCents(amount));

  const [rows, setRows] = useState<Row[]>([
    { amount: "", categoryId: "" },
    { amount: "", categoryId: "" },
  ]);
  const [error, setError] = useState<string | null>(null);

  // Prefill from existing splits when editing an already-split transaction.
  useQuery({
    queryKey: ["splits", txnId],
    queryFn: async () => {
      const res = await getSplits(txnId);
      if (res.splits.length > 0) {
        setRows(
          res.splits.map((s) => ({
            amount: fromCents(Math.abs(toCents(s.amount))),
            categoryId: s.category_id ? String(s.category_id) : "",
          })),
        );
      }
      return res;
    },
    enabled: isSplit,
    staleTime: 0,
  });

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
  });

  function updateRow(i: number, field: keyof Row, value: string) {
    setRows((rs) => rs.map((r, idx) => (idx === i ? { ...r, [field]: value } : r)));
  }
  function addRow() {
    setRows((rs) => [...rs, { amount: "", categoryId: "" }]);
  }
  function removeRow(i: number) {
    setRows((rs) => (rs.length > 2 ? rs.filter((_, idx) => idx !== i) : rs));
  }
  // Pour the remaining amount into the last row (spec §17.3 "auto-balance").
  function autoBalance() {
    setRows((rs) => {
      if (rs.length === 0) return rs;
      const last = rs.length - 1;
      const target = toCents(rs[last].amount) + (totalCents - rs.reduce((a, r) => a + toCents(r.amount), 0));
      return rs.map((r, idx) => (idx === last ? { ...r, amount: fromCents(Math.max(0, target)) } : r));
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
            <tr key={i}>
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
        <button className="link-btn" onClick={autoBalance} disabled={balanced}>Auto-balance</button>
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
