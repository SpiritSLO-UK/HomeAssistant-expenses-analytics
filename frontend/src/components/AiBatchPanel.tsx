import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { applyAiCategories, classifyBatch, type BatchSuggestion } from "../api/client";

/**
 * Batch "AI categorise uncategorised" (local LLM only). Scans, shows each
 * suggestion with a checkbox (pre-ticked above the confidence threshold), and
 * applies only what the user keeps ticked — AI never auto-applies silently.
 */
export default function AiBatchPanel({ base, onClose }: Readonly<{ base: string; onClose: () => void }>) {
  const qc = useQueryClient();
  const [threshold, setThreshold] = useState(0.8);
  const [suggestions, setSuggestions] = useState<BatchSuggestion[] | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const scan = useMutation({
    mutationFn: () => classifyBatch(50),
    onSuccess: (res) => {
      setSuggestions(res.suggestions);
      setPicked(new Set(res.suggestions.filter((s) => (s.confidence ?? 0) >= threshold).map((s) => s.transaction_id)));
      setMsg(`Scanned ${res.considered} uncategorised · ${res.count} suggestion(s).`);
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
      setMsg(`Applied ${res.applied} categor${res.applied === 1 ? "y" : "ies"}.`);
      setSuggestions(null);
    },
    onError: (e) => setErr(String(e instanceof Error ? e.message : e)),
  });

  const toggle = (id: number) =>
    setPicked((p) => {
      const next = new Set(p);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  function applyThreshold(t: number) {
    setThreshold(t);
    if (suggestions) {
      setPicked(new Set(suggestions.filter((s) => (s.confidence ?? 0) >= t).map((s) => s.transaction_id)));
    }
  }

  return (
    <div className="card" style={{ borderLeft: "3px solid #6c5ce7" }}>
      <div className="page__head">
        <h2 className="card__title">✨ AI categorise uncategorised (local)</h2>
        <button className="link-btn" onClick={onClose}>close</button>
      </div>
      <p className="muted">
        Runs your local LLM over uncategorised transactions and proposes a category for each.
        Review the list, untick anything you disagree with, then apply — nothing is changed until you do.
      </p>

      <div className="form-row" style={{ gap: 8, alignItems: "center" }}>
        <button className="btn" disabled={scan.isPending} onClick={() => scan.mutate()}>
          {scan.isPending ? "Scanning…" : "Scan now"}
        </button>
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
                <tr><th></th><th>Description</th><th className="num">Amount</th><th>Suggested category</th><th className="num">Conf.</th></tr>
              </thead>
              <tbody>
                {suggestions.map((s) => (
                  <tr key={s.transaction_id}>
                    <td><input type="checkbox" checked={picked.has(s.transaction_id)} onChange={() => toggle(s.transaction_id)} /></td>
                    <td title={s.rationale ?? ""}>{s.description}</td>
                    <td className="num">{s.amount} {base}</td>
                    <td>{s.category_name}</td>
                    <td className="num">{s.confidence == null ? "—" : `${Math.round(s.confidence * 100)}%`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button className="btn" style={{ marginTop: 8 }} disabled={picked.size === 0 || apply.isPending} onClick={() => apply.mutate()}>
            {apply.isPending ? "Applying…" : `Apply ${picked.size} selected`}
          </button>
        </>
      )}
      {suggestions?.length === 0 && <p className="muted">No suggestions — nothing uncategorised, or the model returned no matches.</p>}
    </div>
  );
}
