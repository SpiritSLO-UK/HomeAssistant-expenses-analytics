import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import AiImageWarningDialog from "../components/AiImageWarningDialog";
import CameraCaptureButton from "../components/CameraCaptureButton";
import { isImageAiWarningDismissed, setImageAiWarningDismissed } from "../prefs";
import {
  aiExtractImport,
  confirmImport,
  getAiStatus,
  listParsers,
  uploadImport,
  uploadReceipt,
  type ConfirmResponse,
  type UploadResponse,
} from "../api/client";

export default function Import() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [parserId, setParserId] = useState<string>("");
  const [preview, setPreview] = useState<UploadResponse | null>(null);
  const [confirmed, setConfirmed] = useState<ConfirmResponse | null>(null);
  const [showAiWarn, setShowAiWarn] = useState(false);

  const { data: parsers } = useQuery({ queryKey: ["parsers"], queryFn: listParsers });
  const aiStatus = useQuery({ queryKey: ["ai-status"], queryFn: getAiStatus });

  const upload = useMutation({
    mutationFn: () => uploadImport(file!, parserId || undefined),
    onSuccess: (data) => {
      setPreview(data);
      setConfirmed(null);
    },
  });

  // Opt-in vision-AI fallback when a photo/scan couldn't be read by OCR.
  const aiExtract = useMutation({
    mutationFn: () => aiExtractImport(file!),
    onSuccess: (data) => {
      setPreview(data);
      setConfirmed(null);
    },
  });

  function tryAiExtract() {
    if (isImageAiWarningDismissed()) aiExtract.mutate();
    else setShowAiWarn(true);
  }

  // If a statement-image import finds no transactions, it's probably a receipt —
  // one click routes the same file into the receipts flow (no re-upload by hand).
  const addAsReceipt = useMutation({
    mutationFn: () => uploadReceipt(file!),
    onSuccess: (r) => {
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      navigate(`/receipts?focus=${r.id}`);
    },
  });

  const confirm = useMutation({
    mutationFn: () => confirmImport(preview!.import_id),
    onSuccess: (data) => {
      setConfirmed(data);
      queryClient.invalidateQueries({ queryKey: ["transactions"] });
    },
  });

  function reset() {
    setFile(null);
    setParserId("");
    setPreview(null);
    setConfirmed(null);
    upload.reset();
    confirm.reset();
    if (fileInput.current) fileInput.current.value = "";
  }

  // A statement-image that yielded no rows is almost always a receipt — surface
  // the receipt/AI recovery path as the primary message instead of the raw error.
  const isReceiptish =
    upload.isError && !!file && /image|No transactions recognised/i.test(String(upload.error));

  return (
    <div className="page">
      <h1 className="page__title">Import</h1>
      <p className="muted" style={{ marginTop: -4 }}>
        Two kinds of upload: a <strong>receipt</strong> (one purchase — read &amp; matched
        automatically) or a <strong>bank statement</strong> (many transactions — preview &amp; confirm).
      </p>

      <ReceiptImportPanel />

      <div className="card">
        <h2 className="card__title">Import a bank statement (CSV, PDF, or a photo/scan)</h2>
        <p className="muted">
          CSV is most reliable. PDF, and now <strong>photos or scans</strong> (JPG/PNG, or a
          scanned PDF), are read best-effort with OCR — extracted rows are{" "}
          <strong>flagged for review</strong> so you can verify them on the Transactions page.
        </p>
        <div className="form-row">
          <input
            ref={fileInput}
            type="file"
            // Broad accept so mobile file pickers don't grey out a bank CSV: phones
            // match by the OS-reported MIME, and a downloaded CSV is often
            // application/octet-stream / vnd.ms-excel / text/plain rather than
            // text/csv. List the realistic CSV aliases (+ octet-stream) alongside
            // PDF/images; the backend validates the actual content on upload.
            accept=".csv,.tsv,.txt,text/csv,text/plain,text/comma-separated-values,application/csv,application/vnd.ms-excel,application/octet-stream,.pdf,application/pdf,image/*"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setPreview(null);
              setConfirmed(null);
            }}
          />
          <CameraCaptureButton
            label="📷 Take photo"
            onCapture={(f) => { setFile(f); setPreview(null); setConfirmed(null); }}
          />
          <select value={parserId} onChange={(e) => setParserId(e.target.value)}>
            <option value="">Auto-detect</option>
            {parsers?.map((p) => (
              <option key={p.parser_id} value={p.parser_id}>
                {p.institution}
              </option>
            ))}
          </select>
          <button
            className="btn"
            disabled={!file || upload.isPending}
            onClick={() => upload.mutate()}
          >
            {upload.isPending ? "Uploading…" : "Preview"}
          </button>
          {(preview || confirmed) && (
            <button className="btn btn--ghost" onClick={reset}>
              Start over
            </button>
          )}
        </div>
        {file && !preview && !confirmed && (
          <p className="muted" style={{ marginTop: 6, fontSize: "0.85rem" }}>Selected: {file.name}</p>
        )}
        {upload.isError && !isReceiptish && (
          <p className="status status--error">{String(upload.error)}</p>
        )}
        {isReceiptish && (
          <div className="card" style={{ marginTop: 8, padding: 12 }}>
            <p className="status status--warn" style={{ marginTop: 0 }}>
              We couldn't read this image as a bank statement — no transactions were recognised.
            </p>
            <p className="muted" style={{ marginTop: 0, fontSize: "0.85rem" }}>
              If it's a <strong>receipt</strong>, add it to Receipts in one click — or have AI try to
              read it. (For statements, a <strong>CSV export</strong> is the most reliable.)
            </p>
            <div className="form-row" style={{ gap: 8, flexWrap: "wrap" }}>
              <button className="btn" disabled={addAsReceipt.isPending} onClick={() => addAsReceipt.mutate()}>
                {addAsReceipt.isPending ? "Adding…" : "🧾 Add as a receipt instead"}
              </button>
              {aiStatus.data?.enabled && (
                <button className="btn btn--ghost" disabled={aiExtract.isPending} onClick={tryAiExtract}>
                  {aiExtract.isPending ? "Asking AI…" : "✨ Extract with AI"}
                </button>
              )}
              <Link className="btn btn--ghost" to="/receipts">Open Receipts page →</Link>
            </div>
          </div>
        )}
        {aiExtract.isError && <p className="status status--error">{String(aiExtract.error)}</p>}
        {addAsReceipt.isError && <p className="status status--error">{String(addAsReceipt.error)}</p>}
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
      </div>

      {preview && !confirmed && (
        <div className="card">
          <h2 className="card__title">
            Preview — detected <code>{preview.detected_parser}</code> ({preview.institution})
          </h2>
          <ImportReportBar report={preview.report} />
          {preview.warnings.map((w) => (
            <p key={w} className="status status--warn">
              ⚠ {w}
            </p>
          ))}
          <PreviewTable rows={preview.preview} />
          <div className="form-row">
            <button
              className="btn"
              disabled={confirm.isPending || preview.report.new === 0}
              onClick={() => confirm.mutate()}
            >
              {confirm.isPending
                ? "Importing…"
                : `Confirm import (${preview.report.new} new)`}
            </button>
            {preview.report.new === 0 && (
              <span className="muted">Nothing new to import (all duplicates).</span>
            )}
          </div>
          {confirm.isError && <p className="status status--error">{String(confirm.error)}</p>}
        </div>
      )}

      {confirmed && (
        <div className="card">
          <h2 className="card__title">Import complete ✅</h2>
          <ImportReportBar report={confirmed.report} />
          <p className="muted">
            Imported {confirmed.report.new} transactions ({confirmed.report.duplicates}{" "}
            duplicates skipped).{" "}
            <Link to="/transactions">View transactions →</Link>
          </p>
        </div>
      )}
    </div>
  );
}

