import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getMe, getSettings, getSupportedCurrencies, updateSettings } from "../api/client";
import { useOptimisticSelect } from "../hooks/useOptimisticSelect";

// A single "Welcome, let's set up" entry that branches by household shape: Solo is
// a short inline flow (base currency + import); Household/Family hand off to the
// existing family-setup wizard. Owner-only.
export default function Setup() {
  const me = useQuery({ queryKey: ["me"], queryFn: getMe });
  const [shape, setShape] = useState<null | "solo" | "shared">(null);

  if (me.data && !me.data.is_admin) {
    return (
      <div className="page">
        <h1 className="page__title">Set up</h1>
        <p className="status status--error">Only an owner (administrator) can set up the household.</p>
      </div>
    );
  }

  return (
    <div className="page">
      <h1 className="page__title">Welcome — let's set up</h1>

      {!shape && (
        <div className="card">
          <p className="muted" style={{ marginTop: 0 }}>
            Who's this for? You can change any of it later — nothing here is locked in.
          </p>
          <div className="cols cols--domain">
            <ShapeCard
              icon="🧍"
              title="Just me"
              detail="A single person. We'll set your currency and get you importing."
              onPick={() => setShape("solo")}
            />
            <ShapeCard
              icon="🏠"
              title="Household"
              detail="A few adults sharing. Approve people, choose shared vs private accounts."
              onPick={() => setShape("shared")}
            />
            <ShapeCard
              icon="👨‍👩‍👧"
              title="Family"
              detail="Adults plus kids. Everything in Household, plus pocket-money allowances."
              onPick={() => setShape("shared")}
            />
          </div>
        </div>
      )}

      {shape === "solo" && <SoloSetup onBack={() => setShape(null)} />}
      {shape === "shared" && <SharedIntro onBack={() => setShape(null)} />}
    </div>
  );
}

function ShapeCard({ icon, title, detail, onPick }: Readonly<{ icon: string; title: string; detail: string; onPick: () => void }>) {
  return (
    <button className="card setup-shape" onClick={onPick} style={{ textAlign: "left", cursor: "pointer" }}>
      <div style={{ fontSize: "1.6rem" }}>{icon}</div>
      <h2 className="card__title" style={{ marginBottom: 4 }}>{title}</h2>
      <p className="muted" style={{ margin: 0, fontSize: "0.85rem" }}>{detail}</p>
    </button>
  );
}

function SoloSetup({ onBack }: Readonly<{ onBack: () => void }>) {
  const qc = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const currencies = useQuery({ queryKey: ["currencies"], queryFn: getSupportedCurrencies });
  const base = settings.data?.base_currency ?? "GBP";
  const [err, setErr] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (code: string) => updateSettings({ base_currency: code }),
    onSuccess: () => {
      setErr(null);
      qc.invalidateQueries({ queryKey: ["settings"] });
    },
    onError: (e) => setErr(String(e)),
  });
  // Optimistic overlay for the base-currency select (FE-8): show the chosen code
  // immediately and revert to the server value on failure. `save` keeps its own
  // onError, so the overlay reverts silently. Singleton, keyed by a constant.
  const baseSelect = useOptimisticSelect<string, string>();
  // Guarantee the configured base is always a selectable option, even when it
  // isn't in the curated list — otherwise the control renders blank and a save
  // could silently overwrite the real base currency.
  const curated = currencies.data ?? [];
  const currencyOptions = curated.some((c) => c.code === base)
    ? curated
    : [{ code: base, name: base, symbol: base }, ...curated];
  return (
    <div className="card">
      <h2 className="card__title">Just me</h2>
      <ol className="setup-list">
        <li>
          <strong>Base currency</strong> — everything is shown in this currency.
          <div style={{ marginTop: 6 }}>
            <select
              value={baseSelect.valueFor("base", base)}
              onChange={(e) => {
                const code = e.target.value;
                baseSelect.choose("base", code, () => save.mutateAsync(code));
              }}
            >
              {currencyOptions.map((c) => (
                <option key={c.code} value={c.code}>{c.symbol} {c.code} — {c.name}</option>
              ))}
            </select>
            {save.isPending && <span className="muted"> saving…</span>}
            {err && <p className="status status--error">{err}</p>}
          </div>
        </li>
        <li>
          <strong>Import a statement</strong> — upload a CSV/PDF to pull in your transactions.
          <div style={{ marginTop: 6 }}><Link className="btn btn--sm" to="/import">Go to Import →</Link></div>
        </li>
        <li>
          <strong>Done!</strong> Explore the dashboard; tweak categories, budgets and rules whenever you like.
        </li>
      </ol>
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 12 }}>
        <button className="btn btn--ghost" onClick={onBack}>← Back</button>
        <Link className="btn" to="/">Go to the dashboard</Link>
      </div>
    </div>
  );
}

function SharedIntro({ onBack }: Readonly<{ onBack: () => void }>) {
  return (
    <div className="card">
      <h2 className="card__title">Household &amp; family</h2>
      <p className="muted">
        Other people appear automatically when they open the add-on through Home Assistant. The family
        wizard walks you through it:
      </p>
      <ol className="setup-list">
        <li><strong>People &amp; roles</strong> — approve who has access and what they can do.</li>
        <li><strong>Shared vs private accounts</strong> — keep some accounts to yourself.</li>
        <li><strong>Kids' allowance</strong> — give children a pocket-money budget (skip if none).</li>
      </ol>
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 12 }}>
        <button className="btn btn--ghost" onClick={onBack}>← Back</button>
        <Link className="btn" to="/family-setup">Continue to family setup →</Link>
      </div>
    </div>
  );
}
