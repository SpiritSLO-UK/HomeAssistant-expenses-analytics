import { useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { QRCodeSVG } from "qrcode.react";
import {
  addFxRate,
  backfillFx,
  disableEncryption,
  dismissSecurityCheck,
  downloadDatabaseBackup,
  downloadEncryptedBackup,
  enableEncryption,
  exportConfig,
  getAiStatus,
  getHealth,
  testAiConnection,
  getMe,
  getDemoStatus,
  getMissingFx,
  getMqttStatus,
  getMqttSensors,
  updateMqttSensors,
  getPaperlessStatus,
  testPaperlessConnection,
  getSecurityHealth,
  getSecurityStatus,
  getServices,
  getSettings,
  getSettingsStats,
  getSupportedCurrencies,
  updateServiceSettings,
  importConfig,
  listFxRates,
  loadDemoData,
  removeDemoData,
  mfaDisable,
  mfaEnable,
  mfaSetup,
  mfaVerify,
  setSessionToken,
  PRIVACY_MODES,
  publishMqtt,
  restoreDatabase,
  restoreEncryptedDatabase,
  updateSettings,
  getRetentionPolicy,
  updateRetentionPolicy,
  previewRetention,
  runRetention,
  isStepUpError,
  mfaStepUp,
  generateMfaBackupCodes,
  getMfaBackupCodesRemaining,
  type AIStatus,
  type RetentionPolicyResponse,
  type RetentionTypePolicy,
  type RetentionPlan,
  type RetentionTypePlan,
  type BackupTrim,
} from "../api/client";
import { useServerState } from "../lib/useServerState";
import { clearAllPrefs, getThemePref, isCloudAiAcknowledged, setCloudAiAcknowledged } from "../prefs";
import { setTheme, type ThemePref } from "../theme";
import CloudAiDisclaimerDialog from "../components/CloudAiDisclaimerDialog";
import { useConfirm } from "../components/dialogs";
import CountrySelect from "../components/CountrySelect";
import PaperlessSetupNote from "../components/PaperlessSetupNote";
import { useOptimisticSelect } from "../hooks/useOptimisticSelect";

const THEME_OPTIONS: { value: ThemePref; label: string }[] = [
  { value: "system", label: "🖥️ System" },
  { value: "light", label: "☀️ Light" },
  { value: "dark", label: "🌙 Dark" },
];

// Per-device colour theme. Personal (not a server setting), so it's ungated and
// applied immediately on click; "System" follows the OS light/dark preference.
function AppearanceCard() {
  const [pref, setPref] = useState<ThemePref>(getThemePref());
  function choose(value: ThemePref) {
    setPref(value);
    setTheme(value);
  }
  return (
    <div className="card">
      <h2 className="card__title">Appearance</h2>
      <p className="muted" style={{ marginTop: 0, fontSize: "0.85rem" }}>
        Colour theme for this device. “System” follows your operating system's light/dark setting.
      </p>
      <div className="form-row" style={{ flexWrap: "wrap" }}>
        {THEME_OPTIONS.map((o) => (
          <button
            key={o.value}
            className={"btn btn--sm" + (pref === o.value ? "" : " btn--ghost")}
            onClick={() => choose(o.value)}
          >
            {o.label}
          </button>
        ))}
      </div>
      <hr style={{ border: "none", borderTop: "1px solid var(--border)", margin: "12px 0" }} />
      <p className="muted" style={{ marginTop: 0, fontSize: "0.85rem" }}>
        Reset this device's UI preferences — theme, dashboard card layout, hidden/ordered nav
        tabs and column widths — back to defaults. Doesn't touch your data.
      </p>
      <button className="btn btn--sm btn--ghost" onClick={() => { clearAllPrefs(); globalThis.location.reload(); }}>
        Reset UI preferences
      </button>
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / 1024 ** i;
  return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

// Storage & statistics (owner/manager): database size on disk plus how much
// importing and AI processing has happened. Read-only; backed by /settings/stats.
function StatsCard() {
  const stats = useQuery({ queryKey: ["settings-stats"], queryFn: getSettingsStats });
  const s = stats.data;
  return (
    <div className="card">
      <h2 className="card__title">Storage &amp; statistics</h2>
      {stats.isLoading && <p className="muted">Loading…</p>}
      {s && (
        <ul className="kv">
          <li><span>Database size</span><span>{formatBytes(s.database_bytes)}</span></li>
          <li><span>Transactions</span><span>{s.transactions.toLocaleString()}</span></li>
          <li><span>Statements imported</span><span>{s.statements.toLocaleString()}</span></li>
          <li><span>Receipts</span><span>{s.receipts.toLocaleString()}</span></li>
          <li>
            <span>AI calls</span>
            <span>
              {s.ai_total.toLocaleString()}
              {s.ai_total > 0 && (
                <span className="muted"> ({s.ai_cloud.toLocaleString()} cloud · {s.ai_local.toLocaleString()} local)</span>
              )}
            </span>
          </li>
          {s.ai_total > 0 && (
            <li>
              <span>AI completed / failed</span>
              <span>{s.ai_completed.toLocaleString()} / {s.ai_failed.toLocaleString()}</span>
            </li>
          )}
          {s.ai_avg_seconds != null && (
            <li><span>Avg AI turnaround</span><span>{s.ai_avg_seconds.toFixed(1)} s</span></li>
          )}
        </ul>
      )}
      <p className="muted" style={{ fontSize: "0.78rem", marginTop: 8 }}>
        Database size includes the live SQLite file and its write-ahead-log sidecars.
        AI counts are 0 in strict-local mode (nothing is sent anywhere).
      </p>
    </div>
  );
}

export default function Settings() {
  const qc = useQueryClient();
  const confirm = useConfirm();
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
    // Intentionally broad: the success handlers on this settings page cover
    // whole-DB operations (backup restore, encrypted restore, config import,
    // demo load/remove) that can touch nearly every query on the app. Narrowing
    // to specific keys here would risk under-invalidation (stale data surviving a
    // full-database swap), so we invalidate everything and let refetch reconcile.
    qc.invalidateQueries();
  }
  function fail(e: unknown) {
    setErr(String(e));
    setMsg(null);
  }

  // Encrypted-restore submit: an empty passphrase must surface a clear validation
  // error and keep the chosen file, not silently wipe the input (kept in a helper
  // so the file-input onChange stays trivial and low-complexity).
  async function submitEncryptedRestore(f: File | undefined, input: HTMLInputElement) {
    if (!f) return;
    if (!passphrase) {
      fail("Enter the passphrase used to encrypt this backup before restoring.");
      return; // keep the selected file; don't reset the input
    }
    if (!(await confirm({ message: "Restore will REPLACE your current database (backed up to <db>.bak first). Continue?", confirmLabel: "Restore", danger: true }))) {
      input.value = "";
      return;
    }
    restoreEncryptedDatabase(f, passphrase)
      .then(() => ok("Database restored from encrypted backup."))
      .catch(fail);
    input.value = "";
  }

  const me = useQuery({ queryKey: ["me"], queryFn: getMe });
  const demoStatus = useQuery({ queryKey: ["demo-status"], queryFn: getDemoStatus });

  const demo = useMutation({
    mutationFn: loadDemoData,
    onSuccess: (r) => ok(`Loaded demo data: ${r.new} new, ${r.duplicates} duplicates skipped.`),
    onError: fail,
  });

  const removeDemo = useMutation({
    mutationFn: removeDemoData,
    onSuccess: (r) => {
      const n = Object.values(r.counts).reduce((a, b) => a + b, 0);
      ok(r.removed ? `Removed demo data (${n} rows deleted).` : "No demo data to remove.");
    },
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
      ok(
        `Imported config: ${r.categories_added} categories, ${r.vendors_added} vendors, ` +
          `${r.settings_set} settings added.` +
          (r.settings_skipped ? ` (${r.settings_skipped} setting(s) skipped — not importable.)` : ""),
      ),
    onError: fail,
  });

  return (
    <div className="page">
      <h1 className="page__title">Settings</h1>

      <AppearanceCard />

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

      {me.data && !me.data.can_manage_settings && (
        <div className="card">
          <p className="muted">
            General settings (currency, integrations, AI) are managed by the owner or someone
            they've granted access. You can still manage your own two-factor security below.
          </p>
        </div>
      )}

      {me.data?.can_manage_settings && <StatsCard />}

      {me.data?.can_manage_settings && <ServicesCard onMessage={ok} onError={fail} />}

      {me.data?.can_manage_settings && <CurrencyFx onMessage={ok} onError={fail} />}

      {me.data?.can_manage_settings && <LocationDefaultsCard onMessage={ok} onError={fail} />}

      {me.data?.can_manage_settings && <MqttCard onMessage={ok} onError={fail} />}

      {me.data?.can_manage_settings && <IntegrationsCard onMessage={ok} onError={fail} />}

      {me.data?.can_manage_settings && <AiCard onMessage={ok} onError={fail} />}

      <MfaCard onMessage={ok} onError={fail} />

      <SecurityHealthCard onError={fail} />

      <SecurityCard onMessage={ok} onError={fail} />

      <RetentionCard onMessage={ok} onError={fail} />

      <LoggingCard onMessage={ok} onError={fail} />

      <div className="card">
        <h2 className="card__title">Demo data</h2>
        <p className="muted">Load a small fabricated dataset to explore the app. Safe to re-run — duplicates are skipped.</p>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="btn" disabled={demo.isPending} onClick={() => demo.mutate()}>
            {demo.isPending ? "Loading…" : "Load demo data"}
          </button>
          {me.data?.is_admin && demoStatus.data?.has_demo_data && (
            <button
              className="btn btn--danger"
              disabled={removeDemo.isPending}
              onClick={async () => {
                if (
                  await confirm({
                    message:
                      "Remove all demo data? This deletes only the demo's own rows (its " +
                      "transactions, example projects/budgets/savings, demo members, vendors and " +
                      "review items). Real imports and anything you added are kept.",
                    confirmLabel: "Remove demo data",
                    danger: true,
                  })
                ) {
                  removeDemo.mutate();
                }
              }}
            >
              {removeDemo.isPending ? "Removing…" : "Remove demo data"}
            </button>
          )}
        </div>
      </div>

      {/* Backup/restore + config import-export are owner-only (server enforces
          require_owner; restoring an arbitrary DB or importing config = takeover). */}
      {me.data?.is_admin && (
      <>
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
            onChange={async (e) => {
              const f = e.target.files?.[0];
              if (f && await confirm({ message: "Restore will REPLACE your current database. The current one is backed up to <db>.bak first. Continue?", confirmLabel: "Restore", danger: true })) {
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
            name="encrypted-backup-passphrase"
            autoComplete="new-password"
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
            onChange={(e) => submitEncryptedRestore(e.target.files?.[0], e.target)}
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
      </>
      )}

      <div className="card">
        <h2 className="card__title">About &amp; source</h2>
        <p className="muted">
          HA Finance Intelligence — a local-first, Home Assistant-first personal finance app.
          Source code, issues and documentation:{" "}
          <a
            href="https://github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics"
            target="_blank"
            rel="noopener noreferrer"
          >
            github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics
          </a>.
        </p>
        <p className="muted" style={{ fontSize: "0.78rem" }}>
          Licensed under Apache-2.0. Provided “as is”, without warranty, and not financial advice —
          see the disclaimer above and the privacy / security docs. Built with the help of an AI assistant.
        </p>
      </div>
    </div>
  );
}

// Settings → Services (backlog §38; per-service panels #67/#68). AI/OCR/online-FX
// are runtime-toggleable; MQTT is add-on-configured, so it's read-only. Each
// service gets its own panel with a consistent header + status tag + control.
// Module-level so they aren't recreated (and remounted) on every render.

// A compact labelled on/off control used inside a service panel.
function ServiceToggle({
  on,
  busy,
  onChange,
}: Readonly<{ on: boolean; busy?: boolean; onChange: (v: boolean) => void }>) {
  return (
    <label className="checkbox" style={{ whiteSpace: "nowrap" }}>
      <input type="checkbox" checked={on} disabled={busy} onChange={(e) => onChange(e.target.checked)} />{" "}
      {on ? "On" : "Off"}
    </label>
  );
}

// One self-contained service panel: name + On/Off status tag, a detail line and
// the service's own control (a toggle, a button or a read-only note).
function ServicePanel({
  title,
  detail,
  on,
  children,
}: Readonly<{ title: string; detail: string; on: boolean; children?: ReactNode }>) {
  return (
    <div className="service-panel">
      <div className="service-panel__head">
        <strong>{title}</strong>
        <span className={"tag" + (on ? "" : " tag--dup")}>{on ? "On" : "Off"}</span>
      </div>
      <span className="muted service-panel__detail">{detail}</span>
      {children}
    </div>
  );
}

function ServicesCard({
  onMessage,
  onError,
}: Readonly<{
  onMessage: (m: string) => void;
  onError: (e: unknown) => void;
}>) {
  const qc = useQueryClient();
  const services = useQuery({ queryKey: ["services"], queryFn: getServices });
  const set = useMutation({
    mutationFn: updateServiceSettings,
    onSuccess: () => {
      onMessage("Services updated.");
      qc.invalidateQueries(); // FX recompute / AI / settings all may shift
    },
    onError,
  });
  const s = services.data;
  return (
    <div className="card">
      <h2 className="card__title">Services</h2>
      <p className="muted" style={{ marginTop: 0, fontSize: "0.85rem" }}>
        Each service has its own panel. The AI assistant stays off until you choose a mode and
        provider in the AI section below — but you can always turn it off here.
      </p>
      {!s && <p className="muted">Loading…</p>}
      {s && (
        <div className="service-grid">
          <ServicePanel title="AI assistant" detail={s.ai.detail} on={s.ai.enabled}>
            {s.ai.enabled ? (
              <button className="btn btn--sm btn--ghost" disabled={set.isPending} onClick={() => set.mutate({ privacy_mode: "no_ai" })}>
                Turn off
              </button>
            ) : (
              <span className="muted" style={{ fontSize: "0.8rem" }}>Choose a mode in the AI section below ↓</span>
            )}
          </ServicePanel>
          <ServicePanel title="Receipt OCR" detail={s.ocr.detail} on={s.ocr.enabled}>
            <ServiceToggle on={s.ocr.enabled} busy={set.isPending} onChange={(v) => set.mutate({ ocr_enabled: v })} />
          </ServicePanel>
          <ServicePanel title="Online exchange rates" detail={s.fx.detail} on={s.fx.enabled}>
            <ServiceToggle on={s.fx.enabled} busy={set.isPending} onChange={(v) => set.mutate({ fx_mode: v ? "frankfurter" : "manual" })} />
          </ServicePanel>
          <ServicePanel title="MQTT (Home Assistant)" detail={s.mqtt.detail} on={s.mqtt.enabled}>
            <span className="muted" style={{ fontSize: "0.8rem" }}>Read-only — set in the add-on options.</span>
          </ServicePanel>
        </div>
      )}
    </div>
  );
}

function LocationDefaultsCard({
  onMessage,
  onError,
}: Readonly<{
  onMessage: (m: string) => void;
  onError: (e: unknown) => void;
}>) {
  const qc = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const current = settings.data?.default_vendor_country || null;
  const save = useMutation({
    mutationFn: (code: string) => updateSettings({ default_vendor_country: code }),
    onSuccess: (_r, code) => {
      onMessage(code ? "Default vendor country saved." : "Default vendor country cleared.");
      qc.invalidateQueries({ queryKey: ["settings"] });
      qc.invalidateQueries({ queryKey: ["dash-geo"] }); // spend-by-location map uses it
    },
    onError,
  });

  return (
    <div className="card">
      <h2 className="card__title">Spending by location</h2>
      <p className="muted" style={{ marginTop: 0 }}>
        A default country for vendors that don't have one set — used by the spend-by-location map.
        It only fills the gap: a transaction's own country (a tagged trip) and a vendor's own country
        always take precedence, and this never overwrites a country you set manually.
      </p>
      <div className="form-row">
        <label>
          Default vendor country{" "}
          <CountrySelect
            value={current}
            onChange={(code) => save.mutate(code ?? "")}
            disabled={save.isPending}
            style={{ minWidth: 220 }}
            title="Country assumed for vendors with no country of their own"
          />
        </label>
      </div>
      <p className="muted" style={{ fontSize: "0.8rem" }}>
        Leave blank to fall back to inferring the country from each transaction's currency, as before.
      </p>
    </div>
  );
}

function MqttCard({
  onMessage,
  onError,
}: Readonly<{
  onMessage: (m: string) => void;
  onError: (e: unknown) => void;
}>) {
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
          <MqttSensorSelector onError={onError} />
        </>
      )}
    </div>
  );
}

// Pick what gets published to MQTT: turn off whole groups, or untick individual
// sensors. Each toggle saves immediately; disabling a sensor clears it from Home
// Assistant on the next publish (the backend removes its retained discovery).
function MqttSensorSelector({ onError }: Readonly<{ onError: (e: unknown) => void }>) {
  const qc = useQueryClient();
  const sel = useQuery({ queryKey: ["mqtt-sensors"], queryFn: getMqttSensors });
  const save = useMutation({
    mutationFn: (v: { groups: string[]; sensors: string[] }) => updateMqttSensors(v.groups, v.sensors),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["mqtt-sensors"] });
      qc.invalidateQueries({ queryKey: ["mqtt-status"] });
    },
    onError,
  });
  const data = sel.data;
  if (!data) return <p className="muted" style={{ fontSize: "0.82rem" }}>Loading sensors…</p>;
  if (data.groups.length === 0) return null;

  const disabledGroups = data.groups.filter((g) => g.disabled).map((g) => g.key);
  const disabledSensors = data.disabled_sensors;
  const toggleGroup = (key: string) =>
    save.mutate({
      groups: disabledGroups.includes(key) ? disabledGroups.filter((g) => g !== key) : [...disabledGroups, key],
      sensors: disabledSensors,
    });
  const toggleSensor = (key: string) =>
    save.mutate({
      groups: disabledGroups,
      sensors: disabledSensors.includes(key) ? disabledSensors.filter((s) => s !== key) : [...disabledSensors, key],
    });

  return (
    <div style={{ marginTop: 14 }}>
      <h3 style={{ margin: "0 0 4px", fontSize: "0.95rem" }}>Published sensors</h3>
      <p className="muted" style={{ marginTop: 0, fontSize: "0.8rem" }}>
        Choose what to publish — turn off a whole group, or untick individual sensors. Disabling one
        removes it from Home Assistant on the next publish.
      </p>
      {data.groups.map((g) => (
        <div key={g.key} style={{ marginBottom: 6 }}>
          <label className="checkbox" style={{ fontWeight: 600 }}>
            <input type="checkbox" checked={!g.disabled} disabled={save.isPending} onChange={() => toggleGroup(g.key)} />{" "}
            {g.label}
          </label>
          {!g.disabled && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: "2px 14px", paddingLeft: 22, marginTop: 2 }}>
              {data.sensors.filter((sensor) => sensor.group === g.key).map((sensor) => (
                <label key={sensor.key} className="checkbox" style={{ fontSize: "0.85rem" }}>
                  <input
                    type="checkbox"
                    checked={!disabledSensors.includes(sensor.key)}
                    disabled={save.isPending}
                    onChange={() => toggleSensor(sensor.key)}
                  />{" "}
                  {sensor.name.replace(/^Finance /, "")}
                </label>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function CurrencyFx({
  onMessage,
  onError,
}: Readonly<{
  onMessage: (m: string) => void;
  onError: (e: unknown) => void;
}>) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const settings = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const currencies = useQuery({ queryKey: ["currencies"], queryFn: getSupportedCurrencies });
  const rates = useQuery({ queryKey: ["fx-rates"], queryFn: listFxRates });
  const missing = useQuery({ queryKey: ["fx-missing"], queryFn: getMissingFx });
  const [rateDate, setRateDate] = useState("");
  const [quote, setQuote] = useState("");
  const [rate, setRate] = useState("");

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["settings"] });
    qc.invalidateQueries({ queryKey: ["fx-rates"] });
    qc.invalidateQueries({ queryKey: ["fx-missing"] });
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

  // Curated list, plus the current base if it's somehow not in the list (so the
  // dropdown always shows the actual value and never silently changes it).
  const options = currencies.data ?? [];
  const knownBase = options.some((c) => c.code === base);

  const chooseBase = async (code: string) => {
    if (!code || code === base) return;
    const c = options.find((o) => o.code === code);
    const label = c ? `${c.name} (${c.symbol})` : code;
    const ok = await confirm({
      message:
        `Change your base currency to ${code} — ${label}?\n\n` +
        "Every transaction's converted amount is recomputed for display using your " +
        "current FX rates / source. Your stored exchange rates are never rewritten — only " +
        "the currency shown changes.",
      confirmLabel: "Change",
    });
    if (ok) save.mutate({ base_currency: code });
  };

  return (
    <div className="card">
      <h2 className="card__title">Currency &amp; exchange rates</h2>
      <div className="form-row">
        <label>
          Base currency{" "}
          <select value={base} onChange={(e) => chooseBase(e.target.value)}>
            {!knownBase && <option value={base}>{base}</option>}
            {options.map((c) => (
              <option key={c.code} value={c.code}>
                {c.symbol} {c.code} — {c.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="muted">
        Amounts are stored in their original currency and converted to your base currency. Existing
        conversions are never rewritten — only missing ones are filled.
      </p>

      {/* Online-rates status lives here; the on/off switch is in Settings → Services. */}
      <div className="card" style={{ background: "var(--bg)", margin: "8px 0", padding: "10px 12px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span>
            <strong>Online rates:</strong>{" "}
            {mode === "frankfurter" ? (
              <span>On — Frankfurter (free ECB rates)</span>
            ) : (
              <span className="muted">Off — manual rates only (no network)</span>
            )}
            <br />
            <span className="muted" style={{ fontSize: "0.8rem" }}>
              {missing.data && missing.data.needs_rate > 0
                ? `${missing.data.needs_rate} transaction(s) still need a rate.`
                : "Every transaction has a rate."}{" "}
              Switch online rates on/off in Settings → Services.
            </span>
          </span>
          <button className="btn btn--sm" disabled={backfill.isPending} onClick={() => backfill.mutate()}>
            {(() => {
              if (backfill.isPending) return "Syncing…";
              return mode === "frankfurter" ? "Sync from Frankfurter" : "Fill from manual rates";
            })()}
          </button>
        </div>
      </div>

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
          Add manual rate
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

function IntegrationsCard({
  onMessage,
  onError,
}: Readonly<{
  onMessage: (m: string) => void;
  onError: (e: unknown) => void;
}>) {
  const qc = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const status = useQuery({ queryKey: ["paperless-status"], queryFn: getPaperlessStatus });
  const [edited, setEdited] = useState<string | null>(null);
  // The stored Settings URL ("" when relying on the env fallback); local edits override.
  const stored = settings.data?.paperless_url ?? "";
  const value = edited ?? stored;

  const save = useMutation({
    mutationFn: (u: string) => updateSettings({ paperless_url: u }),
    onSuccess: () => {
      onMessage("Paperless URL saved.");
      setEdited(null);
      qc.invalidateQueries({ queryKey: ["settings"] });
      qc.invalidateQueries({ queryKey: ["paperless-status"] });
    },
    onError,
  });

  const test = useMutation({
    mutationFn: testPaperlessConnection,
    onSuccess: (r) => onMessage(`Connected to Paperless at ${r.url}.`),
    onError,
  });

  const s = status.data;
  return (
    <div className="card">
      <h2 className="card__title">Integrations · Paperless-ngx</h2>
      <p className="muted" style={{ marginTop: 0 }}>
        Pull documents from your own Paperless-ngx into the Receipts pipeline. One-directional and
        outbound only — Paperless never receives your finance data.
      </p>

      <div className="form-row" style={{ alignItems: "flex-end", gap: 10, flexWrap: "wrap" }}>
        <label style={{ flex: "1 1 320px" }}>
          Paperless URL{" "}
          <input
            type="url"
            placeholder="http(s)://paperless.local:8000"
            value={value}
            onChange={(e) => setEdited(e.target.value)}
            style={{ width: "100%" }}
          />
        </label>
        <button className="btn" disabled={save.isPending || value.trim() === stored} onClick={() => save.mutate(value.trim())}>
          Save URL
        </button>
        <button className="btn btn--ghost" disabled={test.isPending || !s?.configured} onClick={() => test.mutate()}>
          {test.isPending ? "Testing…" : "Test connection"}
        </button>
      </div>

      {s && (
        <ul className="kv" style={{ marginTop: 10 }}>
          <li><span>Status</span><span>{s.configured ? "✅ Configured" : "⚠️ Not configured"}</span></li>
          <li><span>URL in use</span><span>{s.url ? `${s.url} (${s.url_source})` : "—"}</span></li>
          <li><span>API token</span><span>{s.token_present ? "✅ Set (via env)" : "❌ Missing"}</span></li>
        </ul>
      )}

      <PaperlessSetupNote />
    </div>
  );
}

// How the AI API key's presence + origin reads in the status list. Kept flat (no
// nested ternary, Sonar S3358) and never shows the key itself.
function apiKeyLabel(source: AIStatus["key_source"]): string {
  if (source === "env") return "Configured — environment override (HAFI_AI_API_KEY)";
  if (source === "stored") return "Configured — stored (encrypted in your local DB)";
  return "Not configured";
}

// Suffix appended to the Test-connection result so a failure makes clear WHICH
// key was tried — an expired env override masking a valid stored key is a common
// gotcha (#user). Derived from the already-fetched AI status key_source.
function keySourceHint(source: AIStatus["key_source"] | undefined): string {
  if (source === "env") return " (using environment key HAFI_AI_API_KEY)";
  if (source === "stored") return " (using stored key)";
  if (source === "none") return " (no key configured)";
  return "";
}

function AiCard({
  onMessage,
  onError,
}: Readonly<{
  onMessage: (m: string) => void;
  onError: (e: unknown) => void;
}>) {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["ai-status"], queryFn: getAiStatus });
  const settings = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [showDisclaimer, setShowDisclaimer] = useState(false);
  // Write-only API-key field: never pre-filled (the value is never sent back).
  const [keyInput, setKeyInput] = useState("");

  const saveKey = useMutation({
    mutationFn: (v: string) => updateSettings({ ai_api_key: v }),
    onSuccess: (_r, v) => {
      setKeyInput("");
      onMessage(v ? "AI API key saved (stored encrypted)." : "Stored AI API key cleared.");
      qc.invalidateQueries({ queryKey: ["ai-status"] });
    },
    onError,
  });

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

  // Probe the configured endpoint/key/model with a tiny request (#user).
  const [testMsg, setTestMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const test = useMutation({
    mutationFn: testAiConnection,
    onSuccess: (r) =>
      setTestMsg({ ok: r.ok, text: r.message + (r.ok && r.sample_category ? ` (sample → ${r.sample_category})` : "") + keySourceHint(status.data?.key_source) }),
    onError: (e) => setTestMsg({ ok: false, text: String(e) + keySourceHint(status.data?.key_source) }),
  });

  // Gate the first switch to a cloud mode behind a one-time disclaimer (#42).
  const handleSave = () => {
    const enablingCloud = (value("privacy_mode") || "").startsWith("cloud");
    if (enablingCloud && !isCloudAiAcknowledged()) {
      setShowDisclaimer(true);
      return;
    }
    save.mutate();
  };

  // Pre-fill sensible OpenAI defaults when the provider is chosen, so the user
  // doesn't have to type the Base URL/Model (blank fields leave AI "not
  // configured" — a real setup snag). Only fills when those fields are empty.
  const onProviderChange = (provider: string) => {
    const patch: Record<string, string> = { ai_provider: provider };
    if (provider === "openai_compatible") {
      if (!value("ai_base_url")) patch.ai_base_url = "https://api.openai.com/v1";
      if (!value("ai_model")) patch.ai_model = "gpt-4o-mini";
    }
    setDraft((d) => ({ ...d, ...patch }));
  };

  const st = status.data;
  const isCloud = (value("privacy_mode") || "").startsWith("cloud");

  return (
    <div className="card">
      <h2 className="card__title">AI assistant</h2>
      <p className="muted">
        <strong>Off by default.</strong> AI only ever <em>suggests</em> — it never changes a category on
        its own. Local mode keeps data on your device; cloud modes send a <strong>minimal, redacted</strong>{" "}
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
          <select value={value("ai_provider")} onChange={(e) => onProviderChange(e.target.value)}>
            <option value="none">none</option>
            <option value="openai_compatible">openai_compatible</option>
          </select>
        </label>
        <input
          placeholder="Base URL (e.g. https://api.openai.com/v1)"
          value={value("ai_base_url")}
          style={{ minWidth: 240 }}
          onChange={(e) => setDraft((d) => ({ ...d, ai_base_url: e.target.value }))}
        />
        <input
          placeholder="Model (e.g. gpt-4o-mini)"
          value={value("ai_model")}
          style={{ width: 140 }}
          onChange={(e) => setDraft((d) => ({ ...d, ai_model: e.target.value }))}
        />
        <button className="btn" disabled={Object.keys(draft).length === 0 || save.isPending} onClick={handleSave}>
          {save.isPending ? "Saving…" : "Save AI settings"}
        </button>
        <button
          className="btn btn--ghost"
          disabled={test.isPending}
          title="Send a tiny test request to the configured AI endpoint"
          onClick={() => { setTestMsg(null); test.mutate(); }}
        >
          {test.isPending ? "Testing…" : "Test connection"}
        </button>
      </div>
      {value("privacy_mode") === "local_llm" && (
        <p className="status status--warn" style={{ fontSize: "0.82rem" }}>
          ℹ️ The local-LLM path (Ollama / LM Studio / HA LLM) isn't something the author has been able
          to test — there's no local model here to try it against. It targets any OpenAI-compatible
          endpoint and should work, but if you run one, <strong>feedback and requirements are very welcome</strong>{" "}
          (open an issue on GitHub).
        </p>
      )}
      {testMsg && (
        <p className={"status " + (testMsg.ok ? "status--ok" : "status--error")}>
          {testMsg.ok ? "✅ " : "❌ "}{testMsg.text}
        </p>
      )}

      {showDisclaimer && (
        <CloudAiDisclaimerDialog
          onConfirm={() => {
            setCloudAiAcknowledged();
            setShowDisclaimer(false);
            save.mutate();
          }}
          onCancel={() => setShowDisclaimer(false)}
        />
      )}
      {st && (
        <ul className="kv" style={{ marginTop: 8 }}>
          <li><span>Status</span><span>{st.enabled ? "enabled" : "disabled"}</span></li>
          <li><span>Configured</span><span>{st.configured ? "yes" : "no (set provider + URL + model)"}</span></li>
          <li><span>API key</span><span>{apiKeyLabel(st.key_source)}</span></li>
        </ul>
      )}

      {/* API key management. Write-only: the value is stored encrypted at rest and
          never read back. The HAFI_AI_API_KEY env var, when set, always wins. */}
      <div className="form-row" style={{ flexWrap: "wrap", gap: 8, marginTop: 10, alignItems: "center" }}>
        <input
          type="password"
          name="ai-api-key"
          autoComplete="off"
          placeholder="API key (stored encrypted)"
          value={keyInput}
          style={{ minWidth: 240 }}
          onChange={(e) => setKeyInput(e.target.value)}
        />
        <button className="btn btn--sm" disabled={!keyInput || saveKey.isPending} onClick={() => saveKey.mutate(keyInput)}>
          {saveKey.isPending ? "Saving…" : "Set API key"}
        </button>
        <button
          className="btn btn--sm btn--ghost"
          disabled={saveKey.isPending || !st?.has_api_key || st?.key_source === "env"}
          title={st?.key_source === "env" ? "Set via the HAFI_AI_API_KEY environment variable" : "Remove the stored key"}
          onClick={() => saveKey.mutate("")}
        >
          Clear stored key
        </button>
      </div>
      <p className="muted" style={{ fontSize: "0.78rem" }}>
        On a standalone/local instance the key is stored <strong>encrypted at rest</strong> in your local database
        (and doubly protected when database encryption is on). The <code>HAFI_AI_API_KEY</code> environment variable
        always takes precedence{st?.key_source === "env" ? " — it is set, so any stored key is ignored" : ""}.
        {isCloud && <> <code>cloud_manual</code> asks you to approve each request; <code>cloud_auto</code> sends automatically.</>}
      </p>

      <p className="muted" style={{ fontSize: "0.78rem", marginTop: 14 }}>
        Every AI call is recorded — see the <strong>Logs</strong> page (🔑 Decisions / AI calls) for the
        full audit trail.
      </p>
    </div>
  );
}

// One-time backup/recovery codes for MFA (CR-FEAT-1). Rendered only when MFA is
// enabled. Generating is step-up gated (mirrors the RetentionForm pattern): a
// step_up_required error opens a code prompt whose success replays the request.
function MfaBackupCodesSection({
  onMessage,
  onError,
}: Readonly<{
  onMessage: (m: string) => void;
  onError: (e: unknown) => void;
}>) {
  const remaining = useQuery({
    queryKey: ["mfa-backup-codes-remaining"],
    queryFn: getMfaBackupCodesRemaining,
  });
  const [codes, setCodes] = useState<string[] | null>(null);
  const [stepUpOpen, setStepUpOpen] = useState(false);
  const [stepCode, setStepCode] = useState("");

  const generate = useMutation({
    mutationFn: generateMfaBackupCodes,
    onSuccess: (res) => {
      setCodes(res.codes);
      remaining.refetch().catch(() => {});
      onMessage("New backup codes generated — save them now, they won't be shown again.");
    },
    onError: (e: unknown) => {
      if (isStepUpError(e)) {
        setStepUpOpen(true);
        return;
      }
      onError(e);
    },
  });

  const stepUp = useMutation({
    mutationFn: () => mfaStepUp(stepCode),
    onSuccess: () => {
      setStepUpOpen(false);
      setStepCode("");
      generate.mutate();
    },
    onError: () => onError("That code didn't match. Try again."),
  });

  const codesText = codes ? codes.join("\n") : "";
  const copyCodes = () => {
    navigator.clipboard
      .writeText(codesText)
      .then(() => onMessage("Backup codes copied to clipboard."))
      .catch(() => onError("Couldn't copy — select the codes and copy them manually."));
  };
  const downloadCodes = () => {
    const url = URL.createObjectURL(new Blob([`${codesText}\n`], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "hafi-backup-codes.txt";
    a.click();
    URL.revokeObjectURL(url);
  };

  const remainingLabel = () => {
    if (remaining.isLoading) return "Checking remaining codes…";
    const n = remaining.data?.remaining ?? 0;
    if (n === 0) return "No backup codes yet — generate a set to keep as a fallback.";
    return `${n} unused backup code${n === 1 ? "" : "s"} remaining.`;
  };

  return (
    <div style={{ marginTop: 16, borderTop: "1px solid #2a2a2a", paddingTop: 12 }}>
      <h3 style={{ margin: "0 0 6px", fontSize: "0.95rem" }}>Backup codes</h3>
      <p className="muted" style={{ fontSize: "0.85rem", marginTop: 0 }}>
        One-time recovery codes to sign in if you lose your authenticator. Each works once.
        {" "}{remainingLabel()}
      </p>
      <button className="btn btn--ghost" disabled={generate.isPending} onClick={() => generate.mutate()}>
        {generate.isPending ? "Generating…" : "Generate backup codes"}
      </button>

      {codes && (
        <div style={{ marginTop: 12, border: "1px solid #2d7", borderRadius: 8, padding: 12 }}>
          <p className="status status--ok" style={{ marginTop: 0 }}>
            🔑 Here are your {codes.length} backup codes.
          </p>
          <p className="muted" style={{ fontSize: "0.82rem" }}>
            <strong>Save them now — they won't be shown again</strong>, and generating a new set
            invalidates these. Store them somewhere safe (a password manager).
          </p>
          <textarea
            readOnly
            aria-label="Backup codes"
            value={codesText}
            rows={Math.min(codes.length, 10)}
            onFocus={(e) => e.currentTarget.select()}
            style={{ width: "100%", fontFamily: "monospace", resize: "vertical", boxSizing: "border-box" }}
          />
          <div className="form-row" style={{ gap: 8, flexWrap: "wrap", marginTop: 8 }}>
            <button className="btn btn--ghost" onClick={copyCodes}>📋 Copy</button>
            <button className="btn btn--ghost" onClick={downloadCodes}>⬇ Download .txt</button>
            <button className="btn btn--ghost" onClick={() => setCodes(null)}>Done</button>
          </div>
        </div>
      )}

      {stepUpOpen && (
        <div className="card" style={{ borderLeft: "3px solid #2d7", marginTop: 12 }}>
          <h2 className="card__title">🔐 Confirm it's you</h2>
          <p className="muted">
            Generating backup codes needs a fresh two-factor code. Enter the current code —
            your codes will be generated automatically.
          </p>
          <form
            className="form-row"
            onSubmit={(e) => { e.preventDefault(); if (stepCode) stepUp.mutate(); }}
          >
            <input
              name="mfa-backup-stepup-code"
              autoComplete="one-time-code"
              inputMode="numeric"
              autoFocus
              placeholder="123456"
              maxLength={8}
              value={stepCode}
              onChange={(e) => setStepCode(e.target.value.replace(/\D/g, ""))}
              style={{ width: 120 }}
            />
            <button className="btn" type="submit" disabled={!stepCode || stepUp.isPending}>
              {stepUp.isPending ? "Verifying…" : "Verify"}
            </button>
            <button className="btn btn--ghost" type="button" onClick={() => { setStepUpOpen(false); setStepCode(""); }}>
              Cancel
            </button>
          </form>
        </div>
      )}
    </div>
  );
}

function MfaCard({
  onMessage,
  onError,
}: Readonly<{
  onMessage: (m: string) => void;
  onError: (e: unknown) => void;
}>) {
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: getMe });
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [scope, setScope] = useState("app_admin"); // what MFA gates (#157)
  // MFA errors (wrong/expired code) render INLINE next to the code input rather
  // than via the page-level onError banner at the very top of Settings, which
  // sits far above this card and reads as "nothing happened".
  const [mfaErr, setMfaErr] = useState<string | null>(null);

  const begin = useMutation({
    mutationFn: mfaSetup,
    onSuccess: (s) => { setMfaErr(null); setSetup(s); },
    onError: (e) => setMfaErr(String(e)),
  });
  // Enabling MFA is a two-step chain: mfaEnable turns MFA on but does NOT mint a
  // session token; mfaVerify (with the same code) is what mints the app-entry
  // session (setSessionToken). We MUST mint the token BEFORE invalidating queries
  // — otherwise the mass refetch fires with no session token, every protected
  // query 403s, and /api/users/me briefly reports mfa_required, bouncing the whole
  // app to the entry gate (unmounting Settings + the backup-codes section). So we
  // fold verify into the mutation and invalidate only in onSettled, mirroring
  // App.tsx's MfaSetupGate.
  const enable = useMutation({
    mutationFn: async () => {
      await mfaEnable(code, scope);
      // If the TOTP period rolled over between enable and verify this throws —
      // MFA is already on and the pending secret is consumed, so onSettled's
      // refetch flips me to mfa_required and the app-entry gate takes a fresh code.
      await mfaVerify(code);
    },
    onSuccess: () => {
      setSetup(null);
      setCode("");
      setMfaErr(null);
      onMessage("Two-factor authentication enabled.");
    },
    onError: (e) => { setCode(""); setMfaErr(String(e)); },
    onSettled: () => qc.invalidateQueries(),
  });
  const disable = useMutation({
    mutationFn: () => mfaDisable(code),
    onSuccess: () => {
      setCode("");
      setMfaErr(null);
      onMessage("Two-factor authentication disabled.");
      qc.invalidateQueries();
    },
    onError: (e) => { setCode(""); setMfaErr(String(e)); },
  });

  // Drop this device's MFA session so the entry challenge re-appears immediately —
  // lets the user prove MFA works + re-lock on demand (the session is otherwise
  // reused until the app context is closed).
  const lockNow = () => {
    setSessionToken(null);
    onMessage("Locked — enter your code to continue.");
    qc.invalidateQueries();
  };

  const enabled = me.data?.mfa_enabled ?? false;

  return (
    <div className="card">
      <h2 className="card__title">Two-factor authentication (MFA)</h2>
      <p className="muted">
        Optional second factor for <strong>your</strong> account, on top of Home Assistant login.
        When on, you enter a 6-digit code from an authenticator app (Google Authenticator, Aegis,
        1Password…) when you <strong>open this app fresh</strong>, and again to confirm admin actions.
        Codes are time-based (TOTP) and never leave your device. <em>You won't be re-prompted on every
        page within a session — use <strong>Lock now</strong> below to require a code immediately.</em>
      </p>

      {!enabled && !setup && (
        <>
          <button className="btn" disabled={begin.isPending} onClick={() => begin.mutate()}>
            {begin.isPending ? "Preparing…" : "Set up two-factor"}
          </button>
          {mfaErr && <p className="status status--error">{mfaErr}</p>}
        </>
      )}

      {!enabled && setup && (
        <>
          <p className="muted">
            Add this to your authenticator app — <strong>scan the QR code</strong> below, or enter
            the secret by hand — then type the current 6-digit code to confirm.
          </p>
          {/* The QR encodes the otpauth:// URI the server returned; nothing leaves the device. */}
          <div style={{ background: "#fff", padding: 12, borderRadius: 8, display: "inline-block" }}>
            <QRCodeSVG value={setup.otpauth_uri} size={176} />
          </div>
          <p className="muted" style={{ fontSize: "0.82rem", marginTop: 8 }}>
            Can't scan? Enter the secret manually:
          </p>
          <ul className="kv">
            <li><span>Secret</span><span style={{ fontFamily: "monospace" }}>{setup.secret}</span></li>
            <li><span>otpauth URI</span><span style={{ fontFamily: "monospace", wordBreak: "break-all" }}>{setup.otpauth_uri}</span></li>
          </ul>
          <fieldset style={{ border: "1px solid #2a2a2a", borderRadius: 8, padding: "8px 12px", margin: "10px 0" }}>
            <legend className="muted" style={{ fontSize: "0.82rem", padding: "0 6px" }}>When should it ask for a code?</legend>
            <label className="checkbox" style={{ display: "block", marginBottom: 4 }}>
              <input type="radio" name="mfa-scope" checked={scope === "app_admin"} onChange={() => setScope("app_admin")} />{" "}
              <strong>Opening the app + confirming admin actions</strong> (recommended)
            </label>
            <label className="checkbox" style={{ display: "block" }}>
              <input type="radio" name="mfa-scope" checked={scope === "app"} onChange={() => setScope("app")} />{" "}
              <strong>Opening the app only</strong> (no extra prompt for admin actions)
            </label>
          </fieldset>
          <p className="muted" style={{ fontSize: "0.85rem", marginBottom: 4 }}>
            Enter the current 6-digit code, then <strong>Confirm &amp; enable</strong>. (The button
            stays greyed until you type a code.)
          </p>
          <div className="form-row">
            <input
              name="mfa-enable-code"
              autoComplete="one-time-code"
              inputMode="numeric"
              placeholder="123456"
              maxLength={8}
              value={code}
              onChange={(e) => { setMfaErr(null); setCode(e.target.value.replace(/\D/g, "")); }}
              style={{ width: 120 }}
            />
            <button className="btn" disabled={!code || enable.isPending} onClick={() => enable.mutate()}>
              {enable.isPending ? "Confirming…" : "Confirm & enable"}
            </button>
            <button className="btn btn--ghost" onClick={() => { setSetup(null); setCode(""); setMfaErr(null); }}>
              Cancel
            </button>
          </div>
          {mfaErr && <p className="status status--error">{mfaErr}</p>}
        </>
      )}

      {enabled && (
        <>
          <p className="status status--ok">🔐 Two-factor is enabled for your account.</p>
          <p className="muted" style={{ fontSize: "0.85rem", marginTop: 0 }}>
            Asks for a code: <strong>{me.data?.mfa_scope === "app" ? "opening the app" : "opening the app + admin actions"}</strong>.
            {me.data?.mfa_policy === "required" && " Required by an administrator."}
            {" "}To change the scope, disable and re-enable.
          </p>
          <div className="form-row" style={{ gap: 8, flexWrap: "wrap" }}>
            <button className="btn btn--ghost" onClick={lockNow}>🔒 Lock now (require a code)</button>
          </div>
          {/* A required-MFA user can't turn it off (the server rejects it), so don't
              offer a disable control that would only fail. */}
          {me.data?.mfa_policy === "required" && (
            <p className="muted" style={{ fontSize: "0.85rem", marginTop: 12 }}>
              Two-factor is required by an administrator, so it can't be turned off here.
            </p>
          )}
          {me.data?.mfa_policy !== "required" && (
            <>
              <p className="muted" style={{ fontSize: "0.85rem", marginTop: 12, marginBottom: 4 }}>
                To turn it off, enter the <strong>current 6-digit code</strong> from your authenticator.
                (The button stays greyed until you type a code.)
              </p>
              <div className="form-row">
                <input
                  name="mfa-disable-code"
                  autoComplete="one-time-code"
                  inputMode="numeric"
                  placeholder="123456"
                  maxLength={8}
                  value={code}
                  onChange={(e) => { setMfaErr(null); setCode(e.target.value.replace(/\D/g, "")); }}
                  style={{ width: 150 }}
                />
                <button className="btn btn--ghost" disabled={!code || disable.isPending} onClick={() => disable.mutate()}>
                  {disable.isPending ? "Disabling…" : "Disable two-factor"}
                </button>
              </div>
              {mfaErr && <p className="status status--error">{mfaErr}</p>}
            </>
          )}
          <MfaBackupCodesSection onMessage={onMessage} onError={onError} />
        </>
      )}
    </div>
  );
}

function LoggingCard({
  onMessage,
  onError,
}: Readonly<{
  onMessage: (m: string) => void;
  onError: (e: unknown) => void;
}>) {
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: getMe });
  const settings = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const isAdmin = me.data?.is_admin === true;
  const save = useMutation({
    mutationFn: (level: string) => updateSettings({ log_level: level }),
    onSuccess: () => {
      onMessage("Log level updated.");
      qc.invalidateQueries({ queryKey: ["settings"] });
    },
    onError,
  });
  // Optimistic overlay for the log-level select (FE-8): show the chosen level at
  // once and revert on failure. `save` keeps its own onError. Singleton, keyed by
  // a constant.
  const levelSelect = useOptimisticSelect<string, string>();
  if (me.data && !isAdmin) return null; // owner-only
  const level = settings.data?.log_level ?? "INFO";
  return (
    <div className="card">
      <h2 className="card__title">Logging</h2>
      <div className="form-row">
        <label>
          Log level{" "}
          <select
            value={levelSelect.valueFor("level", level)}
            onChange={(e) => {
              const next = e.target.value;
              levelSelect.choose("level", next, () => save.mutateAsync(next));
            }}
          >
            {["DEBUG", "INFO", "WARNING", "ERROR"].map((l) => (
              <option key={l} value={l}>{l}</option>
            ))}
          </select>
        </label>
      </div>
      <p className="muted">
        How much detail the app logs to stdout (the Home Assistant add-on Log panel). Takes effect
        immediately. DEBUG is verbose for troubleshooting; INFO is the sensible default. (The demo
        defaults to DEBUG.)
      </p>
    </div>
  );
}

function SecurityHealthCard({ onError }: Readonly<{ onError: (e: unknown) => void }>) {
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: getMe });
  const isAdmin = me.data?.is_admin === true;
  const health = useQuery({
    queryKey: ["security-health"],
    queryFn: getSecurityHealth,
    enabled: isAdmin,
  });
  const dismiss = useMutation({
    mutationFn: (v: { id: string; snooze_days?: number; clear?: boolean }) =>
      dismissSecurityCheck(v.id, { snooze_days: v.snooze_days, clear: v.clear }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["security-health"] }),
    onError,
  });

  if (me.data && !isAdmin) return null; // owner-only panel

  const checks = health.data?.checks ?? [];
  const icon = (s: string) => {
    if (s === "warn") return "⚠️";
    if (s === "info") return "ℹ️";
    return "✅";
  };
  const colour = (s: string) => {
    if (s === "warn") return "#e05555";
    if (s === "info") return "#e0a800";
    return "#3aa55a";
  };

  return (
    <div className="card">
      <h2 className="card__title">Security health</h2>
      <p className="muted">
        Which protections are on, and what you could improve. These are recommendations only —
        dismiss or snooze anything you don't want to be reminded about.
      </p>
      {health.isLoading && <p className="muted">Loading…</p>}
      {health.data?.active_count === 0 && (
        <p className="status status--ok">✅ No outstanding security recommendations.</p>
      )}
      {checks.length > 0 && (
        <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 10 }}>
          {checks.map((c) => (
            <li
              key={c.id}
              style={{
                borderLeft: `3px solid ${colour(c.severity)}`,
                paddingLeft: 10,
                opacity: c.dismissed ? 0.55 : 1,
              }}
            >
              <div>
                <strong>{icon(c.severity)} {c.title}</strong>
                {c.dismissed && (
                  <span className="muted">
                    {" "}· {c.snoozed_until ? `snoozed until ${c.snoozed_until.slice(0, 10)}` : "dismissed"}
                  </span>
                )}
              </div>
              <div className="muted" style={{ fontSize: "0.85rem" }}>{c.recommendation}</div>
              {c.severity !== "ok" && (
                <div style={{ marginTop: 4, fontSize: "0.85rem" }}>
                  {c.dismissed ? (
                    <button className="link-btn" onClick={() => dismiss.mutate({ id: c.id, clear: true })}>
                      restore
                    </button>
                  ) : (
                    <>
                      <button className="link-btn" onClick={() => dismiss.mutate({ id: c.id, snooze_days: 7 })}>
                        remind me in 7 days
                      </button>
                      {" · "}
                      <button className="link-btn" onClick={() => dismiss.mutate({ id: c.id })}>
                        dismiss
                      </button>
                    </>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SecurityCard({
  onMessage,
  onError,
}: Readonly<{
  onMessage: (m: string) => void;
  onError: (e: unknown) => void;
}>) {
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

      {s?.encryption_available && !s.encryption_enabled && (
        <>
          <p className="muted">
            Encrypt the database on disk so nothing but this app (with your passphrase) can read it.{" "}
            <strong>If you lose the passphrase, the data is unrecoverable.</strong>
          </p>
          <div className="form-row">
            <input
              type="password"
              name="enable-encryption-passphrase"
              autoComplete="new-password"
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
              Stored mode saves the passphrase on this device so the database unlocks automatically on
              every restart, with nothing to type. On the Home Assistant add-on the key instead comes from
              the <code>db_key</code> option (its <strong>Configuration</strong> tab), which always takes
              precedence. This is a convenience, not protection against someone who has your disk: the key
              sits next to the database. Choose <strong>Prompt me</strong> for the strongest at-rest
              protection, where nothing is stored.
            </p>
          )}
        </>
      )}

      {s?.encryption_enabled && (
        <>
          <p className="status status--ok">
            🔒 Encrypted · unlock mode: {s.unlock_mode}
          </p>
          {s.unlock_mode === "stored" && !s.stored_key_present && (
            <p className="status status--warn">
              ⚠️ Stored unlock mode is selected but no key is configured, so the database will lock
              again on the next restart. Set <code>db_key</code> in the add-on’s <strong>Configuration</strong>{" "}
              tab (or the <code>HAFI_DB_KEY</code> environment variable) to the passphrase you encrypted with.
            </p>
          )}
          {s.unlock_mode === "stored" && s.stored_key_present && (
            <p className="muted" style={{ fontSize: "0.78rem" }}>
              {s.stored_key_source === "env"
                ? "The auto-unlock key is provided by the HAFI_DB_KEY option, so the database unlocks on every restart."
                : "The passphrase is saved on this device, so the database unlocks automatically on every restart. This is convenience, not protection against disk theft; disable encryption to remove the saved key."}
            </p>
          )}
          <div className="form-row">
            <input
              type="password"
              name="disable-encryption-passphrase"
              autoComplete="current-password"
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

const RETENTION_LABELS: Record<string, string> = {
  ai_requests: "AI request logs",
  audit_logs: "Activity (audit) logs",
  receipts: "Receipt files",
  failed_unlock: "Failed-unlock records",
};

function RetentionCard({
  onMessage,
  onError,
}: Readonly<{
  onMessage: (m: string) => void;
  onError: (e: unknown) => void;
}>) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const me = useQuery({ queryKey: ["me"], queryFn: getMe });
  const isAdmin = me.data?.is_admin === true;
  const policy = useQuery({
    queryKey: ["retention-policy"],
    queryFn: getRetentionPolicy,
    enabled: isAdmin,
  });

  // Re-syncs when the server policy changes (save/refetch) instead of seeding once
  // and going stale (FE-7). React Query's structural sharing keeps the reference
  // stable across no-op refetches, so in-progress edits aren't clobbered.
  const [draft, setDraft] = useServerState<RetentionPolicyResponse | null>(policy.data ?? null);
  const [plan, setPlan] = useState<RetentionPlan | null>(null);

  // Admin actions can be challenged for a fresh MFA code (#124); replay on success.
  const lastAction = useRef<(() => void) | null>(null);
  const [stepUpOpen, setStepUpOpen] = useState(false);
  const [stepCode, setStepCode] = useState("");

  const handleError = (e: unknown) => {
    if (isStepUpError(e)) {
      setStepUpOpen(true);
      return;
    }
    onError(e);
  };

  const save = useMutation({
    mutationFn: () =>
      updateRetentionPolicy({
        policy: draft!.policy,
        receipt_delete_after_processing: draft!.receipt_delete_after_processing,
        backup_trim: draft!.backup_trim,
      }),
    onSuccess: (resp) => {
      setDraft(resp);
      onMessage("Retention settings saved.");
      qc.invalidateQueries({ queryKey: ["retention-policy"] });
      previewRetention().then(setPlan).catch(() => {});
    },
    onError: handleError,
  });

  const run = useMutation({
    mutationFn: runRetention,
    onSuccess: (r) => {
      const archived = Object.values(r.counts).reduce((n, c) => n + c.archived, 0);
      const purged = Object.values(r.counts).reduce((n, c) => n + c.purged, 0);
      onMessage(
        `Cleanup done — archived ${archived}, purged ${purged}.` +
          (r.backup_taken ? " A safety backup was taken first." : ""),
      );
      previewRetention().then(setPlan).catch(() => {});
      // Aged-out rows may have vanished from the log/receipt views.
      qc.invalidateQueries({ queryKey: ["activity-log"] });
      qc.invalidateQueries({ queryKey: ["ai-requests"] });
      qc.invalidateQueries({ queryKey: ["receipts"] });
      qc.invalidateQueries({ queryKey: ["security-health"] });
    },
    onError: handleError,
  });

  const stepUp = useMutation({
    mutationFn: () => mfaStepUp(stepCode),
    onSuccess: () => {
      setStepUpOpen(false);
      setStepCode("");
      lastAction.current?.();
    },
    onError: () => onError("That code didn't match. Try again."),
  });

  const doSave = () => {
    lastAction.current = () => save.mutate();
    save.mutate();
  };
  const doRun = async () => {
    if (!(await confirm({
      message:
        "Run data cleanup now? Archiving is reversible, but PURGING permanently deletes aged-out " +
        "data. A timestamped safety backup is taken before any purge. Continue?",
      confirmLabel: "Run cleanup",
      danger: true,
    }))) return;
    lastAction.current = () => run.mutate();
    run.mutate();
  };

  if (me.data && !isAdmin) return null; // owner-only

  const setField = (dtype: string, field: keyof RetentionTypePolicy, value: number | boolean | null) =>
    setDraft((d) =>
      d ? { ...d, policy: { ...d.policy, [dtype]: { ...d.policy[dtype], [field]: value } } } : d,
    );

  const setTrim = (field: keyof BackupTrim, value: number) =>
    setDraft((d) => (d ? { ...d, backup_trim: { ...d.backup_trim, [field]: value } } : d));

  const daysValue = (n: number | null | undefined) => (n === null || n === undefined ? "" : String(n));
  const parseDays = (v: string): number | null => {
    const t = v.trim();
    if (t === "") return null;
    const n = Number.parseInt(t, 10);
    return Number.isNaN(n) ? null : Math.max(0, n);
  };

  return (
    <div className="card">
      <h2 className="card__title">Data retention</h2>
      <p className="muted">
        Age out old data on your own schedule. For each type you can <strong>archive after</strong> a
        number of days (reversible — hidden from view, kept) and/or <strong>purge after</strong> a
        number of days (permanent delete). Leave a box blank to turn that stage off. Everything is
        off by default. Archiving runs automatically on startup; purging only runs when you click{" "}
        <em>Run cleanup now</em> below — unless you tick <strong>auto-purge</strong> for a type.
      </p>

      {!draft && <p className="muted">Loading…</p>}

      {draft && (
        <>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Data</th><th>Archive after (days)</th><th>Purge after (days)</th><th>Auto-purge</th>
                </tr>
              </thead>
              <tbody>
                {draft.data_types.map((dtype) => {
                  const pol = draft.policy[dtype] ?? {};
                  const archivable = draft.archivable.includes(dtype);
                  return (
                    <tr key={dtype}>
                      <td>{RETENTION_LABELS[dtype] ?? dtype}</td>
                      <td>
                        {archivable ? (
                          <input
                            inputMode="numeric"
                            placeholder="off"
                            value={daysValue(pol.archive_after_days)}
                            style={{ width: 80 }}
                            onChange={(e) => setField(dtype, "archive_after_days", parseDays(e.target.value))}
                          />
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                      <td>
                        <input
                          inputMode="numeric"
                          placeholder="off"
                          value={daysValue(pol.purge_after_days)}
                          style={{ width: 80 }}
                          onChange={(e) => setField(dtype, "purge_after_days", parseDays(e.target.value))}
                        />
                      </td>
                      <td style={{ textAlign: "center" }}>
                        <input
                          type="checkbox"
                          checked={pol.auto_purge ?? false}
                          onChange={(e) => setField(dtype, "auto_purge", e.target.checked)}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <label className="muted" style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 12 }}>
            <input
              type="checkbox"
              checked={draft.receipt_delete_after_processing}
              onChange={(e) => setDraft((d) => (d ? { ...d, receipt_delete_after_processing: e.target.checked } : d))}
            />{" "}
            Delete a receipt's original file once it's processed &amp; matched (keeps the extracted fields)
          </label>

          <h3 style={{ margin: "16px 0 6px", fontSize: "0.95rem" }}>Safety-backup limits</h3>
          <p className="muted" style={{ fontSize: "0.82rem", marginTop: 0 }}>
            A timestamped backup is taken before every purge. These limits keep that history from
            growing without bound (the most recent few are always kept).
          </p>
          <div className="form-row" style={{ gap: 12, flexWrap: "wrap" }}>
            <label>
              Max age (days){" "}
              <input
                inputMode="numeric"
                value={String(draft.backup_trim.max_age_days)}
                style={{ width: 80 }}
                onChange={(e) => setTrim("max_age_days", Math.max(1, Number.parseInt(e.target.value, 10) || 1))}
              />
            </label>
            <label>
              Max total (MB){" "}
              <input
                inputMode="numeric"
                value={String(draft.backup_trim.max_total_mb)}
                style={{ width: 80 }}
                onChange={(e) => setTrim("max_total_mb", Math.max(1, Number.parseInt(e.target.value, 10) || 1))}
              />
            </label>
            <label>
              Always keep last{" "}
              <input
                inputMode="numeric"
                value={String(draft.backup_trim.min_keep)}
                style={{ width: 80 }}
                onChange={(e) => setTrim("min_keep", Math.max(1, Number.parseInt(e.target.value, 10) || 1))}
              />
            </label>
          </div>

          <div className="form-row" style={{ marginTop: 14, gap: 8 }}>
            <button className="btn" disabled={save.isPending} onClick={doSave}>
              {save.isPending ? "Saving…" : "Save retention settings"}
            </button>
            <button
              className="btn btn--ghost"
              onClick={() => previewRetention().then(setPlan).catch(onError)}
            >
              Preview removal plan
            </button>
            <button className="btn btn--danger" disabled={run.isPending} onClick={doRun}>
              {run.isPending ? "Running…" : "Run cleanup now"}
            </button>
          </div>

          {plan && <RetentionPlanView plan={plan} types={draft.data_types} />}
        </>
      )}

      {stepUpOpen && (
        <div className="card" style={{ borderLeft: "3px solid #2d7", marginTop: 12 }}>
          <h2 className="card__title">🔐 Confirm it's you</h2>
          <p className="muted">
            Changing retention or running a purge needs a fresh two-factor code. Enter the current
            code — your last action will run automatically.
          </p>
          <form
            className="form-row"
            onSubmit={(e) => { e.preventDefault(); if (stepCode) stepUp.mutate(); }}
          >
            <input
              name="mfa-retention-stepup-code"
              autoComplete="one-time-code"
              inputMode="numeric"
              autoFocus
              placeholder="123456"
              maxLength={8}
              value={stepCode}
              onChange={(e) => setStepCode(e.target.value.replace(/\D/g, ""))}
              style={{ width: 120 }}
            />
            <button className="btn" type="submit" disabled={!stepCode || stepUp.isPending}>
              {stepUp.isPending ? "Verifying…" : "Verify"}
            </button>
            <button className="btn btn--ghost" type="button" onClick={() => { setStepUpOpen(false); setStepCode(""); }}>
              Cancel
            </button>
          </form>
        </div>
      )}
    </div>
  );
}

function RetentionPlanView({ plan, types }: Readonly<{ plan: RetentionPlan; types: string[] }>) {
  const rows = types
    .map((t) => ({ t, p: plan[t] as RetentionTypePlan }))
    .filter((r) => r.p && (r.p.archive_due > 0 || r.p.purge_due > 0));

  return (
    <div style={{ marginTop: 12 }}>
      <h3 style={{ margin: "0 0 6px", fontSize: "0.95rem" }}>Removal plan (right now)</h3>
      {plan.pending_purge > 0 && (
        <p className="status status--warn">
          {plan.pending_purge} item(s) are past their purge age and awaiting your confirmation.
        </p>
      )}
      {rows.length === 0 ? (
        <p className="muted">Nothing is due for archive or purge right now.</p>
      ) : (
        <ul className="kv">
          {rows.map(({ t, p }) => (
            <li key={t}>
              <span>{RETENTION_LABELS[t] ?? t}</span>
              <span>
                {p.archive_due > 0 ? `${p.archive_due} to archive` : ""}
                {p.archive_due > 0 && p.purge_due > 0 ? " · " : ""}
                {p.purge_due > 0 ? `${p.purge_due} to purge` : ""}
                {p.purge_due > 0 && p.auto_purge ? " (auto)" : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
