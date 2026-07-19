import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { applyAiCategories, classifyBatch, type BatchSuggestion } from "../api/client";
import { money } from "../lib/money";
import { downloadCsvFile, suggestionsToCsv } from "../lib/csv";

// Header "select all" checkbox. React has no `indeterminate` prop, so the DOM
// property is set imperatively via a ref: unchecked when none are selected,
// checked when all are, indeterminate when only some are.
function SelectAllCheckbox({
  allSelected,
  someSelected,
  onToggle,
}: Readonly<{ allSelected: boolean; someSelected: boolean; onToggle: (checked: boolean) => void }>) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = someSelected;
  }, [someSelected]);
  return (
    <input
      ref={ref}
      type="checkbox"
      title="Select all suggestions"
      checked={allSelected}
      onChange={(e) => onToggle(e.target.checked)}
    />
  );
}

/**
 * Batch "AI categorise uncategorised" (local LLM only). Scans, shows each
 * suggestion with a checkbox (pre-ticked above the confidence threshold), and
 * applies only what the user keeps ticked — AI never auto-applies silently.
 */
export default function AiBatchPanel({ base, onClose }: Readonly<{ base: string; onClose: () => void }>) {
  const qc = useQueryClient();
  const [threshold, setThreshold] = useState(0.8);
  const [recheck, setRecheck] = useState(false);
  const [suggestions, setSuggestions] = useState<BatchSuggestion[] | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const scan = useMutation({
    mutationFn: () => classifyBatch(50, recheck ? "recheck" : "uncategorised"),
    onSuccess: (res) => {
      setSuggestions(res.suggestions);
      setPicked(new Set(res.suggestions.filter((s) => (s.confidence ?? 0) >= threshold).map((s) => s.transaction_id)));
      setMsg(`Scanned ${res.considered} transaction(s) · ${res.count} suggestion(s).`);
      setErr(null);
    },
    onError: (e) => setErr(String(e instanceof Error ? e.message : e)),
  });

  const apply = useMutation({
    mutationFn: () => {
      const items = (suggestions ?? [])
        .filter((s) => picked.has(s.transaction_id))
        .map((s) => ({ transaction_id: s.transaction_id, category_id: s.category_id }));
      return applyAiCategories(items);
    },
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      // Also refresh the Review Queue (used there too): categorised rows leave the
      // uncategorised list and their review items resolve.
      qc.invalidateQueries({ queryKey: ["review"] });
      qc.invalidateQueries({ queryKey: ["uncategorised"] });
      setMsg(`Applied ${res.applied} categor${res.applied === 1 ? "y" : "ies"}.`);
      setSuggestions(null);
    },
    onError: (e) => setErr(String(e instanceof Error ? e.message : e)),
  });

  const toggle = (id: number) =>
    setPicked((p) => {
      const next = new Set(p);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const rows = suggestions ?? [];
  const selectedCount = rows.filter((s) => picked.has(s.transaction_id)).length;
  const allSelected = rows.length > 0 && selectedCount === rows.length;
  const someSelected = selectedCount > 0 && selectedCount < rows.length;

  // Select/clear every suggestion at once (indeterminate/none → select-all).
  const toggleAll = (checked: boolean) =>
    setPicked(checked ? new Set(rows.map((s) => s.transaction_id)) : new Set<number>());

  const exportCsv = () => {
    if (rows.length === 0) return;
    downloadCsvFile("ai-suggestions.csv", suggestionsToCsv(rows));
  };

  function applyThreshold(raw: number) {
    // Clamp to [0,1] (and guard NaN from a cleared field) — the min/max attrs
    // don't constrain manually typed values in every browser.
    const t = Number.isFinite(raw) ? Math.min(1, Math.max(0, raw)) : 0;
    setThreshold(t);
    if (suggestions) {
      setPicked(new Set(suggestions.filter((s) => (s.confidence ?? 0) >= t).map((s) => s.transaction_id)));
    }
  }

  return (
    <div className="card" style={{ borderLeft: "3px solid #6c5ce7" }}>
      <div className="page__head">
        <h2 className="card__title">✨ AI categorise uncategorised (local)</h2>
        <button type="button" className="link-btn" onClick={onClose}>close</button>
      </div>
      <p className="muted">
        Runs your local LLM over your transactions and proposes a category for each. Review the list,
        untick anything you disagree with, then apply — nothing is changed until you do. Tick{" "}
        <strong>re-check already-categorised</strong> to re-run after plugging in (or improving) a model
        and find new matches; manual categories are never touched.
      </p>

      <div className="form-row" style={{ gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <button type="button" className="btn" disabled={scan.isPending} onClick={() => scan.mutate()}>
          {scan.isPending ? "Scanning…" : "Scan now"}
        </button>
        <label className="checkbox">
          <input type="checkbox" checked={recheck} onChange={(e) => setRecheck(e.target.checked)} />{" "}
          Re-check already-categorised
        </label>
        <label className="muted">
          Auto-tick at ≥{" "}
          <input
            type="number" min="0" max="1" step="0.05" value={threshold}
            style={{ width: 70 }}
            onChange={(e) => applyThreshold(Number(e.target.value))}
          />{" "}confidence
        </label>
      </div>

      {err && <p className="status status--error">{err}</p>}
      {msg && <p className="muted">{msg}</p>}

      {suggestions && suggestions.length > 0 && (
        <>
          <div className="table-wrap" style={{ marginTop: 8 }}>
            <table className="table">
              <thead>
                <tr>
                  <th><SelectAllCheckbox allSelected={allSelected} someSelected={someSelected} onToggle={toggleAll} /></th>
                  <th>Description</th><th className="num">Amount</th><th>Suggested category</th><th className="num">Conf.</th>
                </tr>
              </thead>
              <tbody>
                {suggestions.map((s) => (
                  <tr key={s.transaction_id}>
                    <td><input type="checkbox" checked={picked.has(s.transaction_id)} onChange={() => toggle(s.transaction_id)} /></td>
                    <td title={s.rationale ?? ""}>
                      {s.description}
                      {s.already_ai_processed && (
                        <span className="tag" title="This transaction was AI-processed before">already AI'd</span>
                      )}
                    </td>
                    <td className="num">{money(s.amount, base)}</td>
                    <td>{s.category_name}</td>
                    <td className="num">{s.confidence == null ? "—" : `${Math.round(s.confidence * 100)}%`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="form-row" style={{ gap: 8, marginTop: 8, flexWrap: "wrap" }}>
            <button type="button" className="btn" disabled={picked.size === 0 || apply.isPending} onClick={() => apply.mutate()}>
              {apply.isPending ? "Applying…" : `Apply ${picked.size} selected`}
            </button>
            <button className="btn btn--ghost" type="button" onClick={exportCsv}>
              Export CSV
            </button>
          </div>
        </>
      )}
      {suggestions?.length === 0 && <p className="muted">No new suggestions — nothing to categorise, or the model proposed no changes.</p>}
    </div>
  );
}
