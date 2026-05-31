import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addFxRate,
  backfillFx,
  downloadDatabaseBackup,
  exportConfig,
  getHealth,
  getSettings,
  importConfig,
  listFxRates,
  loadDemoData,
  restoreDatabase,
  updateSettings,
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
        <p className="muted" style={{ fontSize: "0.78rem", marginTop: 8 }}>
          Provided “as is”, no warranty, not financial advice — use at your own risk and keep
          your own backups. Built with the help of an AI assistant; review before relying on it.
        </p>
      </div>

      <CurrencyFx onMessage={ok} onError={fail} />

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
          Setup mode, accounts, import profiles, AI providers, OCR, MQTT and Home Assistant sensors
          arrive in later stages (spec §25.12). Encrypted / cloud backup is on the backlog (#15).
        </p>
      </div>
    </div>
  );
}

function CurrencyFx({
  onMessage,
  onError,
}: {
  onMessage: (m: string) => void;
  onError: (e: unknown) => void;
}) {
  const qc = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const rates = useQuery({ queryKey: ["fx-rates"], queryFn: listFxRates });
  const [rateDate, setRateDate] = useState("");
  const [quote, setQuote] = useState("");
  const [rate, setRate] = useState("");

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["settings"] });
    qc.invalidateQueries({ queryKey: ["fx-rates"] });
    qc.invalidateQueries({ queryKey: ["transactions"] });
    qc.invalidateQueries({ queryKey: ["summary"] });
  };

  const save = useMutation({
    mutationFn: (patch: Record<string, string>) => updateSettings(patch),
    onSuccess: (r) => {
      onMessage(
        r.recompute
          ? "Base currency changed — re-converted existing transactions."
          : "Settings saved.",
      );
      invalidate();
    },
    onError,
  });

  const addRate = useMutation({
    mutationFn: () => addFxRate(rateDate, quote, rate),
    onSuccess: () => {
      setQuote("");
      setRate("");
      onMessage("Rate saved.");
      invalidate();
    },
    onError,
  });

  const backfill = useMutation({
    mutationFn: backfillFx,
    onSuccess: (r) => {
      onMessage(`Backfill: ${r.filled} filled, ${r.still_missing} still missing.`);
      invalidate();
    },
    onError,
  });

  const base = settings.data?.base_currency ?? "GBP";
  const mode = settings.data?.fx_mode ?? "manual";

  return (
    <div className="card">
      <h2 className="card__title">Currency &amp; exchange rates</h2>
      <div className="form-row">
        <label>
          Base currency{" "}
          <input
            defaultValue={base}
            maxLength={3}
            style={{ width: 70, textTransform: "uppercase" }}
            onBlur={(e) => {
              const v = e.target.value.trim().toUpperCase();
              if (v && v !== base) save.mutate({ base_currency: v });
            }}
          />
        </label>
        <label>
          FX rates{" "}
          <select value={mode} onChange={(e) => save.mutate({ fx_mode: e.target.value })}>
            <option value="manual">Manual (no internet)</option>
            <option value="frankfurter">Frankfurter (online, ECB)</option>
          </select>
        </label>
      </div>
      <p className="muted">
        Amounts are stored in their original currency and converted to your base currency.
        Manual mode never makes network calls; Frankfurter fetches free ECB rates (opt-in) and
        caches them. Existing conversions are never rewritten — only missing ones are backfilled.
      </p>

      <div className="form-row" style={{ marginTop: 8 }}>
        <input type="date" value={rateDate} onChange={(e) => setRateDate(e.target.value)} />
        <input
          placeholder="Currency (e.g. EUR)"
          maxLength={3}
          value={quote}
          style={{ width: 130, textTransform: "uppercase" }}
          onChange={(e) => setQuote(e.target.value.toUpperCase())}
        />
        <input
          placeholder={`rate (1 unit = ? ${base})`}
          value={rate}
          style={{ width: 160 }}
          onChange={(e) => setRate(e.target.value)}
        />
        <button
          className="btn"
          disabled={!rateDate || !quote || !rate || addRate.isPending}
          onClick={() => addRate.mutate()}
        >
          Add rate
        </button>
        <button className="btn btn--ghost" disabled={backfill.isPending} onClick={() => backfill.mutate()}>
          {backfill.isPending ? "Backfilling…" : "Backfill missing"}
        </button>
      </div>

      {rates.data && rates.data.length > 0 && (
        <div className="table-wrap" style={{ marginTop: 10 }}>
          <table className="table">
            <thead>
              <tr><th>Date</th><th>From</th><th>To</th><th className="num">Rate</th><th>Source</th></tr>
            </thead>
            <tbody>
              {rates.data.slice(0, 20).map((r) => (
                <tr key={r.id}>
                  <td>{r.rate_date}</td>
                  <td>{r.quote}</td>
                  <td>{r.base}</td>
                  <td className="num">{r.rate}</td>
                  <td className="muted">{r.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
