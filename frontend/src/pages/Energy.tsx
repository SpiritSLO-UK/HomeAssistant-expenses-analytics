import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Sparkline from "../components/Sparkline";
import {
  getEnergyHistory,
  getEnergyOffset,
  getEnergyProductionHistory,
  getEnergyStatus,
  getMe,
  listCategories,
  updateEnergyConfig,
} from "../api/client";

const HISTORY_RANGES = [
  { period: "day", label: "Daily", count: 30 },
  { period: "month", label: "Monthly", count: 12 },
  { period: "year", label: "Yearly", count: 5 },
];

const SOURCES = [
  { value: "off", label: "Off (no offset)" },
  { value: "ha_api", label: "Home Assistant API — read named entities" },
  { value: "mqtt", label: "MQTT — read topics" },
];

const SOURCE_LABEL: Record<string, string> = {
  off: "Off",
  ha_api: "Home Assistant API",
  mqtt: "MQTT",
};

const PRICE_SOURCE_LABEL: Record<string, string> = {
  tariff: "your tariff",
  derived: "derived from meter readings",
  none: "not set",
};

export default function Energy() {
  const me = useQuery({ queryKey: ["me"], queryFn: getMe });
  const offset = useQuery({ queryKey: ["energy-offset"], queryFn: () => getEnergyOffset() });
  const canManage = me.data?.can_manage_settings ?? false;
  const o = offset.data;
  const base = o?.currency ?? "GBP";

  return (
    <div className="page">
      <div className="page__head">
        <h1 className="page__title">⚡ Energy cost offset</h1>
        <button className="btn" onClick={() => offset.refetch()} disabled={offset.isFetching}>
          {offset.isFetching ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      <p className="muted">
        Net the energy you <strong>produce</strong> (solar/grid, read live from Home Assistant)
        against what you <strong>spend</strong> on your energy bill, to see your production's effect
        on the cost. Off by default — point it at your HA sensors (or MQTT topics) below.
      </p>

      {o && !o.configured && (
        <p className="status">
          Energy offset is <strong>off</strong>.{" "}
          {canManage ? "Pick a source in the settings below to switch it on." : "Ask an admin to enable it."}
        </p>
      )}

      {o && o.configured && (
        <div className="card">
          <h2 className="card__title">This month ({o.month})</h2>
          {!o.available && (
            <p className="status status--warn">
              Source <strong>{SOURCE_LABEL[o.source] ?? o.source}</strong> isn't reachable right now
              {o.source === "ha_api" ? " (needs the add-on's Home Assistant API access)" : " (MQTT is off)"}.
            </p>
          )}
          <ul className="kv">
            <li><span>Produced</span><span>{o.produced_kwh} kWh</span></li>
            <li>
              <span>Unit price</span>
              <span>
                {o.unit_price ? `${o.unit_price} ${base}/kWh` : "—"}{" "}
                <span className="muted">
                  ({PRICE_SOURCE_LABEL[o.unit_price_source] ?? o.unit_price_source})
                </span>
              </span>
            </li>
            <li><span>Saving from production</span><span><strong>{o.saving} {base}</strong></span></li>
            <li><span>Energy bill (spend)</span><span>{o.energy_spend} {base}</span></li>
            <li><span>Net effective cost</span><span><strong>{o.net_cost} {base}</strong></span></li>
          </ul>
          {o.unit_price_source === "none" && (
            <p className="muted" style={{ fontSize: "0.8rem" }}>
              Set a tariff (£/kWh) below, or log a couple of Home electricity meter readings with costs
              (Cars &amp; assets) so the price can be derived — otherwise the saving shows as 0.
            </p>
          )}
        </div>
      )}

      <EnergyHistoryCard currency={base} />

      {o && o.configured && <ProductionTrendCard currency={base} />}

      {canManage && <EnergyConfigCard />}
    </div>
  );
}

function EnergyHistoryCard({ currency }: Readonly<{ currency: string }>) {
  const [range, setRange] = useState(HISTORY_RANGES[1]); // monthly default
  const q = useQuery({
    queryKey: ["energy-history", range.period, range.count],
    queryFn: () => getEnergyHistory(range.period, range.count),
  });
  const buckets = q.data?.buckets ?? [];
  const values = buckets.map((b) => Number(b.spend));
  const total = values.reduce((a, b) => a + b, 0);
  const hasData = values.some((v) => v > 0);
  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <h2 className="card__title" style={{ margin: 0 }}>Energy bill over time</h2>
        <div style={{ display: "flex", gap: 6 }}>
          {HISTORY_RANGES.map((r) => (
            <button
              key={r.period}
              className={"btn btn--sm" + (range.period === r.period ? "" : " btn--ghost")}
              onClick={() => setRange(r)}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>
      {hasData ? (
        <>
          <Sparkline values={values} color="#f59e0b" width={560} height={120} />
          <p className="muted" style={{ marginBottom: 0 }}>
            {buckets[0].label} – {buckets[buckets.length - 1].label} · total {currency}{" "}
            {total.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </p>
        </>
      ) : (
        <p className="muted">
          No energy-bill spend to chart yet. Set your <strong>energy-bill category</strong> below — your
          imported statement history then fills this in (daily / monthly / yearly).
        </p>
      )}
    </div>
  );
}

function ProductionTrendCard({ currency }: Readonly<{ currency: string }>) {
  const [range, setRange] = useState(HISTORY_RANGES[1]); // monthly default
  const q = useQuery({
    queryKey: ["energy-production-history", range.period, range.count],
    queryFn: () => getEnergyProductionHistory(range.period, range.count),
  });
  const buckets = q.data?.buckets ?? [];
  const produced = buckets.map((b) => Number(b.produced_kwh));
  const totalProduced = produced.reduce((a, b) => a + b, 0);
  const totalSaving = buckets.reduce((a, b) => a + Number(b.saving), 0);
  const hasData = produced.some((v) => v > 0);
  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <h2 className="card__title" style={{ margin: 0 }}>Production &amp; saving over time</h2>
        <div style={{ display: "flex", gap: 6 }}>
          {HISTORY_RANGES.map((r) => (
            <button
              key={r.period}
              className={"btn btn--sm" + (range.period === r.period ? "" : " btn--ghost")}
              onClick={() => setRange(r)}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>
      {hasData ? (
        <>
          <Sparkline values={produced} color="#22c55e" width={560} height={120} />
          <p className="muted" style={{ marginBottom: 0 }}>
            {buckets[0].label} – {buckets[buckets.length - 1].label} · produced{" "}
            {totalProduced.toLocaleString(undefined, { maximumFractionDigits: 2 })} kWh · saved {currency}{" "}
            {totalSaving.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </p>
        </>
      ) : (
        <p className="muted">
          No production captured yet. Production is sampled when this page reads your source — open it
          (or refresh) over time and the trend fills in. Set the sensor type below (cumulative total vs.
          per-interval) so the maths matches your sensor.
        </p>
      )}
    </div>
  );
}

function lines(text: string): string[] {
  return text
    .split(/[\n,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function EnergyConfigCard() {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["energy-status"], queryFn: getEnergyStatus });
  const cats = useQuery({ queryKey: ["categories"], queryFn: listCategories });
  const [msg, setMsg] = useState<string | null>(null);

  const [source, setSource] = useState("off");
  const [entities, setEntities] = useState("");
  const [topics, setTopics] = useState("");
  const [tariff, setTariff] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [semantics, setSemantics] = useState("cumulative");

  // Seed the form from the saved config once it loads.
  useEffect(() => {
    const s = status.data;
    if (!s) return;
    setSource(s.source);
    setEntities(s.production_entities.join("\n"));
    setTopics(s.production_topics.join("\n"));
    setTariff(s.tariff_per_kwh);
    setCategoryId(s.energy_category_id != null ? String(s.energy_category_id) : "");
    setSemantics(s.production_semantics);
  }, [status.data]);

  const save = useMutation({
    mutationFn: () =>
      updateEnergyConfig({
        source,
        production_entities: lines(entities),
        production_topics: lines(topics),
        tariff_per_kwh: tariff.trim(),
        energy_category_id: categoryId ? Number(categoryId) : null,
        production_semantics: semantics,
      }),
    onSuccess: () => {
      setMsg("Saved.");
      qc.invalidateQueries({ queryKey: ["energy-offset"] });
      qc.invalidateQueries({ queryKey: ["energy-status"] });
      qc.invalidateQueries({ queryKey: ["energy-production-history"] });
    },
    onError: (e) => setMsg(String(e)),
  });

  const s = status.data;

  return (
    <div className="card">
      <h2 className="card__title">Settings</h2>
      <p className="muted" style={{ fontSize: "0.85rem" }}>
        Reading HA entities uses the add-on's read-only Home Assistant API access and only the entities
        you name. MQTT reads the topics you list from your broker. Both are opt-in.
      </p>

      <label className="field">
        <span>Source</span>
        <select value={source} onChange={(e) => setSource(e.target.value)}>
          {SOURCES.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </label>

      {source === "ha_api" && (
        <label className="field">
          <span>Production entities (one per line)</span>
          <textarea
            rows={3}
            placeholder={"sensor.solar_energy_today\nsensor.grid_export_today"}
            value={entities}
            onChange={(e) => setEntities(e.target.value)}
          />
          <small className="muted">
            Point at sensors that report the period's production in kWh (e.g. a HA Utility Meter for
            "this month"). {s?.ha_api_available === false && "⚠️ HA API not available to the add-on yet."}
          </small>
        </label>
      )}

      {source === "mqtt" && (
        <label className="field">
          <span>Production topics (one per line)</span>
          <textarea
            rows={3}
            placeholder={"home/solar/energy_this_month"}
            value={topics}
            onChange={(e) => setTopics(e.target.value)}
          />
        </label>
      )}

      {source !== "off" && (
        <label className="field">
          <span>Production sensor type</span>
          <select value={semantics} onChange={(e) => setSemantics(e.target.value)}>
            <option value="cumulative">Cumulative total (an ever-increasing kWh meter)</option>
            <option value="interval">Per-interval (production since the last reading)</option>
          </select>
          <small className="muted">
            How your sensor reports, so the over-time trend is computed correctly. Most solar/grid
            "total energy" sensors are cumulative; a "this reading" / per-period sensor is interval.
          </small>
        </label>
      )}

      <label className="field">
        <span>Tariff (price per kWh)</span>
        <input
          inputMode="decimal"
          placeholder="e.g. 0.28 — blank to derive from meter readings"
          value={tariff}
          onChange={(e) => setTariff(e.target.value)}
        />
        {s?.derived_unit_price && !tariff && (
          <small className="muted">Derived from your meter readings: {s.derived_unit_price}/kWh.</small>
        )}
      </label>

      <label className="field">
        <span>Energy-bill category</span>
        <select value={categoryId} onChange={(e) => setCategoryId(e.target.value)}>
          <option value="">— none —</option>
          {(cats.data ?? []).map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
        <small className="muted">Spending in this category is treated as your energy bill.</small>
      </label>

      <div style={{ marginTop: 10 }}>
        <button className="btn" onClick={() => save.mutate()} disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save"}
        </button>
        {msg && <span className="muted" style={{ marginLeft: 10 }}>{msg}</span>}
      </div>
    </div>
  );
}
