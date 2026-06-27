import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addAssetLog,
  createAsset,
  deleteAsset,
  deleteAssetLog,
  getAsset,
  listAssets,
  type Asset,
  type AssetLog,
} from "../api/client";
import { money } from "../lib/money";

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

// UK/imperial gallon — fuel is stored canonically in litres; imperial cars enter
// and display gallons, converted here so the system is never mixed.
const IMP_GALLON = 4.54609;

const KIND_ICON: Record<string, string> = { car: "🚗", home: "🏠", other: "📦" };

export default function Assets() {
  const [err, setErr] = useState<string | null>(null);
  const assets = useQuery({ queryKey: ["assets"], queryFn: () => listAssets() });
  const [showNew, setShowNew] = useState(false);
  const qc = useQueryClient();
  // The success callback passed to child cards — clearing the error here drops a
  // stale banner after a later success (FE-3).
  const invalidate = () => {
    setErr(null);
    qc.invalidateQueries({ queryKey: ["assets"] });
  };
  const fail = (e: unknown) => setErr(String(e));

  return (
    <div className="page">
      <div className="page__head">
        <h1 className="page__title">Cars &amp; assets</h1>
        <button className="btn btn--sm" onClick={() => setShowNew((v) => !v)}>
          {showNew ? "Cancel" : "＋ New asset"}
        </button>
      </div>
      <p className="muted">
        Track a car, your home or anything else. Log a car's refuels (odometer + litres + cost) to
        get <strong>MPG</strong> and <strong>L/100km</strong>, plus servicing and running costs.
      </p>
      {err && <p className="status status--error">{err}</p>}

      {showNew && (
        <div className="card">
          <NewAssetForm onCreated={() => { invalidate(); setShowNew(false); }} onError={fail} />
        </div>
      )}

      {assets.data?.length === 0 && !showNew && (
        <div className="card"><p className="muted">No assets yet — add a car with ＋ New asset.</p></div>
      )}

      {assets.data?.map((a) => (
        <AssetCard key={a.id} asset={a} onChange={invalidate} onError={fail} />
      ))}
    </div>
  );
}

function NewAssetForm({ onCreated, onError }: Readonly<{ onCreated: () => void; onError: (e: unknown) => void }>) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState("car");
  const [identifier, setIdentifier] = useState("");
  const [unit, setUnit] = useState("mi");
  const create = useMutation({
    mutationFn: () =>
      createAsset({ name, kind, identifier: identifier || undefined, distance_unit: unit }),
    onSuccess: () => { setName(""); setIdentifier(""); onCreated(); },
    onError,
  });
  return (
    <form
      className="form-row"
      style={{ flexWrap: "wrap" }}
      onSubmit={(e) => { e.preventDefault(); if (name) create.mutate(); }}
    >
      <input placeholder="Name (e.g. Family car)" value={name} onChange={(e) => setName(e.target.value)} />
      <select value={kind} onChange={(e) => setKind(e.target.value)}>
        <option value="car">Car</option>
        <option value="home">Home</option>
        <option value="other">Other</option>
      </select>
      <input
        placeholder={kind === "car" ? "Reg / model (optional)" : "Label (optional)"}
        value={identifier}
        onChange={(e) => setIdentifier(e.target.value)}
      />
      {kind === "car" && (
        <select value={unit} onChange={(e) => setUnit(e.target.value)} title="Measurement system">
          <option value="mi">Imperial (miles · gallons · MPG)</option>
          <option value="km">Metric (km · litres · L/100km)</option>
        </select>
      )}
      <button className="btn" type="submit" disabled={!name || create.isPending}>
        {create.isPending ? "Adding…" : "Add asset"}
      </button>
    </form>
  );
}

