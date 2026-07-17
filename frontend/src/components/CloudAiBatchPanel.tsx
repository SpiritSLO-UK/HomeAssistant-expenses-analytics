import { useState, type Dispatch, type SetStateAction } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  applyAiCategories,
  cloudBatchPrepare,
  cloudBatchSend,
  type BatchSuggestion,
  type CloudBatchItem,
} from "../api/client";

/**
 * Batch AI categorise for **cloud** providers (backlog #154). Unlike the local
 * panel, sending leaves the device, so it's a deliberate two-step approval:
 *   1. Prepare — preview the *redacted* payload that would be sent for each
 *      uncategorised transaction; untick any you don't want to send.
 *   2. Send — approve the whole list at once; the cloud runs and returns
 *      suggestions, which you then review (pre-ticked by confidence) and apply.
 * Nothing leaves the device until you click "Send", and nothing is written until
 * you click "Apply".
 */
export default function CloudAiBatchPanel({ base, onClose }: Readonly<{ base: string; onClose: () => void }>) {
  const qc = useQueryClient();
  const [items, setItems] = useState<CloudBatchItem[] | null>(null);
  const [toSend, setToSend] = useState<Set<number>>(new Set());
  const [suggestions, setSuggestions] = useState<BatchSuggestion[] | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [threshold, setThreshold] = useState(0.8);
  const [recheck, setRecheck] = useState(false);
  const [showPayload, setShowPayload] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const prepare = useMutation({
    mutationFn: () => cloudBatchPrepare(50, recheck ? "recheck" : "uncategorised"),
    onSuccess: (res) => {
      setItems(res.items);
      // Default-untick rows already sent to AI before, so you don't re-send (and
      // re-pay/re-expose) them — re-tick any you do want.
      const fresh = res.items.filter((i) => !i.already_ai_processed);
      setToSend(new Set(fresh.map((i) => i.ai_request_id)));
      setSuggestions(null);
      const already = res.items.length - fresh.length;
      setMsg(
        `Found ${res.count} transaction(s)` +
          (already ? ` · ${already} already AI-processed (unticked)` : "") +
          ". Review what would be sent, then send.",
      );
      setErr(null);
    },
    onError: (e) => setErr(String(e instanceof Error ? e.message : e)),
  });

  const send = useMutation({
    mutationFn: () => {
      const all = (items ?? []).map((i) => i.ai_request_id);
      const approve = all.filter((id) => toSend.has(id));
      const reject = all.filter((id) => !toSend.has(id));
      return cloudBatchSend(approve, reject);
    },
    onSuccess: (res) => {
      setSuggestions(res.suggestions);
      setPicked(new Set(res.suggestions.filter((s) => (s.confidence ?? 0) >= threshold).map((s) => s.transaction_id)));
      setItems(null);
      const failed = res.failed.length ? ` · ${res.failed.length} failed` : "";
      setMsg(`Cloud returned ${res.count} suggestion(s)${failed}. Review and apply.`);
      setErr(null);
    },
    onError: (e) => setErr(String(e instanceof Error ? e.message : e)),
  });

  const apply = useMutation({
    mutationFn: () => {
      const chosen = (suggestions ?? [])
        .filter((s) => picked.has(s.transaction_id))
        .map((s) => ({ transaction_id: s.transaction_id, category_id: s.category_id }));
      return applyAiCategories(chosen);
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

  const toggle = (setter: Dispatch<SetStateAction<Set<number>>>, id: number) => {
    // Functional updater so rapid successive toggles each apply against the
    // latest state instead of a stale closure (which dropped fast updates).
    setter((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
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

  const scanLabel = recheck ? "Scan transactions" : "Scan uncategorised";

  return (
    <div className="card" style={{ borderLeft: "3px solid #e0a800" }}>
      <div className="page__head">
        <h2 className="card__title">☁️ AI categorise uncategorised (cloud)</h2>
        <button className="link-btn" onClick={onClose}>close</button>
      </div>
      <p className="muted">
        Sends a <strong>redacted</strong>, minimal payload per transaction to your configured cloud AI —
        only after you review the list and approve. Nothing leaves the device until you click{" "}
        <strong>Send to cloud</strong>, and no category is written until you click <strong>Apply</strong>.
        Every call is logged.
      </p>

      {!items && !suggestions && (
        <div className="form-row" style={{ gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <button className="btn" disabled={prepare.isPending} onClick={() => prepare.mutate()}>
            {prepare.isPending ? "Scanning…" : scanLabel}
          </button>
          <label className="checkbox">
            <input type="checkbox" checked={recheck} onChange={(e) => setRecheck(e.target.checked)} />{" "}
            Re-check already-categorised (skips manual)
          </label>
        </div>
      )}

      {err && <p className="status status--error">{err}</p>}
      {msg && <p className="muted">{msg}</p>}

      {/* Stage 1: review the redacted payloads that would be sent */}
      {items && items.length > 0 && (
        <>
          <div className="table-wrap" style={{ marginTop: 8 }}>
            <table className="table">
              <thead>
                <tr><th></th><th>Will send (redacted)</th><th className="num">Amount</th><th></th></tr>
              </thead>
              <tbody>
                {items.map((it) => (
                  <tr key={it.ai_request_id}>
                    <td>
                      <input
                        type="checkbox"
                        checked={toSend.has(it.ai_request_id)}
                        onChange={() => toggle(setToSend, it.ai_request_id)}
                      />
                    </td>
                    <td>
                      {it.description || <span className="muted">(empty)</span>}
                      {it.already_ai_processed && (
                        <span className="tag" title="Already sent to AI before — unticked so it isn't re-sent">
                          already AI'd
                        </span>
                      )}
                      {showPayload === it.ai_request_id && (
                        <pre style={{ whiteSpace: "pre-wrap", fontSize: "0.72rem", marginTop: 4, maxHeight: 240, overflow: "auto" }}>
                          {JSON.stringify(it.payload, null, 2)}
                        </pre>
                      )}
                    </td>
                    <td className="num">{it.amount} {it.currency}</td>
                    <td>
                      <button
                        className="link-btn"
                        onClick={() => setShowPayload(showPayload === it.ai_request_id ? null : it.ai_request_id)}
                      >
                        {showPayload === it.ai_request_id ? "hide" : "view payload"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button
            className="btn"
            style={{ marginTop: 8 }}
            disabled={toSend.size === 0 || send.isPending}
            onClick={() => send.mutate()}
          >
            {send.isPending ? "Sending…" : `Send ${toSend.size} to cloud →`}
          </button>
        </>
      )}
      {items?.length === 0 && <p className="muted">Nothing to send.</p>}

      {/* Stage 2: review the suggestions the cloud returned */}
      {suggestions && suggestions.length > 0 && (
        <>
          <div className="form-row" style={{ gap: 8, alignItems: "center", marginTop: 8 }}>
            <label className="muted">
              Auto-tick at ≥{" "}
              <input
                type="number" min="0" max="1" step="0.05" value={threshold}
                style={{ width: 70 }}
                onChange={(e) => applyThreshold(Number(e.target.value))}
              />{" "}confidence
            </label>
          </div>
          <div className="table-wrap" style={{ marginTop: 8 }}>
            <table className="table">
              <thead>
                <tr><th></th><th>Description</th><th className="num">Amount</th><th>Suggested category</th><th className="num">Conf.</th></tr>
              </thead>
              <tbody>
                {suggestions.map((s) => (
                  <tr key={s.transaction_id}>
                    <td><input type="checkbox" checked={picked.has(s.transaction_id)} onChange={() => toggle(setPicked, s.transaction_id)} /></td>
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
      {suggestions?.length === 0 && <p className="muted">The cloud returned no usable suggestions.</p>}
    </div>
  );
}
