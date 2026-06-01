import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addFxRate,
  backfillFx,
  disableEncryption,
  downloadDatabaseBackup,
  downloadEncryptedBackup,
  enableEncryption,
  exportConfig,
  getAiStatus,
  getHealth,
  getMqttStatus,
  getSecurityStatus,
  getSettings,
  importConfig,
  listFxRates,
  loadDemoData,
  PRIVACY_MODES,
  publishMqtt,
  restoreDatabase,
  restoreEncryptedDatabase,
  updateSettings,
} from "../api/client";

export default function Settings() {
  const qc = useQueryClient();
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const restoreInput = useRef<HTMLInputElement>(null);
  const configInput = useRef<HTMLInputElement>(null);
  const encRestoreInput = useRef<HTMLInputElement>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [passphrase, setPassphrase] = useState("");

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

      <MqttCard onMessage={ok} onError={fail} />

      <AiCard onMessage={ok} onError={fail} />

      <SecurityCard onMessage={ok} onError={fail} />

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

        <h3 style={{ margin: "16px 0 6px", fontSize: "0.95rem" }}>Encrypted backup</h3>
        <p className="muted">
          Protect a backup with a passphrase (AES-256-GCM) — safe to keep off-device or in the
          cloud. <strong>If you lose the passphrase the backup is unrecoverable.</strong>
        </p>
        <div className="form-row">
          <input
            type="password"
            placeholder="Passphrase"
            value={passphrase}
            onChange={(e) => setPassphrase(e.target.value)}
            style={{ minWidth: 200 }}
          />
          <button
            className="btn"
            disabled={!passphrase}
            onClick={() => downloadEncryptedBackup(passphrase).then(() => ok("Encrypted backup downloaded.")).catch(fail)}
          >
            ⬇ Download encrypted
          </button>
          <input
            ref={encRestoreInput}
            type="file"
            accept=".enc,application/octet-stream"
            style={{ display: "none" }}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f && passphrase &&
                  confirm("Restore will REPLACE your current database (backed up to <db>.bak first). Continue?")) {
                restoreEncryptedDatabase(f, passphrase)
                  .then(() => { ok("Database restored from encrypted backup."); qc.invalidateQueries(); })
                  .catch(fail);
              }
              if (encRestoreInput.current) encRestoreInput.current.value = "";
            }}
          />
          <button className="btn btn--ghost" disabled={!passphrase} onClick={() => encRestoreInput.current?.click()}>
            ⬆ Restore encrypted…
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
          Setup mode, accounts, import profiles, AI providers and OCR arrive in later stages
          (spec §25.12). Cloud backup destinations are on the backlog (#15).
        </p>
      </div>
    </div>
  );
}