function AssetCard({ asset, onChange, onError }: Readonly<{ asset: Asset; onChange: () => void; onError: (e: unknown) => void }>) {
  const [open, setOpen] = useState(false);
  const qc = useQueryClient();
  const detail = useQuery({
    queryKey: ["asset", asset.id],
    queryFn: () => getAsset(asset.id),
    enabled: open,
  });
  const refresh = () => { onChange(); qc.invalidateQueries({ queryKey: ["asset", asset.id] }); };

  const remove = useMutation({
    mutationFn: () => deleteAsset(asset.id),
    onSuccess: onChange,
    onError,
  });

  const car = asset.car;
  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
        <div>
          <button className="link-btn" style={{ fontWeight: 700, fontSize: "1.05rem" }} onClick={() => setOpen((v) => !v)}>
            {open ? "▾ " : "▸ "}{KIND_ICON[asset.kind] ?? "📦"} {asset.name}
          </button>
          {asset.identifier && <span className="muted"> · {asset.identifier}</span>}
          <div className="muted" style={{ fontSize: "0.85rem", marginTop: 2 }}>
            {car?.avg_economy != null ? <>≈ {car.avg_economy} {car.economy_unit} · </> : null}
            Total cost {money(asset.total_cost)}
            {car?.latest_odometer && <> · {car.latest_odometer} {car.distance_unit}</>}
          </div>
        </div>
        <button
          className="link-btn"
          onClick={() => { if (globalThis.confirm(`Delete "${asset.name}" and all its logs?`)) remove.mutate(); }}
        >
          ✕
        </button>
      </div>

      {open && (
        <div style={{ marginTop: 12 }}>
          {detail.isLoading && <p className="muted">Loading…</p>}
          {detail.data?.kind === "car" && detail.data.car && <CarStatsPanel car={detail.data.car} />}
          {detail.data?.kind === "car" && (
            <RefuelForm assetId={asset.id} unit={asset.distance_unit} onAdded={refresh} onError={onError} />
          )}
          {detail.data?.kind === "home" && detail.data.home && <HomeStatsPanel home={detail.data.home} />}
          {detail.data?.kind === "home" && (
            <ReadingForm assetId={asset.id} onAdded={refresh} onError={onError} />
          )}
          <EntryForm assetId={asset.id} onAdded={refresh} onError={onError} />
          {detail.data && <LogHistory asset={detail.data} logs={detail.data.logs ?? []} onChange={refresh} onError={onError} />}
        </div>
      )}
    </div>
  );
}

function CarStatsPanel({ car }: Readonly<{ car: NonNullable<Asset["car"]> }>) {
  const eu = car.economy_unit;
  return (
    <div className="stat-grid" style={{ marginBottom: 12 }}>
      <Stat label="Avg economy" value={car.avg_economy == null ? "—" : `${car.avg_economy} ${eu}`} />
      <Stat label="Last fill" value={car.last_economy == null ? "—" : `${car.last_economy} ${eu}`} />
      <Stat label={`Fuel used (${car.fuel_unit})`} value={Number(car.total_fuel) > 0 ? car.total_fuel : "—"} />
      <Stat label="Fuel cost" value={money(car.total_fuel_cost)} />
    </div>
  );
}

function Stat({ label, value }: Readonly<{ label: string; value: string }>) {
  return (
    <div className="stat">
      <div className="stat__label">{label}</div>
      <div className="stat__value" style={{ fontSize: "1.1rem" }}>{value}</div>
    </div>
  );
}

function HomeStatsPanel({ home }: Readonly<{ home: NonNullable<Asset["home"]> }>) {
  if (home.meters.length === 0) {
    return <p className="muted" style={{ marginTop: 0 }}>No meter readings yet — log one below to track usage.</p>;
  }
  return (
    <div style={{ marginBottom: 12 }}>
      {home.meters.map((m) => (
        <div key={m.meter} style={{ marginBottom: 4 }}>
          <strong style={{ textTransform: "capitalize" }}>{m.meter}</strong>
          <span className="muted">
            {" "}· latest {m.latest_reading}{m.unit ? ` ${m.unit}` : ""}
            {" "}· used {m.total_usage}{m.unit ? ` ${m.unit}` : ""}
            {" "}· {money(m.total_cost)}
          </span>
        </div>
      ))}
    </div>
  );
}

const METER_UNITS: Record<string, string> = { electricity: "kWh", gas: "kWh", water: "m3", other: "" };

