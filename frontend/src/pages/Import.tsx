import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  confirmImport,
  listParsers,
  uploadImport,
  type ConfirmResponse,
  type UploadResponse,
} from "../api/client";

export default function Import() {
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [parserId, setParserId] = useState<string>("");
  const [preview, setPreview] = useState<UploadResponse | null>(null);
  const [confirmed, setConfirmed] = useState<ConfirmResponse | null>(null);

  const { data: parsers } = useQuery({ queryKey: ["parsers"], queryFn: listParsers });

  const upload = useMutation({
    mutationFn: () => uploadImport(file!, parserId || undefined),
    onSuccess: (data) => {
      setPreview(data);
      setConfirmed(null);
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

  return (
    <div className="page">
      <h1 className="page__title">Import</h1>

      <div className="card">
        <h2 className="card__title">Upload a bank statement (CSV, PDF, or a photo/scan)</h2>
        <p className="muted">
          CSV is most reliable. PDF, and now <strong>photos or scans</strong> (JPG/PNG, or a
          scanned PDF), are read best-effort with OCR — extracted rows are
          <strong> flagged for review</strong> so you can verify them on the Transactions page.
        </p>
        <div className="form-row">
          <input
            ref={fileInput}
            type="file"
            accept=".csv,text/csv,.pdf,application/pdf,image/*"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setPreview(null);
              setConfirmed(null);
            }}
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
        {upload.isError && <p className="status status--error">{String(upload.error)}</p>}
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