function MqttCard({
  onMessage,
  onError,
}: {
  onMessage: (m: string) => void;
  onError: (e: unknown) => void;
}) {
  const status = useQuery({ queryKey: ["mqtt-status"], queryFn: getMqttStatus });
  const publish = useMutation({
    mutationFn: publishMqtt,
    onSuccess: (r) => onMessage(`Published ${r.published} MQTT message(s) for ${r.sensors ?? "?"} sensors.`),
    onError,
  });
  const s = status.data;

  return (
    <div className="card">
      <h2 className="card__title">Home Assistant sensors (MQTT)</h2>
      {!s && <p className="muted">Loading…</p>}
      {s && (
        <>
          <p className="muted">
            Publishes spend, income, net, review count and per-budget progress as Home Assistant
            sensors via MQTT discovery (spec §27). Off by default (strict-local). Enable it with the
            add-on’s <code>mqtt_enabled</code> option and point it at your broker
            (<code>{s.host}:{s.port}</code>).
          </p>
          <ul className="kv">
            <li><span>Status</span><span>{s.enabled ? "enabled" : "disabled"}</span></li>
            <li><span>Driver (paho-mqtt)</span><span>{s.available ? "installed" : "not installed"}</span></li>
            <li><span>Discovery prefix</span><span>{s.discovery_prefix}</span></li>
            <li><span>State topic</span><span>{s.base_topic}/state/…</span></li>
            {s.sensor_count != null && <li><span>Sensors</span><span>{s.sensor_count}</span></li>}
          </ul>
          <button
            className="btn"
            disabled={!s.enabled || publish.isPending}
            title={s.enabled ? "Publish sensors now" : "Enable MQTT in the add-on options first"}
            onClick={() => publish.mutate()}
          >
            {publish.isPending ? "Publishing…" : "Publish now"}
          </button>
          {!s.enabled && <p className="muted" style={{ fontSize: "0.78rem", marginTop: 6 }}>Enable MQTT in the add-on options to publish.</p>}
        </>
      )}
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

function AiCard({
  onMessage,
  onError,
}: {
  onMessage: (m: string) => void;
  onError: (e: unknown) => void;
}) {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["ai-status"], queryFn: getAiStatus });
  const settings = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const [draft, setDraft] = useState<Record<string, string>>({});

  const s = settings.data;
  const value = (key: string) => draft[key] ?? (s?.[key] ?? "");

  const save = useMutation({
    mutationFn: () => updateSettings(draft),
    onSuccess: () => {
      setDraft({});
      onMessage("AI settings saved.");
      qc.invalidateQueries({ queryKey: ["settings"] });
      qc.invalidateQueries({ queryKey: ["ai-status"] });
    },
    onError,
  });

  const st = status.data;
  const isCloud = (value("privacy_mode") || "").startsWith("cloud");

  return (
    <div className="card">
      <h2 className="card__title">AI assistant</h2>
      <p className="muted">
        <strong>Off by default.</strong> AI only ever <em>suggests</em> — it never changes a category on
        its own. Local mode keeps data on your device; cloud modes send a <strong>minimal, redacted</strong>
        payload (description/amount/currency/candidate categories only). Works with any OpenAI-compatible
        endpoint (Ollama, LM Studio, Home Assistant LLM, or a cloud API).
      </p>
      <div className="form-row" style={{ flexWrap: "wrap", gap: 8 }}>
        <label>
          Mode{" "}
          <select value={value("privacy_mode")} onChange={(e) => setDraft((d) => ({ ...d, privacy_mode: e.target.value }))}>
            {PRIVACY_MODES.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
        <label>
          Provider{" "}
          <select value={value("ai_provider")} onChange={(e) => setDraft((d) => ({ ...d, ai_provider: e.target.value }))}>
            <option value="none">none</option>
            <option value="openai_compatible">openai_compatible</option>
          </select>
        </label>
        <input
          placeholder="Base URL (e.g. http://localhost:11434/v1)"
          value={value("ai_base_url")}
          style={{ minWidth: 240 }}
          onChange={(e) => setDraft((d) => ({ ...d, ai_base_url: e.target.value }))}
        />
        <input
          placeholder="Model (e.g. llama3)"
          value={value("ai_model")}
          style={{ width: 140 }}
          onChange={(e) => setDraft((d) => ({ ...d, ai_model: e.target.value }))}
        />
        <button className="btn" disabled={Object.keys(draft).length === 0 || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? "Saving…" : "Save AI settings"}
        </button>
      </div>
      {st && (
        <ul className="kv" style={{ marginTop: 8 }}>
          <li><span>Status</span><span>{st.enabled ? "enabled" : "disabled"}</span></li>
          <li><span>Configured</span><span>{st.configured ? "yes" : "no (set provider + URL + model)"}</span></li>
          {isCloud && <li><span>API key (HAFI_AI_API_KEY)</span><span>{st.has_api_key ? "set" : "not set"}</span></li>}
        </ul>
      )}
      {isCloud && (
        <p className="muted" style={{ fontSize: "0.78rem" }}>
          Cloud mode: set the API key as the add-on’s <code>HAFI_AI_API_KEY</code> option (never stored in the
          database). <code>cloud_manual</code> asks you to approve each request; <code>cloud_auto</code> sends automatically.
        </p>
      )}
    </div>
  );
}

function SecurityCard({
  onMessage,
  onError,
}: {
  onMessage: (m: string) => void;
  onError: (e: unknown) => void;
}) {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["security-status"], queryFn: getSecurityStatus });
  const [pass, setPass] = useState("");
  const [unlockMode, setUnlockMode] = useState("prompt");

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["security-status"] });
    setPass("");
  };
  const enable = useMutation({
    mutationFn: () => enableEncryption(pass, unlockMode),
    onSuccess: () => { onMessage("Database encrypted."); invalidate(); },
    onError,
  });
  const disable = useMutation({
    mutationFn: () => disableEncryption(pass),
    onSuccess: () => { onMessage("Encryption disabled (database decrypted)."); invalidate(); },
    onError,
  });

  const s = status.data;

  return (
    <div className="card">
      <h2 className="card__title">Database encryption (at rest)</h2>
      {!s && <p className="muted">Loading…</p>}

      {s && !s.encryption_available && (
        <p className="muted">
          At-rest encryption needs the SQLCipher driver, which is available on Linux / the Home
          Assistant add-on but not on this platform. (Encrypted <em>backups</em> above work
          everywhere.)
        </p>
      )}

      {s && s.encryption_available && !s.encryption_enabled && (
        <>
          <p className="muted">
            Encrypt the database on disk so nothing but this app (with your passphrase) can read it.
            <strong> If you lose the passphrase, the data is unrecoverable.</strong>
          </p>
          <div className="form-row">
            <input
              type="password"
              placeholder="New passphrase"
              value={pass}
              onChange={(e) => setPass(e.target.value)}
              style={{ minWidth: 200 }}
            />
            <label>
              On restart{" "}
              <select value={unlockMode} onChange={(e) => setUnlockMode(e.target.value)}>
                <option value="prompt">Prompt me (most secure)</option>
                <option value="stored">Use stored key (unattended)</option>
              </select>
            </label>
            <button className="btn" disabled={!pass || enable.isPending} onClick={() => enable.mutate()}>
              {enable.isPending ? "Encrypting…" : "Encrypt database"}
            </button>
          </div>
          {unlockMode === "stored" && (
            <p className="muted" style={{ fontSize: "0.78rem" }}>
              Stored mode also needs the passphrase set as the add-on’s <code>HAFI_DB_KEY</code> option
              so it can start unattended.
            </p>
          )}
        </>
      )}

      {s && s.encryption_enabled && (
        <>
          <p className="status status--ok">
            🔒 Encrypted · unlock mode: {s.unlock_mode}
          </p>
          <div className="form-row">
            <input
              type="password"
              placeholder="Passphrase to disable"
              value={pass}
              onChange={(e) => setPass(e.target.value)}
              style={{ minWidth: 200 }}
            />
            <button className="btn btn--ghost" disabled={!pass || disable.isPending} onClick={() => disable.mutate()}>
              {disable.isPending ? "Decrypting…" : "Disable encryption"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
