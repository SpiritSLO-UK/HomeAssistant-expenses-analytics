import { useEffect, useState, type Dispatch, type SetStateAction } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  applyAiCategories,
  cloudBatchPrepare,
  cloudBatchSend,
  cloudBatchStatus,
  type BatchSuggestion,
  type CloudBatchItem,
} from "../api/client";
import { cloudItemsToCsv, downloadCsvFile, suggestionsToCsv } from "../lib/csv";

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
  // The ai_request_ids of the batch currently being sent in the background; while
  // set we poll cloud-batch/status for progress and stop once it reports `done`.
  const [batchIds, setBatchIds] = useState<number[] | null>(null);
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
      // Fire the non-blocking send; carry the approved ids through so onSuccess can
      // start polling their progress.
      return cloudBatchSend(approve, reject).then((ack) => ({ ack, approve }));
    },
    onSuccess: ({ ack, approve }) => {
      // Don't await the whole batch — begin polling status for the approved ids.
      setBatchIds(approve);
      setMsg(`Queued ${ack.queued} for cloud send — sending in the background…`);
      setErr(null);
    },
    onError: (e) => setErr(String(e instanceof Error ? e.message : e)),
  });

  // Poll batch progress while sending; refetch on an interval until `done`.
  const statusQuery = useQuery({
    queryKey: ["cloud-batch-status", batchIds],
    queryFn: () => cloudBatchStatus(batchIds ?? []),
    enabled: batchIds != null,
    refetchInterval: (query) => (query.state.data?.done ? false : 1200),
  });

  // When the background send finishes, move to the review stage using the
  // suggestions the status endpoint carries, and stop polling.
  const status = statusQuery.data;
  useEffect(() => {
    if (batchIds == null || !status?.done) return;
    setSuggestions(status.suggestions);
    setPicked(
      new Set(status.suggestions.filter((s) => (s.confidence ?? 0) >= threshold).map((s) => s.transaction_id)),
    );
    setItems(null);
    setBatchIds(null); // stop polling
    const failed = status.failed ? ` · ${status.failed} failed` : "";
    setMsg(`Cloud returned ${status.suggestions.length} suggestion(s)${failed}. Review and apply.`);
    setErr(null);
  }, [batchIds, status, threshold]);

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

  // Stage 1: export the redacted rows that would actually be sent (the ticked
  // ones) so the user can audit exactly what leaves the device.
  const exportWillSend = () => {
    const rows = (items ?? []).filter((it) => toSend.has(it.ai_request_id));
    if (rows.length === 0) return;
    downloadCsvFile("cloud-ai-will-send.csv", cloudItemsToCsv(rows));
  };

  // Stage 2: export the returned suggestions — same columns as the local panel.
  const exportSuggestions = () => {
    const rows = suggestions ?? [];
    if (rows.length === 0) return;
    downloadCsvFile("ai-suggestions.csv", suggestionsToCsv(rows));
  };

  const scanLabel = recheck ? "Scan transactions" : "Scan uncategorised";

  // A background send is in flight while we have batch ids to poll.
  const sending = batchIds != null;
  const failedSuffix = status?.failed ? ` · ${status.failed} failed` : "";
  const progressLabel = status ? `Sent ${status.sent} / ${status.total}${failedSuffix}…` : "Starting send…";

  return (
    <div className="card" style={{ borderLeft: "3px solid #e0a800" }}>
      <div className="page__head">
        <h2 className="card__title">☁️ AI categorise uncategorised (cloud)</h2>
        <button type="button" className="link-btn" onClick={onClose}>close</button>
      </div>
      <p className="muted">
        Sends a <strong>redacted</strong>, minimal payload per transaction to your configured cloud AI —
        only after you review the list and approve. Nothing leaves the device until you click{" "}
        <strong>Send to cloud</strong>, and no category is written until you click <strong>Apply</strong>.
        Every call is logged.
      </p>

      {!items && !suggestions && (
        <div className="form-row" style={{ gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <button type="button" className="btn" disabled={prepare.isPending} onClick={() => prepare.mutate()}>
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
                        type="button"
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
          <div className="form-row" style={{ gap: 8, marginTop: 8, flexWrap: "wrap" }}>
            <button
              type="button"
              className="btn"
              disabled={toSend.size === 0 || send.isPending || sending}
              onClick={() => send.mutate()}
            >
              {sending ? "Sending…" : `Send ${toSend.size} to cloud →`}
            </button>
            <button
              className="btn btn--ghost"
              type="button"
              disabled={toSend.size === 0 || sending}
              onClick={exportWillSend}
            >
              Export CSV
            </button>
          </div>
          {sending && (
            <p className="status" aria-live="polite" style={{ marginTop: 8 }}>
              {progressLabel}
            </p>
          )}
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
          <div className="form-row" style={{ gap: 8, marginTop: 8, flexWrap: "wrap" }}>
            <button type="button" className="btn" disabled={picked.size === 0 || apply.isPending} onClick={() => apply.mutate()}>
              {apply.isPending ? "Applying…" : `Apply ${picked.size} selected`}
            </button>
            <button className="btn btn--ghost" type="button" onClick={exportSuggestions}>
              Export CSV
            </button>
          </div>
        </>
      )}
      {suggestions?.length === 0 && <p className="muted">The cloud returned no usable suggestions.</p>}
    </div>
  );
}
