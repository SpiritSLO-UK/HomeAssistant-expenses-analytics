import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import AiImageWarningDialog from "../components/AiImageWarningDialog";
import CameraCaptureButton from "../components/CameraCaptureButton";
import { isImageAiWarningDismissed, setImageAiWarningDismissed } from "../prefs";
import {
  aiExtractImport,
  ApiError,
  confirmImport,
  createImportProfile,
  deleteImportProfile,
  getAiStatus,
  inspectCsv,
  listAccounts,
  listImportProfiles,
  listParsers,
  setFundingLink,
  uploadImport,
  uploadReceipt,
  type ConfirmResponse,
  type DateFormat,
  type FundingLabel,
  type ImportProfile,
  type PreviewRow,
  type UploadResponse,
} from "../api/client";

// Prefer the typed ApiError's parsed body/detail over a stringified error: a raw
// String(error) leaks the "API <endpoint> failed:" prefix and is fragile to match
// on. Fall back gracefully to a plain Error message, then String().
function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = error.body?.detail;
    return typeof detail === "string" ? detail : error.message;
  }
  if (error instanceof Error) return error.message;
  return String(error);
}

export default function Import() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [parserId, setParserId] = useState<string>("");
  const [preview, setPreview] = useState<UploadResponse | null>(null);
  const [confirmed, setConfirmed] = useState<ConfirmResponse | null>(null);
  const [showAiWarn, setShowAiWarn] = useState(false);
  const [showMapper, setShowMapper] = useState(false);

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
    setShowMapper(false);
    setShowAiWarn(false);
    upload.reset();
    confirm.reset();
    // Also clear the recovery-path mutations so a prior ai-extract / add-receipt
    // error doesn't linger under a fresh file (previously left stuck on-screen).
    aiExtract.reset();
    addAsReceipt.reset();
    if (fileInput.current) fileInput.current.value = "";
  }

  // While any upload path is in flight, disable the others: they all act on the
  // same `file`, so concurrent submits race and confuse the preview/confirm state.
  const anyPending =
    upload.isPending || aiExtract.isPending || addAsReceipt.isPending || confirm.isPending;

  // A statement-image that yielded no rows is almost always a receipt — surface
  // the receipt/AI recovery path as the primary message instead of the raw error.
  // Detect from typed signals (the picked file is an image, or the backend's
  // ApiError detail says no transactions) rather than a regex on String(error).
  const isReceiptish =
    upload.isError &&
    !!file &&
    (file.type.startsWith("image/") ||
      /no transactions recognised/i.test(errorMessage(upload.error)));

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
              setShowMapper(false);
            }}
          />
          <CameraCaptureButton
            label="📷 Take photo"
            onCapture={(f) => { setFile(f); setPreview(null); setConfirmed(null); setShowMapper(false); }}
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
            disabled={!file || anyPending}
            onClick={() => upload.mutate()}
          >
            {upload.isPending ? "Uploading…" : "Preview"}
          </button>
          {file && (
            <button className="btn btn--ghost" onClick={() => setShowMapper((v) => !v)}>
              {showMapper ? "Hide column mapping" : "⚙ Map columns (custom CSV)"}
            </button>
          )}
          {(preview || confirmed) && (
            <button className="btn btn--ghost" onClick={reset}>
              Start over
            </button>
          )}
        </div>
        {file && !preview && !confirmed && (
          <p className="muted" style={{ marginTop: 6, fontSize: "0.85rem" }}>Selected: {file.name}</p>
        )}
        {showMapper && file && (
          <CsvMappingPanel
            file={file}
            onPreview={(data) => { setPreview(data); setConfirmed(null); }}
          />
        )}
        {upload.isError && !isReceiptish && (
          <p className="status status--error">{errorMessage(upload.error)}</p>
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
              <button className="btn" disabled={anyPending} onClick={() => addAsReceipt.mutate()}>
                {addAsReceipt.isPending ? "Adding…" : "🧾 Add as a receipt instead"}
              </button>
              {aiStatus.data?.enabled && (
                <button className="btn btn--ghost" disabled={anyPending} onClick={tryAiExtract}>
                  {aiExtract.isPending ? "Asking AI…" : "✨ Extract with AI"}
                </button>
              )}
              <Link className="btn btn--ghost" to="/receipts">Open Receipts page →</Link>
            </div>
          </div>
        )}
        {aiExtract.isError && <p className="status status--error">{errorMessage(aiExtract.error)}</p>}
        {addAsReceipt.isError && <p className="status status--error">{errorMessage(addAsReceipt.error)}</p>}
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
          {preview.funding_labels.length > 0 && (
            <FundingLinkPanel labels={preview.funding_labels} onChanged={() => upload.mutate()} />
          )}
          <PreviewTable rows={preview.preview} />
          <div className="form-row">
            <button
              className="btn"
              disabled={anyPending || preview.report.new === 0}
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
          {confirm.isError && <p className="status status--error">{errorMessage(confirm.error)}</p>}
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
    onError: (e) => setMsg(errorMessage(e)),
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

