import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  downloadDatabaseBackup,
  exportConfig,
  getHealth,
  importConfig,
  loadDemoData,
  restoreDatabase,
} from "../api/client";

export default function Settings() {
  const qc = useQueryClient();
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const restoreInput = useRef<HTMLInputElement>(null);
  const configInput = useRef<HTMLInputElement>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  function ok(m: string) {
    setMsg(m);
    setErr(null);
    qc.invalidateQueries();
  }
  function fail(e: unknown) {
    setErr(String(e));
    setMsg(null);
  }

  const demo = useMutation({
    mutationFn: loadDemoData,
    onSuccess: (r) => ok(`Loaded demo data: ${r.new} new, ${r.duplicates} duplicates skipped.`),
    onError: fail,
  });

  const restore = useMutation({
    mutationFn: (f: File) => restoreDatabase(f),
    onSuccess: () => ok("Database restored."),
    onError: fail,
  });

  const importCfg = useMutation({
    mutationFn: (f: File) => importConfig(f),
    onSuccess: (r) =>
      ok(`Imported config: ${r.categories_added} categories, ${r.vendors_added} vendors added.`),
    onError: fail,
  });

  return (
    <div className="page">
      <h1 className="page__title">Settings</h1>

      {msg && <p className="status status--ok">{msg}</p>}
      {err && <p className="status status--error">{err}</p>}

      <div className="card">
        <h2 className="card__title">Status</h2>
        <ul className="kv">
          <li><span>Version</span><span>{health.data?.version ?? "—"}</span></li>
          <li><span>Privacy mode</span><span>{health.data?.privacy_mode ?? "—"}</span></li>
          <li><span>Database</span><span>{health.data?.database ?? "—"}</span></li>
        </ul>
        <p className="muted">
          Strict local mode is the default — no external network calls. See the privacy and
          security model in the project docs (docs/privacy.md, docs/security.md).
        </p>
      </div>

      <div className="card">
        <h2 className="card__title">Demo data</h2>
        <p className="muted">Load a small fabricated dataset to explore the app. Safe to re-run — duplicates are skipped.</p>
        <button className="btn" disabled={demo.isPending} onClick={() => demo.mutate()}>
          {demo.isPending ? "Loading…" : "Load demo data"}
        </button>
      </div>

      <div className="card">
        <h2 className="card__title">Backup &amp; restore</h2>
        <p className="muted">Download a full copy of your database, or restore one. Your data never leaves your device.</p>
        <div className="form-row">
          <button className="btn" onClick={() => downloadDatabaseBackup().catch(fail)}>
            ⬇ Download database backup
          </button>
          <input
            ref={restoreInput}
            type="file"
            accept=".db,.sqlite,application/octet-stream"
            style={{ display: "none" }}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f && confirm("Restore will REPLACE your current database. The current one is backed up to <db>.bak first. Continue?")) {
                restore.mutate(f);
              }
              if (restoreInput.current) restoreInput.current.value = "";
            }}
          />
          <button className="btn btn--ghost" onClick={() => restoreInput.current?.click()}>
            ⬆ Restore from backup…
          </button>
        </div>
      </div>

      <div className="card">
        <h2 className="card__title">Config &amp; library</h2>
        <p className="muted">Export or import your categories, vendor aliases and settings as JSON (import merges, never deletes).</p>
        <div className="form-row">
          <button className="btn" onClick={() => exportConfig().catch(fail)}>⬇ Export config</button>
          <input
            ref={configInput}
            type="file"
            accept=".json,application/json"
            style={{ display: "none" }}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) importCfg.mutate(f);
              if (configInput.current) configInput.current.value = "";
            }}
          />
          <button className="btn btn--ghost" onClick={() => configInput.current?.click()}>⬆ Import config…</button>
        </div>
      </div>

      <div className="card">
        <h2 className="card__title">Coming later</h2>
        <p className="muted">
          Setup mode, currency, accounts, import profiles, AI providers, OCR, MQTT and Home
          Assistant sensors arrive in later stages (spec §25.12). Encrypted / cloud backup is on the
          backlog (#15), pending a master-key decision.
        </p>
      </div>
    </div>
  );
}
