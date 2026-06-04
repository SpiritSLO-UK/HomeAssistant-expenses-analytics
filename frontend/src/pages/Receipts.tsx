import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  confirmReceiptMatch,
  deleteReceipt,
  getOcrStatus,
  getPaperlessStatus,
  importPaperlessDocument,
  listPaperlessDocuments,
  listReceipts,
  matchReceipt,
  updateReceipt,
  uploadReceipt,
  type MatchResult,
  type Receipt,
} from "../api/client";

export default function Receipts() {
  const qc = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [err, setErr] = useState<string | null>(null);

  const ocr = useQuery({ queryKey: ["ocr-status"], queryFn: getOcrStatus });
  const receipts = useQuery({ queryKey: ["receipts"], queryFn: listReceipts });

  const upload = useMutation({
    mutationFn: (f: File) => uploadReceipt(f),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["receipts"] });
      qc.invalidateQueries({ queryKey: ["review"] });
    },
    onError: (e) => setErr(String(e)),
  });

  return (
    <div className="page">
      <h1 className="page__title">Receipts</h1>
      {err && <p className="status status--error">{err}</p>}

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
        <button className="btn" disabled={upload.isPending} onClick={() => fileInput.current?.click()}>
          {upload.isPending ? "Uploading…" : "⬆ Upload receipt"}
        </button>
      </div>

      <PaperlessCard onError={(e) => setErr(String(e))} />

      <div className="card">
        <h2 className="card__title">Your receipts</h2>
        {receipts.isLoading && <p className="muted">Loading…</p>}
        {receipts.data?.length === 0 && (
          <p className="muted">No receipts yet. Upload one above.</p>
        )}
        <div>
          {receipts.data?.map((r) => <ReceiptCard key={r.id} r={r} onError={setErr} />)}
        </div>
      </div>
    </div>
  );
}

function PaperlessCard({ onError }: Readonly<{ onError: (e: unknown) => void }>) {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["paperless-status"], queryFn: getPaperlessStatus });
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const configured = status.data?.configured ?? false;

  const docs = useQuery({
    queryKey: ["paperless-docs", submitted],
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

  if (status.isLoading) return null;

  return (
    <div className="card">
      <h2 className="card__title">Import from Paperless</h2>
      {configured ? (
        <>
          <p className="muted" style={{ marginTop: 0 }}>
            Connected to <code>{status.data?.url}</code>. Search your documents and import one as a receipt.
          </p>
          <form className="form-row" onSubmit={(e) => { e.preventDefault(); setSubmitted(query); }}>
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
                  <span>{d.title}{d.created && <span className="muted"> · {d.created.slice(0, 10)}</span>}</span>
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
        </>
      ) : (
        <p className="muted">
          Pull documents from your <strong>Paperless-ngx</strong> instance into receipts. It's
          one-directional — we only ever request from Paperless, never the other way. To enable,
          set <code>HAFI_PAPERLESS_URL</code> and <code>HAFI_PAPERLESS_TOKEN</code> in the add-on /
          environment and restart.
        </p>
      )}
    </div>
  );
}

function ReceiptCard({ r, onError }: Readonly<{ r: Receipt; onError: (e: string) => void }>) {
  const qc = useQueryClient();
  const [merchant, setMerchant] = useState(r.merchant_raw ?? "");
  const [date, setDate] = useState(r.receipt_date ?? "");
  const [total, setTotal] = useState(r.total_amount ?? "");
  const [result, setResult] = useState<MatchResult | null>(null);

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

  const confirmed = r.matches.find((m) => m.match_status === "confirmed" || m.match_status === "auto_confirmed");
  const suggested = r.matches.find((m) => m.match_status === "suggested");

  return (
    <div style={{ padding: "12px 0", borderBottom: "1px solid rgba(127,127,127,0.2)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
        <strong>{r.source_filename ?? `Receipt #${r.id}`}</strong>
        <span>
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
        <button className="link-btn" onClick={() => { if (globalThis.confirm("Delete this receipt?")) remove.mutate(); }}>delete</button>
      </div>

      {confirmed && (
        <p className="muted" style={{ marginTop: 6 }}>
          ✓ Matched to transaction #{confirmed.transaction_id}
          {confirmed.matched_by ? ` (${confirmed.matched_by})` : ""}.
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
                    <td>{c.transaction_date}</td>
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
    </div>
  );
}