function ReadingForm({ assetId, onAdded, onError }: Readonly<{ assetId: number; onAdded: () => void; onError: (e: unknown) => void }>) {
  const [date, setDate] = useState(today());
  const [meter, setMeter] = useState("electricity");
  const [reading, setReading] = useState("");
  const [unit, setUnit] = useState("kWh");
  const [cost, setCost] = useState("");

  const add = useMutation({
    mutationFn: () =>
      addAssetLog(assetId, {
        log_date: date,
        kind: "reading",
        meter,
        reading,
        unit: unit || undefined,
        cost: cost || undefined,
      }),
    onSuccess: () => { setReading(""); setCost(""); onAdded(); },
    onError,
  });

  return (
    <form
      className="form-row"
      style={{ flexWrap: "wrap", gap: 6, marginBottom: 8 }}
      onSubmit={(e) => { e.preventDefault(); if (reading) add.mutate(); }}
    >
      <strong style={{ alignSelf: "center", fontSize: "0.85rem" }}>📊 Reading:</strong>
      <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
      <select
        value={meter}
        onChange={(e) => { const m = e.target.value; setMeter(m); setUnit(METER_UNITS[m] ?? unit); }}
      >
        <option value="electricity">Electricity</option>
        <option value="gas">Gas</option>
        <option value="water">Water</option>
        <option value="other">Other</option>
      </select>
      <input placeholder="Reading" value={reading} style={{ width: 110 }} onChange={(e) => setReading(e.target.value)} />
      <input placeholder="Unit" value={unit} style={{ width: 70 }} onChange={(e) => setUnit(e.target.value)} />
      <input placeholder="Cost" value={cost} style={{ width: 80 }} onChange={(e) => setCost(e.target.value)} />
      <button className="btn btn--sm" type="submit" disabled={!reading || add.isPending}>
        {add.isPending ? "…" : "Add"}
      </button>
    </form>
  );
}

function RefuelForm({ assetId, unit, onAdded, onError }: Readonly<{
  assetId: number; unit: string; onAdded: () => void; onError: (e: unknown) => void;
}>) {
  const imperial = unit === "mi";
  const [date, setDate] = useState(today());
  const [odometer, setOdometer] = useState("");
  const [fuel, setFuel] = useState("");  // entered in the asset's system (gal or L)
  const [cost, setCost] = useState("");
  const [fullTank, setFullTank] = useState(true);

  const add = useMutation({
    mutationFn: () =>
      addAssetLog(assetId, {
        log_date: date,
        kind: "refuel",
        odometer,
        // Stored canonically in litres; convert from gallons for imperial cars.
        litres: imperial ? String(Number(fuel) * IMP_GALLON) : fuel,
        cost: cost || undefined,
        is_full_tank: fullTank,
      }),
    onSuccess: () => { setOdometer(""); setFuel(""); setCost(""); onAdded(); },
    onError,
  });

  return (
    <form
      className="form-row"
      style={{ flexWrap: "wrap", gap: 6, marginBottom: 8 }}
      onSubmit={(e) => { e.preventDefault(); if (odometer && fuel) add.mutate(); }}
    >
      <strong style={{ alignSelf: "center", fontSize: "0.85rem" }}>⛽ Refuel:</strong>
      <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
      <input placeholder={`Odometer (${unit})`} value={odometer} style={{ width: 120 }} onChange={(e) => setOdometer(e.target.value)} />
      <input placeholder={imperial ? "Gallons" : "Litres"} value={fuel} style={{ width: 80 }} onChange={(e) => setFuel(e.target.value)} />
      <input placeholder="Cost" value={cost} style={{ width: 80 }} onChange={(e) => setCost(e.target.value)} />
      <label className="muted" style={{ fontSize: "0.82rem", display: "flex", alignItems: "center", gap: 4 }}>
        <input type="checkbox" checked={fullTank} onChange={(e) => setFullTank(e.target.checked)} /> full tank
      </label>
      <button className="btn btn--sm" type="submit" disabled={!odometer || !fuel || add.isPending}>
        {add.isPending ? "…" : "Add"}
      </button>
    </form>
  );
}

