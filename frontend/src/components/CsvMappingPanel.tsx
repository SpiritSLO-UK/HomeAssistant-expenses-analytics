import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createImportProfile,
  deleteImportProfile,
  inspectCsv,
  listImportProfiles,
  uploadImport,
  type ImportProfile,
  type UploadResponse,
} from "../api/client";

const REPO = "https://github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics";

/** Drop empty header assignments so we only send real mappings. */
function cleanMapping(m: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(m).filter(([, v]) => v));
}

/** A profile is just column names — safe to download/share (no transaction data). */
function profileBlob(p: ImportProfile): string {
  return JSON.stringify({ name: p.name, mapping: p.mapping, default_currency: p.default_currency }, null, 2);
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

// Column-mapping UI for a CSV no built-in parser handles (backlog: user-defined
// CSV import). Inspect the file → map columns → preview/import via the generic
// parser; save the mapping as a reusable profile, and export/share it.
export default function CsvMappingPanel({
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
  const [selectedId, setSelectedId] = useState<number | "">("");
  const [profileName, setProfileName] = useState("");
  const [err, setErr] = useState<string | null>(null);

  // Seed from the heuristic suggestion once the file is inspected.
  useEffect(() => {
    if (inspect.data) setMapping(inspect.data.suggested_mapping ?? {});
  }, [inspect.data]);

  const preview = useMutation({
    mutationFn: () => uploadImport(file, "generic_csv", cleanMapping(mapping)),
    onSuccess: (data) => { setErr(null); onPreview(data); },
    onError: (e) => setErr(String(e instanceof Error ? e.message : e)),
  });

  const save = useMutation({
    mutationFn: () =>
      createImportProfile({ name: profileName.trim(), mapping: cleanMapping(mapping), default_currency: "GBP" }),
    onSuccess: () => {
      setProfileName("");
      setErr(null);
      qc.invalidateQueries({ queryKey: ["import-profiles"] });
    },
    onError: (e) => setErr(String(e instanceof Error ? e.message : e)),
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
        <p className="status status--error">Couldn't read this as a CSV: {String(inspect.error)}</p>
      </div>
    );
  }

  const { headers, sample_rows, fields } = inspect.data;
  const selected = profiles.data?.find((p) => p.id === selectedId);
  const canPreview = !!mapping.date && !!(mapping.amount || mapping.debit || mapping.credit);

  const setField = (key: string, header: string) =>
    setMapping((m) => {
      const next = { ...m };
      if (header) next[key] = header;
      else delete next[key];
      return next;
    });

  return (
    <div className="card" style={{ background: "var(--surface)" }}>
      <h2 className="card__title">⚙ Map columns (custom CSV)</h2>
      <p className="muted">
        For a bank with no built-in parser: tell us which column is which, preview, then import. Save it
        as a profile to reuse next time — and export or share it so it can become a built-in parser.
      </p>

      {!!profiles.data?.length && (
        <div className="form-row" style={{ flexWrap: "wrap", gap: 8 }}>
          <label>
            Saved profile{" "}
            <select
              value={selectedId}
              onChange={(e) => {
                const id = e.target.value ? Number(e.target.value) : "";
                setSelectedId(id);
                const p = profiles.data?.find((x) => x.id === id);
                if (p) setMapping({ ...p.mapping });
              }}
            >
              <option value="">— choose —</option>
              {profiles.data.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </label>
          {selected && (
            <>
              <button type="button" className="btn btn--ghost" onClick={() => exportProfile(selected)} title="Download (anonymous — column names only)">⬇ Export</button>
              <button type="button" className="btn btn--ghost" onClick={() => shareProfile(selected)} title="Open a prefilled GitHub issue">↗ Share</button>
              <button type="button" className="btn btn--ghost" disabled={del.isPending} onClick={() => del.mutate(selected.id)} title="Delete this profile">🗑</button>
            </>
          )}
        </div>
      )}

      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Field</th><th>CSV column</th></tr></thead>
          <tbody>
            {fields.map((f) => (
              <tr key={f.key}>
                <td>{f.label}{f.required ? " *" : ""}</td>
                <td>
                  <select value={mapping[f.key] ?? ""} onChange={(e) => setField(f.key, e.target.value)}>
                    <option value="">— none —</option>
                    {headers.map((h) => <option key={h} value={h}>{h}</option>)}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted" style={{ fontSize: "0.8rem" }}>
        Minimum: a <strong>Date</strong> and either an <strong>Amount</strong> (signed) or a{" "}
        <strong>Money out</strong>/<strong>Money in</strong> pair.
      </p>

      {sample_rows.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead><tr>{headers.map((h) => <th key={h}>{h}</th>)}</tr></thead>
            <tbody>
              {sample_rows.map((r) => (
                <tr key={headers.map((h) => r[h] ?? "").join("")}>
                  {headers.map((h) => <td key={h}>{r[h] ?? ""}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="form-row" style={{ flexWrap: "wrap", gap: 8 }}>
        <button type="button" className="btn" disabled={!canPreview || preview.isPending} onClick={() => preview.mutate()}>
          {preview.isPending ? "Previewing…" : "Preview with this mapping"}
        </button>
        <input placeholder="Profile name" value={profileName} onChange={(e) => setProfileName(e.target.value)} />
        <button
          type="button"
          className="btn btn--ghost"
          disabled={!canPreview || !profileName.trim() || save.isPending}
          onClick={() => save.mutate()}
        >
          {save.isPending ? "Saving…" : "Save as profile"}
        </button>
      </div>
      {!canPreview && <p className="muted" style={{ fontSize: "0.8rem" }}>Map a Date + an Amount (or Money out/Money in) to continue.</p>}
      {err && <p className="status status--error">{err}</p>}
    </div>
  );
}
