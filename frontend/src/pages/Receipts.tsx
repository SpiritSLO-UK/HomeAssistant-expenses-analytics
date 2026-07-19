import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import AiImageWarningDialog from "../components/AiImageWarningDialog";
import CameraCaptureButton from "../components/CameraCaptureButton";
import ReceiptPreview from "../components/ReceiptPreview";
import { useConfirm } from "../components/dialogs";
import { useServerState } from "../lib/useServerState";
import { formatDate, useDateFormat } from "../lib/date";
import { isImageAiWarningDismissed, setImageAiWarningDismissed } from "../prefs";
import {
  aiExtractReceipt,
  confirmReceiptMatch,
  createTransactionFromReceipt,
  deleteReceipt,
  getAiStatus,
  getOcrStatus,
  getPaperlessStatus,
  importPaperlessDocument,
  listAccounts,
  listPaperlessDocuments,
  listReceipts,
  matchReceipt,
  receiptFileUrl,
  updateReceipt,
  uploadReceipt,
  type MatchResult,
  type Receipt,
} from "../api/client";

export default function Receipts() {
  const qc = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  // Deep-link target from the Review Queue's "Open receipt →" — highlight + scroll to it.
  const [params] = useSearchParams();
  const focusId = Number(params.get("focus")) || null;

  const ocr = useQuery({ queryKey: ["ocr-status"], queryFn: getOcrStatus });
  const receipts = useQuery({ queryKey: ["receipts"], queryFn: listReceipts });

  const upload = useMutation({
    mutationFn: (f: File) => uploadReceipt(f),
    onSuccess: (r) => {
      setErr(null);
      setMsg(
        r.already_imported
          ? "That receipt was already imported — showing the existing one (re-uploading an identical file changes nothing)."
          : "Receipt uploaded.",
      );
      qc.invalidateQueries({ queryKey: ["receipts"] });
      qc.invalidateQueries({ queryKey: ["review"] });
    },
    onError: (e) => { setMsg(null); setErr(String(e)); },
  });

  return (
    <div className="page">
      <h1 className="page__title">Receipts</h1>
      {err && <p className="status status--error">{err}</p>}
      {msg && <p className="status status--ok">{msg}</p>}

      <div className="card">
        <h2 className="card__title">Upload a receipt</h2>
        <p className="muted">
          Upload a photo or PDF. {ocr.data?.available
            ? "Local OCR will try to read the merchant, date and total — check and correct them, then match to a transaction."
            : "Local OCR isn't available here, so enter the merchant, date and total yourself, then match to a transaction. (OCR runs in the Home Assistant add-on.)"}
        </p>
        <input
          ref={fileInput}
          type="file"
          accept="image/*,application/pdf"
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) upload.mutate(f);
            if (fileInput.current) fileInput.current.value = "";
          }}
        />
        <div className="form-row" style={{ gap: 8, flexWrap: "wrap" }}>
          <button className="btn" disabled={upload.isPending} onClick={() => fileInput.current?.click()}>
            {upload.isPending ? "Uploading…" : "⬆ Upload receipt"}
          </button>
          <CameraCaptureButton onCapture={(f) => upload.mutate(f)} disabled={upload.isPending} />
        </div>
      </div>

      <PaperlessCard onError={(e) => setErr(String(e))} />

      <div className="card">
        <h2 className="card__title">Your receipts</h2>
        {receipts.isLoading && <p className="muted">Loading…</p>}
        {receipts.data?.length === 0 && (
          <p className="muted">No receipts yet. Upload one above.</p>
        )}
        <div>
          {receipts.data?.map((r) => <ReceiptCard key={r.id} r={r} onError={setErr} focused={r.id === focusId} />)}
        </div>
      </div>
    </div>
  );
}