function EntryForm({ assetId, onAdded, onError }: Readonly<{ assetId: number; onAdded: () => void; onError: (e: unknown) => void }>) {
  const [date, setDate] = useState(today());
  const [kind, setKind] = useState("expense");
  const [cost, setCost] = useState("");
  const [note, setNote] = useState("");

  const add = useMutation({
    mutationFn: () => addAssetLog(assetId, { log_date: date, kind, cost: cost || undefined, note: note || undefined }),
    onSuccess: () => { setCost(""); setNote(""); onAdded(); },
    onError,
  });

  return (
    <form
      className="form-row"
      style={{ flexWrap: "wrap", gap: 6, marginBottom: 8 }}
      onSubmit={(e) => { e.preventDefault(); if (cost || note) add.mutate(); }}
    >
      <strong style={{ alignSelf: "center", fontSize: "0.85rem" }}>🔧 Entry:</strong>
      <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
      <select value={kind} onChange={(e) => setKind(e.target.value)}>
        <option value="expense">Expense</option>
        <option value="service">Service</option>
        <option value="note">Note</option>
      </select>
      <input placeholder="Cost" value={cost} style={{ width: 80 }} onChange={(e) => setCost(e.target.value)} />
      <input placeholder="Note" value={note} style={{ flex: 1, minWidth: 140 }} onChange={(e) => setNote(e.target.value)} />
      <button className="btn btn--sm btn--ghost" type="submit" disabled={(!cost && !note) || add.isPending}>
        {add.isPending ? "…" : "Add"}
      </button>
    </form>
  );
}

function LogHistory({ asset, logs, onChange, onError }: Readonly<{
  asset: Asset; logs: AssetLog[]; onChange: () => void; onError: (e: unknown) => void;
}>) {
  const remove = useMutation({
    mutationFn: (id: number) => deleteAssetLog(id),
    onSuccess: onChange,
    onError,
  });
  // Per-segment economy (in the asset's system) keyed by the "to" odometer.
  const econByOdo = new Map<string, number>();
  asset.car?.segments.forEach((s) => econByOdo.set(s.to_odometer, s.economy));
  const imperial = asset.car?.system === "imperial";
  const economyUnit = asset.car?.economy_unit ?? "MPG";
  const fuelDisplay = (litres: string) =>
    imperial ? `${(Number(litres) / IMP_GALLON).toFixed(2)} gal` : `${litres} L`;

  const rows = [...logs].reverse(); // newest first
  if (rows.length === 0) return <p className="muted">No log entries yet.</p>;
  return (
    <div className="table-wrap" style={{ marginTop: 6 }}>
      <table className="table">
        <thead>
          <tr><th>Date</th><th>Kind</th><th>Detail</th><th>Cost</th><th></th></tr>
        </thead>
        <tbody>
          {rows.map((lg) => {
            const econ = lg.odometer == null ? undefined : econByOdo.get(lg.odometer);
            const detail =
              lg.kind === "reading" ? (
                <>
                  <span style={{ textTransform: "capitalize" }}>{lg.meter}</span>: {lg.reading}
                  {lg.unit ? ` ${lg.unit}` : ""}
                </>
              ) : (
                lg.note ?? <span className="muted">—</span>
              );
            return (
              <tr key={lg.id}>
                <td style={{ whiteSpace: "nowrap" }}>{lg.log_date}</td>
                <td>{lg.kind}</td>
                <td>
                  {lg.kind === "refuel" ? (
                    <>
                      {lg.odometer} · {lg.litres == null ? "—" : fuelDisplay(lg.litres)}
                      {lg.is_full_tank === false && <span className="muted"> · partial</span>}
                      {econ != null && <span className="amt--pos"> · {econ} {economyUnit}</span>}
                    </>
                  ) : (
                    detail
                  )}
                </td>
                <td style={{ whiteSpace: "nowrap" }}>{lg.cost == null ? "—" : money(lg.cost)}</td>
                <td>
                  <button className="link-btn" onClick={() => { if (globalThis.confirm("Delete this entry?")) remove.mutate(lg.id); }}>✕</button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