// Map each Curve "Card Name" to the real account behind it. Curve forwards every
// payment to an underlying card, so the same purchase also lands on that card's
// own statement; linking the two lets us skip the duplicate across accounts.
function FundingLinkPanel({
  labels,
  onChanged,
}: Readonly<{ labels: FundingLabel[]; onChanged: () => void }>) {
  const queryClient = useQueryClient();
  const { data: accounts } = useQuery({ queryKey: ["accounts"], queryFn: listAccounts });
  const save = useMutation({
    mutationFn: (vars: { label: string; accountId: number | null }) =>
      setFundingLink(vars.label, vars.accountId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["funding-links"] });
      onChanged(); // re-preview so cross-account duplicates refresh
    },
  });

  return (
    <div className="card" style={{ background: "var(--surface)" }}>
      <h2 className="card__title">💳 Curve funding cards</h2>
      <p className="muted">
        Curve is a <strong>pass-through</strong> card: each payment is charged to one of your real
        cards, so the same purchase also appears on that card's own statement. Tell us which account
        each card is and we'll skip the duplicates when you import both. Leave a card{" "}
        <em>unmapped</em> (e.g. Curve Cash) to import it normally.
      </p>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Card (from Curve)</th>
              <th className="num">Rows</th>
              <th>Is really…</th>
            </tr>
          </thead>
          <tbody>
            {labels.map((l) => (
              <tr key={l.label}>
                <td>{l.label}</td>
                <td className="num">{l.count}</td>
                <td>
                  <select
                    value={l.account_id ?? ""}
                    disabled={save.isPending}
                    onChange={(e) =>
                      save.mutate({
                        label: l.label,
                        accountId: e.target.value ? Number(e.target.value) : null,
                      })
                    }
                  >
                    <option value="">— not linked —</option>
                    {accounts?.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {save.isError && <p className="status status--error">{errorMessage(save.error)}</p>}
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
          {rows.map((r) => (
            <tr key={`${r.transaction_date}|${r.description_raw}|${r.amount}|${r.currency}`} className={rowClass(r)}>
              <td>{r.transaction_date}</td>
              <td>{r.description_raw}</td>
              <td className={"num " + (r.direction === "credit" ? "amt--pos" : "amt--neg")}>
                {r.amount}
              </td>
              <td>{r.currency}</td>
              <td className="muted">{r.category_hint ?? ""}</td>
              <td>
                <DupBadge row={r} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function rowClass(r: PreviewRow): string {
  if (r.is_duplicate) return "row--dup";
  if (r.warning) return "row--warn";
  return "";
}

// Per-row status badge: a same-account/cross-account duplicate (skipped), or a
// possible cross-account Curve match that's kept but flagged.
function DupBadge({ row }: Readonly<{ row: PreviewRow }>) {
  if (row.is_duplicate) {
    return (
      <span className="tag tag--dup" title={row.dup_reason ?? undefined}>
        {row.dup_reason ?? "dup"}
      </span>
    );
  }
  if (row.warning) {
    return (
      <span className="tag tag--warn" title={row.warning}>
        ⚠ {row.warning}
      </span>
    );
  }
  return null;
}

// ---------------------------------------------------------------------------
// Custom-CSV column mapping (page-local). Kept inside the Import page so the
// mapping UX stays self-contained: inspect the file → map each field to a CSV
// column (with a live first-row preview + inline validation) → preview/import
// via the generic parser; save the mapping as a reusable profile, and
// export/share it so it can become a built-in parser.
// ---------------------------------------------------------------------------

const REPO = "https://github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics";

// Short, plain-language hint for each target field so the user knows what a
// column should contain. Keyed by the backend field key; unknown keys fall
// back to no hint (the label alone is enough).
const FIELD_HELP: Readonly<Record<string, string>> = {
  date: "When the transaction happened (e.g. 2024-01-31 or 31/01/2024).",
  amount: "A single signed amount — negative for money out, positive for money in.",
  description: "The payee / description text shown on the statement.",
  merchant: "Merchant name, if your CSV keeps it separate from the description.",
  debit: "Money out as a positive number. Pair with Money in when there's no single signed amount.",
  credit: "Money in as a positive number.",
  currency: "3-letter currency code (defaults to GBP if left unmapped).",
};

/** Drop empty header assignments so we only send real mappings. */
function cleanMapping(m: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(m).filter(([, v]) => v));
}

/** A profile is just column names — safe to download/share (no transaction data). */
function profileBlob(p: ImportProfile): string {
  return JSON.stringify(
    { name: p.name, mapping: p.mapping, default_currency: p.default_currency, date_format: p.date_format },
    null,
    2,
  );
}

function exportProfile(p: ImportProfile): void {
  const blob = new Blob([profileBlob(p)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `import-profile-${p.name.replace(/[^\w.-]+/g, "_")}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

function shareProfile(p: ImportProfile): void {
  const title = `CSV import mapping: ${p.name}`;
  const body = [
    "Sharing a CSV import column mapping so it can become a built-in parser.",
    "",
    "```json",
    profileBlob(p),
    "```",
    "",
    "_(No transaction data — only column names.)_",
  ].join("\n");
  const url = `${REPO}/issues/new?title=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}`;
  globalThis.open(url, "_blank", "noopener,noreferrer");
}

function CsvMappingPanel({
  file,
  onPreview,
}: Readonly<{ file: File; onPreview: (data: UploadResponse) => void }>) {
  const qc = useQueryClient();
  const inspect = useQuery({
    queryKey: ["csv-inspect", file.name, file.size, file.lastModified],
    queryFn: () => inspectCsv(file),
  });
  const profiles = useQuery({ queryKey: ["import-profiles"], queryFn: listImportProfiles });

  const [mapping, setMapping] = useState<Record<string, string>>({});
  // CSV date order for this profile/preview: auto (per-file heuristic) / UK day-first
  // / US month-first. Persisted on the profile and sent with each preview request.
  const [dateFormat, setDateFormat] = useState<DateFormat>("auto");
  const [selectedId, setSelectedId] = useState<number | "">("");
  const [profileName, setProfileName] = useState("");
  const [err, setErr] = useState<string | null>(null);

  // Seed from the heuristic suggestion once the file is inspected.
  useEffect(() => {
    if (inspect.data) setMapping(inspect.data.suggested_mapping ?? {});
  }, [inspect.data]);

  const preview = useMutation({
    mutationFn: () => uploadImport(file, "generic_csv", cleanMapping(mapping), dateFormat),
    onSuccess: (data) => { setErr(null); onPreview(data); },
    onError: (e) => setErr(errorMessage(e)),
  });

  const save = useMutation({
    mutationFn: () =>
      createImportProfile({
        name: profileName.trim(),
        mapping: cleanMapping(mapping),
        default_currency: "GBP",
        date_format: dateFormat,
      }),
    // BUGFIX: after saving, refetch the profiles list AND select the profile we
    // just created so it's immediately usable (previously it stayed on
    // "— choose —" and the new profile appeared unselectable). We seed the cache
    // optimistically so the dropdown shows + selects right away, then invalidate
    // to stay authoritative.
    onSuccess: (created) => {
      setProfileName("");
      setErr(null);
      qc.setQueryData<ImportProfile[]>(["import-profiles"], (old) => {
        const list = old ? [...old] : [];
        if (!list.some((p) => p.id === created.id)) list.push(created);
        return list;
      });
      qc.invalidateQueries({ queryKey: ["import-profiles"] });
      setSelectedId(created.id);
      setMapping({ ...created.mapping });
      setDateFormat(created.date_format);
    },
    onError: (e) => setErr(errorMessage(e)),
  });

  const del = useMutation({
    mutationFn: (id: number) => deleteImportProfile(id),
    onSuccess: () => {
      setSelectedId("");
      qc.invalidateQueries({ queryKey: ["import-profiles"] });
    },
  });

  if (inspect.isPending) {
    return <div className="card" style={{ background: "var(--surface)" }}><p className="muted">Reading columns…</p></div>;
  }
  if (inspect.isError) {
    return (
      <div className="card" style={{ background: "var(--surface)" }}>
        <p className="status status--error">Couldn't read this as a CSV: {errorMessage(inspect.error)}</p>
      </div>
    );
  }

  const { headers, sample_rows, fields } = inspect.data;
  const firstRow = sample_rows[0];
  const selected = profiles.data?.find((p) => p.id === selectedId);

  const hasDate = !!mapping.date;
  const hasAmount = !!(mapping.amount || mapping.debit || mapping.credit);
  const hasDescription = !!mapping.description;
  const canPreview = hasDate && hasAmount;

  // Inline validation: list the required pieces that are still missing.
  const missing: string[] = [];
  if (!hasDate) missing.push("a Date column");
  if (!hasAmount) missing.push("an Amount (or a Money out / Money in pair)");

  const setField = (key: string, header: string) =>
    setMapping((m) => {
      const next = { ...m };
      if (header) next[key] = header;
      else delete next[key];
      return next;
    });

  const applyAutodetect = () => {
    if (inspect.data) setMapping(inspect.data.suggested_mapping ?? {});
  };

  const chooseProfile = (raw: string) => {
    const id = raw ? Number(raw) : "";
    setSelectedId(id);
    const p = profiles.data?.find((x) => x.id === id);
    if (p) {
      setMapping({ ...p.mapping });
      setDateFormat(p.date_format);
    }
  };

  return (
    <div className="card" style={{ background: "var(--surface)" }}>
      <h2 className="card__title">⚙ Map columns (custom CSV)</h2>
      <p className="muted">
        For a bank with no built-in parser: tell us which CSV column holds each field, check the{" "}
        <strong>first-row preview</strong>, then Preview and import. Save it as a profile to reuse next
        time — and export or share it so it can become a built-in parser.
      </p>

      {!!profiles.data?.length && (
        <div className="form-row" style={{ flexWrap: "wrap", gap: 8 }}>
          <label>
            Saved profile{" "}
            <select value={selectedId} onChange={(e) => chooseProfile(e.target.value)}>
              <option value="">— choose —</option>
              {profiles.data.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </label>
          {selected && (
            <>
              <button className="btn btn--ghost" onClick={() => exportProfile(selected)} title="Download (anonymous — column names only)">⬇ Export</button>
              <button className="btn btn--ghost" onClick={() => shareProfile(selected)} title="Open a prefilled GitHub issue">↗ Share</button>
              <button className="btn btn--ghost" disabled={del.isPending} onClick={() => del.mutate(selected.id)} title="Delete this profile">🗑</button>
            </>
          )}
        </div>
      )}

      <div className="form-row" style={{ flexWrap: "wrap", gap: 8, alignItems: "center" }}>
        <button className="btn btn--ghost" onClick={applyAutodetect} title="Guess columns from their header names">
          ✨ Auto-detect columns
        </button>
        <label title="How dates like 01/02/2024 are read — pick your bank's order if auto-detect gets it wrong">
          Date format{" "}
          <select value={dateFormat} onChange={(e) => setDateFormat(e.target.value as DateFormat)}>
            <option value="auto">Auto-detect</option>
            <option value="dmy">Day-first DD/MM</option>
            <option value="mdy">Month-first MM/DD</option>
          </select>
        </label>
      </div>

      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Field</th><th>CSV column</th><th>First row</th></tr></thead>
          <tbody>
            {fields.map((f) => {
              const header = mapping[f.key];
              const sample = header ? firstRow?.[header] : undefined;
              const help = FIELD_HELP[f.key] ?? "";
              return (
                <tr key={f.key}>
                  <td>
                    <div>{f.label}{f.required ? " *" : ""}</div>
                    {help && <div className="muted" style={{ fontSize: "0.78rem" }}>{help}</div>}
                  </td>
                  <td>
                    <select value={header ?? ""} onChange={(e) => setField(f.key, e.target.value)}>
                      <option value="">— none —</option>
                      {headers.map((h) => <option key={h} value={h}>{h}</option>)}
                    </select>
                  </td>
                  <td className="muted" style={{ maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis" }}>
                    {header ? (sample ?? "") : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="muted" style={{ fontSize: "0.8rem" }}>
        Minimum: a <strong>Date</strong> and either an <strong>Amount</strong> (signed) or a{" "}
        <strong>Money out</strong>/<strong>Money in</strong> pair. A{" "}
        <strong>Description</strong> is recommended so transactions are readable.
      </p>

      {sample_rows.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead><tr>{headers.map((h) => <th key={h}>{h}</th>)}</tr></thead>
            <tbody>
              {sample_rows.map((r) => (
                <tr key={headers.map((h) => r[h] ?? "").join("")}>
                  {headers.map((h) => <td key={h}>{r[h] ?? ""}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="form-row" style={{ flexWrap: "wrap", gap: 8 }}>
        <button className="btn" disabled={!canPreview || preview.isPending} onClick={() => preview.mutate()}>
          {preview.isPending ? "Previewing…" : "Preview with this mapping"}
        </button>
        <input placeholder="Profile name" value={profileName} onChange={(e) => setProfileName(e.target.value)} />
        <button
          className="btn btn--ghost"
          disabled={!canPreview || !profileName.trim() || save.isPending}
          onClick={() => save.mutate()}
        >
          {save.isPending ? "Saving…" : "Save as profile"}
        </button>
      </div>
      {missing.length > 0 && (
        <p className="muted" style={{ fontSize: "0.8rem" }}>
          Still needed to continue: {missing.join(" and ")}.
        </p>
      )}
      {canPreview && !hasDescription && (
        <p className="muted" style={{ fontSize: "0.8rem" }}>
          Tip: map a <strong>Description</strong> column so imported transactions are easy to read.
        </p>
      )}
      {err && <p className="status status--error">{err}</p>}
    </div>
  );
}