function PaperlessCard({ onError }: Readonly<{ onError: (e: unknown) => void }>) {
  const qc = useQueryClient();
  const dateFmt = useDateFormat();
  const status = useQuery({ queryKey: ["paperless-status"], queryFn: getPaperlessStatus });
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState<string | null>(null);
  // Bump on every submit so re-searching the *same* text still refetches — the query
  // key changes, so React Query re-runs even when `submitted` is unchanged.
  const [nonce, setNonce] = useState(0);
  const [msg, setMsg] = useState<string | null>(null);
  const configured = status.data?.configured ?? false;

  const docs = useQuery({
    queryKey: ["paperless-docs", submitted, nonce],
    queryFn: () => listPaperlessDocuments(submitted || undefined, 25),
    enabled: configured && submitted !== null,
  });

  const importDoc = useMutation({
    mutationFn: (id: number) => importPaperlessDocument(id),
    onSuccess: (r) => {
      setMsg(r.created ? `Imported "${r.filename}" — see it below.` : `"${r.filename}" was already imported.`);
      qc.invalidateQueries({ queryKey: ["receipts"] });
      qc.invalidateQueries({ queryKey: ["review"] });
    },
    onError,
  });

  // Show this card only when Paperless is configured (#67/#68). Until then there's
  // nothing to import here — setup guidance lives in Settings → Integrations.
  if (!configured) return null;

  return (
    <div className="card">
      <h2 className="card__title">Import from Paperless</h2>
      <p className="muted" style={{ marginTop: 0 }}>
        Connected to <code>{status.data?.url}</code>. Search your documents and import one as a receipt.
      </p>
      <form className="form-row" onSubmit={(e) => { e.preventDefault(); setSubmitted(query); setNonce((n) => n + 1); }}>
        <input
          placeholder="Search (blank = most recent)"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ flex: 1, minWidth: 160 }}
        />
        <button className="btn btn--sm" type="submit">{docs.isFetching ? "Searching…" : "Browse"}</button>
      </form>
      {msg && <p className="status status--ok" style={{ marginTop: 8 }}>{msg}</p>}
      {submitted !== null && docs.data?.length === 0 && (
        <p className="muted">No documents found.</p>
      )}
      {docs.data && docs.data.length > 0 && (
        <ul className="kv" style={{ marginTop: 8 }}>
          {docs.data.map((d) => (
            <li key={d.id}>
              <span>{d.title}{d.created && <span className="muted"> · {formatDate(d.created, dateFmt)}</span>}</span>
              <button
                className="btn btn--sm btn--ghost"
                disabled={importDoc.isPending}
                onClick={() => importDoc.mutate(d.id)}
              >
                Import
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// `matched_by` stores *how* the match was made, not a person — show it plainly.
// (Previously the raw "user" leaked into the UI and read like a username.)
function matchedByLabel(by: string): string {
  if (by === "user") return "matched by you";
  if (by === "local_ocr") return "auto-matched";
  return by;
}

function ReceiptCard({ r, onError, focused = false }: Readonly<{ r: Receipt; onError: (e: string) => void; focused?: boolean }>) {
  const qc = useQueryClient();
  const confirmDialog = useConfirm();
  const dateFmt = useDateFormat();
  // Re-sync from the server value when the receipts query refetches (e.g. after OCR
  // finishes, merchant/date/total arrive) — without clobbering an in-progress edit.
  const [merchant, setMerchant] = useServerState(r.merchant_raw ?? "");
  const [date, setDate] = useServerState(r.receipt_date ?? "");
  const [total, setTotal] = useServerState(r.total_amount ?? "");
  const [result, setResult] = useState<MatchResult | null>(null);
  const cardRef = useRef<HTMLDivElement>(null);

  // When linked to from the Review Queue, scroll this receipt into view once.
  useEffect(() => {
    if (focused) cardRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [focused]);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["receipts"] });
    qc.invalidateQueries({ queryKey: ["review"] });
  };

  const save = useMutation({
    mutationFn: () => updateReceipt(r.id, {
      merchant_raw: merchant || null,
      receipt_date: date || null,
      total_amount: total || null,
    }),
    onSuccess: invalidate,
    onError: (e) => onError(String(e)),
  });
  const match = useMutation({
    mutationFn: () => matchReceipt(r.id),
    onSuccess: (res) => { setResult(res); invalidate(); },
    onError: (e) => onError(String(e)),
  });
  const confirm = useMutation({
    mutationFn: (txnId: number) => confirmReceiptMatch(r.id, txnId),
    onSuccess: () => { setResult(null); invalidate(); },
    onError: (e) => onError(String(e)),
  });
  const remove = useMutation({
    mutationFn: () => deleteReceipt(r.id),
    onSuccess: invalidate,
    onError: (e) => onError(String(e)),
  });

  // Create a transaction from this receipt (cash / un-imported purchases). Pick an
  // existing account, or create/use a dedicated "Cash & receipts" account ("new").
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: listAccounts });
  const [acct, setAcct] = useState("new");
  const createTxn = useMutation({
    mutationFn: () =>
      createTransactionFromReceipt(r.id, acct === "new" ? { new_account: true } : { account_id: Number(acct) }),
    onSuccess: () => {
      setResult(null);
      invalidate();
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["accounts"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
    onError: (e) => onError(String(e)),
  });

  // Opt-in vision-AI fallback: read merchant/date/total from the image.
  const aiStatus = useQuery({ queryKey: ["ai-status"], queryFn: getAiStatus });
  const [showAiWarn, setShowAiWarn] = useState(false);
  const aiExtract = useMutation({
    mutationFn: () => aiExtractReceipt(r.id),
    onSuccess: (updated) => {
      setMerchant(updated.merchant_raw ?? "");
      setDate(updated.receipt_date ?? "");
      setTotal(updated.total_amount ?? "");
      invalidate();
    },
    onError: (e) => onError(String(e)),
  });
  const tryAiExtract = () => {
    if (isImageAiWarningDismissed()) aiExtract.mutate();
    else setShowAiWarn(true);
  };
  const [preview, setPreview] = useState(false);

  const confirmed = r.matches.find((m) => m.match_status === "confirmed" || m.match_status === "auto_confirmed");
  const suggested = r.matches.find((m) => m.match_status === "suggested");
  const rec = r.recommended_transaction;
  let addLabel = rec ? "Add transaction" : "Create transaction";
  if (createTxn.isPending) addLabel = "Adding…";

  return (
    <div
      ref={cardRef}
      style={{
        padding: "12px",
        margin: "0 -12px",
        borderBottom: "1px solid rgba(127,127,127,0.2)",
        borderRadius: focused ? 8 : 0,
        outline: focused ? "2px solid var(--accent, #4a90d9)" : "none",
        background: focused ? "rgba(74,144,217,0.08)" : "transparent",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
        <strong>{r.source_filename ?? `Receipt #${r.id}`}</strong>
        <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {r.has_file && (
            <button
              className="link-btn"
              title="Preview the uploaded image/PDF in a popup"
              onClick={() => setPreview(true)}
            >
              View original
            </button>
          )}
          <span className="tag">{r.ocr_status}</span>
          {r.needs_review && <span className="tag tag--dup">review</span>}
          {confirmed && <span className="tag" style={{ background: "#3a9b5c", color: "#fff" }}>matched</span>}
        </span>
      </div>

      <div className="form-row" style={{ flexWrap: "wrap", gap: 8, marginTop: 8 }}>
        <input placeholder="Merchant" value={merchant} onChange={(e) => setMerchant(e.target.value)} style={{ minWidth: 160 }} />
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        <input placeholder="Total" type="number" step="0.01" min="0" value={total} onChange={(e) => setTotal(e.target.value)} style={{ width: 110 }} />
        <button className="btn btn--ghost" disabled={save.isPending} onClick={() => save.mutate()}>Save</button>
        <button className="btn" disabled={!total || match.isPending} onClick={() => match.mutate()}>
          {match.isPending ? "Matching…" : "Find match"}
        </button>
        {aiStatus.data?.enabled && (
          <button
            className="btn btn--ghost"
            disabled={aiExtract.isPending}
            title="Read the merchant, date and total from the image with AI"
            onClick={tryAiExtract}
          >
            {aiExtract.isPending ? "Asking AI…" : "✨ Extract with AI"}
          </button>
        )}
        <button className="link-btn" onClick={async () => { if (await confirmDialog({ message: "Delete this receipt?", confirmLabel: "Delete", danger: true })) remove.mutate(); }}>delete</button>
      </div>

      {showAiWarn && (
        <AiImageWarningDialog
          provider={aiStatus.data?.base_url}
          onConfirm={(dontWarn) => {
            if (dontWarn) setImageAiWarningDismissed();
            setShowAiWarn(false);
            aiExtract.mutate();
          }}
          onCancel={() => setShowAiWarn(false)}
        />
      )}

      {preview && r.has_file && (
        <ReceiptPreview
          url={receiptFileUrl(r.id)}
          filename={r.source_filename}
          onClose={() => setPreview(false)}
        />
      )}

      {confirmed && (
        <p className="muted" style={{ marginTop: 6 }}>
          ✓ Matched to transaction #{confirmed.transaction_id}
          {confirmed.matched_by ? ` (${matchedByLabel(confirmed.matched_by)})` : ""}.
        </p>
      )}

      {result && (
        <div style={{ marginTop: 8 }}>
          {result.candidates.length === 0 && <p className="muted">No candidate transactions found.</p>}
          {result.candidates.length > 0 && (
            <table className="table">
              <thead><tr><th>Date</th><th>Description</th><th className="num">Amount</th><th className="num">Score</th><th></th></tr></thead>
              <tbody>
                {result.candidates.map((c) => (
                  <tr key={c.transaction_id}>
                    <td>{formatDate(c.transaction_date, dateFmt)}</td>
                    <td>{c.description}</td>
                    <td className="num">{c.amount}</td>
                    <td className="num" title={JSON.stringify(c.breakdown)}>{c.score}</td>
                    <td><button className="btn btn--ghost" disabled={confirm.isPending} onClick={() => confirm.mutate(c.transaction_id)}>Confirm</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
      {suggested && !result && (
        <p className="muted" style={{ marginTop: 6 }}>
          Suggested match: transaction #{suggested.transaction_id} (score {suggested.match_score}). Click <em>Find match</em> to review.
        </p>
      )}

      {/* No matching transaction (e.g. cash, or the statement isn't imported)?
          Recommend adding one straight from the receipt — pre-filled, one click. */}
      {!confirmed && (
        <div
          className="card"
          style={{ marginTop: 8, padding: 10, background: "var(--surface)" }}
        >
          {rec ? (
            <p className="muted" style={{ marginTop: 0 }}>
              💡 <strong>No matching transaction.</strong> Add this one?{" "}
              <strong>{rec.merchant}</strong> · {formatDate(rec.transaction_date, dateFmt)} ·{" "}
              <span className="amt--neg">{rec.amount} {rec.currency}</span>
              {rec.category_name ? <> · {rec.category_name}</> : null}
            </p>
          ) : (
            <p className="muted" style={{ marginTop: 0 }}>
              No matching transaction? {total ? "Create one:" : "Set the total above, then create one."}
            </p>
          )}
          <div className="form-row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <select value={acct} onChange={(e) => setAcct(e.target.value)} disabled={createTxn.isPending} aria-label="Account for the new transaction">
              <option value="new">➕ New “Cash &amp; receipts” account</option>
              {accounts.data?.map((a) => <option key={a.id} value={String(a.id)}>{a.name}</option>)}
            </select>
            <button
              className={rec ? "btn" : "btn btn--ghost"}
              disabled={!total || createTxn.isPending}
              title={total ? "Create a transaction from this receipt's merchant, date and total" : "Set the total first"}
              onClick={() => createTxn.mutate()}
            >
              {addLabel}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