function ImportReportBar({ report }: Readonly<{ report: { rows_detected: number; new: number; duplicates: number; errors: number } }>) {
  return (
    <div className="report">
      <span className="report__chip">Rows: {report.rows_detected}</span>
      <span className="report__chip report__chip--ok">New: {report.new}</span>
      <span className="report__chip report__chip--dup">Duplicates: {report.duplicates}</span>
      {report.errors > 0 && (
        <span className="report__chip report__chip--err">Errors: {report.errors}</span>
      )}
    </div>
  );
}

// Receipts-first panel (user ask): a proactive receipt uploader at the TOP of the
// Import page so you choose receipt vs statement up front, rather than discovering
// a statement-image was really a receipt only after it fails. Reuses the receipts
// flow and lands on the Receipts page (focused on the new one) to review/match.
function ReceiptImportPanel() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const fileRef = useRef<HTMLInputElement>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const upload = useMutation({
    mutationFn: (f: File) => uploadReceipt(f),
    onSuccess: (r) => {
      queryClient.invalidateQueries({ queryKey: ["receipts"] });
      queryClient.invalidateQueries({ queryKey: ["review"] });
      navigate(`/receipts?focus=${r.id}`);
    },
    onError: (e) => setMsg(String(e instanceof Error ? e.message : e)),
  });
  const send = (f: File) => { setMsg(null); upload.mutate(f); };

  return (
    <div className="card">
      <h2 className="card__title">🧾 Add a receipt</h2>
      <p className="muted">
        A photo or PDF of a single <strong>receipt</strong> (not a bank statement). It's read with OCR
        and matched to a transaction automatically — you'll land on the Receipts page to review it.
      </p>
      <input
        ref={fileRef}
        type="file"
        accept="image/*,application/pdf"
        style={{ display: "none" }}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) send(f);
          if (fileRef.current) fileRef.current.value = "";
        }}
      />
      <div className="form-row" style={{ gap: 8, flexWrap: "wrap" }}>
        <button className="btn" disabled={upload.isPending} onClick={() => fileRef.current?.click()}>
          {upload.isPending ? "Uploading…" : "🧾 Upload receipt"}
        </button>
        <CameraCaptureButton onCapture={send} disabled={upload.isPending} />
        <Link className="btn btn--ghost" to="/receipts">Open Receipts page →</Link>
      </div>
      {msg && <p className="status status--error">{msg}</p>}
    </div>
  );
}

function PreviewTable({ rows }: Readonly<{ rows: UploadResponse["preview"] }>) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Date</th>
            <th>Description</th>
            <th className="num">Amount</th>
            <th>Cur</th>
            <th>Hint</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className={r.is_duplicate ? "row--dup" : ""}>
              <td>{r.transaction_date}</td>
              <td>{r.description_raw}</td>
              <td className={"num " + (r.direction === "credit" ? "amt--pos" : "amt--neg")}>
                {r.amount}
              </td>
              <td>{r.currency}</td>
              <td className="muted">{r.category_hint ?? ""}</td>
              <td>{r.is_duplicate ? <span className="tag tag--dup">dup</span> : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
