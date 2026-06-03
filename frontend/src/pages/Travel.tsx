import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  bulkUpdateTransactions,
  createProjectFromTrip,
  getTravelByCurrency,
  getTravelTrips,
  type Trip,
} from "../api/client";

function fmtRange(first: string, last: string): string {
  return first === last ? first : `${first} → ${last}`;
}

// A tiny 2-letter country tagger for a trip (e.g. ES). Tags all the trip's
// transactions so the spend-by-location map shows the real country.
function TripCountry({ onSet, pending }: { onSet: (code: string) => void; pending: boolean }) {
  const [code, setCode] = useState("");
  return (
    <span style={{ display: "inline-flex", gap: 4, alignItems: "center" }}>
      <input
        placeholder="ES"
        value={code}
        maxLength={2}
        style={{ width: 44, textTransform: "uppercase" }}
        title="Tag this trip's spend with a country (ISO code, e.g. ES) for the spending-by-location map"
        onChange={(e) => setCode(e.target.value.replace(/[^A-Za-z]/g, ""))}
      />
      <button className="btn btn--sm btn--ghost" disabled={!code || pending} onClick={() => onSet(code.toUpperCase())}>
        Set country
      </button>
    </span>
  );
}

export default function Travel() {
  const qc = useQueryClient();
  const [gapDays, setGapDays] = useState(14);
  const [msg, setMsg] = useState<string | null>(null);
  const [openTrip, setOpenTrip] = useState<string | null>(null);

  const byCurrency = useQuery({ queryKey: ["travel-by-currency"], queryFn: getTravelByCurrency });
  const trips = useQuery({ queryKey: ["travel-trips", gapDays], queryFn: () => getTravelTrips(gapDays) });

  const makeProject = useMutation({
    mutationFn: (v: { name: string; ids: number[] }) => createProjectFromTrip(v.name, v.ids),
    onSuccess: (r) => {
      setMsg(`Created project “${r.name}” and assigned the trip's transactions to it.`);
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["travel-trips"] });
    },
    onError: (e) => setMsg(String(e instanceof Error ? e.message : e)),
  });

  // Tag a whole trip's transactions with a country, so the spend-by-location map
  // shows e.g. Spain instead of the coarse "Eurozone" currency fallback.
  const setCountry = useMutation({
    mutationFn: (v: { ids: number[]; country: string }) =>
      bulkUpdateTransactions(v.ids, { country: v.country }),
    onSuccess: (_r, v) => {
      setMsg(`Tagged ${v.ids.length} transaction(s) as ${v.country} — the spend-by-location map will use it.`);
      qc.invalidateQueries({ queryKey: ["dash-geo"] });
      qc.invalidateQueries({ queryKey: ["transactions"] });
    },
    onError: (e) => setMsg(String(e instanceof Error ? e.message : e)),
  });

  function createFor(trip: Trip) {
    const suggested = `${trip.label} ${trip.last.slice(0, 4)}`.trim();
    const name = window.prompt("Name this trip project:", suggested)?.trim();
    if (name) makeProject.mutate({ name, ids: trip.transaction_ids });
  }

  const rows = byCurrency.data?.currencies ?? [];
  const base = byCurrency.data?.base_currency ?? "GBP";

  return (
    <div className="page">
      <div className="page__head">
        <h1 className="page__title">Travel</h1>
      </div>
      <p className="muted">
        Spending abroad, inferred from each transaction's <strong>currency</strong> (anything not in your
        base currency, {base}). Group it by where it was spent, and see trips detected from clusters of
        foreign spend — turn any trip into a project to give it a budget and the usual breakdowns.
      </p>

      {msg && <p className="status status--ok">{msg}</p>}

      <div className="card">
        <h2 className="card__title">Spend by currency</h2>
        {byCurrency.isLoading && <p className="muted">Loading…</p>}
        {byCurrency.data && rows.length === 0 && (
          <p className="muted">No foreign-currency spending found — everything is in {base}.</p>
        )}
        {rows.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Where</th>
                  <th>Currency</th>
                  <th className="num">Spent (original)</th>
                  <th className="num">Spent ({base})</th>
                  <th className="num">Txns</th>
                  <th>Seen</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.currency}>
                    <td>{r.place}</td>
                    <td>{r.currency}</td>
                    <td className="num">{r.original_total} {r.currency}</td>
                    <td className="num">{r.base_total} {base}</td>
                    <td className="num">{r.count}</td>
                    <td className="muted">{fmtRange(r.first, r.last)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <h2 className="card__title" style={{ margin: 0 }}>Detected trips</h2>
          <label className="muted" style={{ fontSize: "0.85rem" }}>
            New trip after a gap of{" "}
            <select value={gapDays} onChange={(e) => setGapDays(Number(e.target.value))}>
              {[7, 14, 21, 30].map((n) => <option key={n} value={n}>{n} days</option>)}
            </select>
          </label>
        </div>
        {trips.isLoading && <p className="muted">Loading…</p>}
        {trips.data && trips.data.length === 0 && (
          <p className="muted">No trips detected yet. They appear once you have foreign-currency spend.</p>
        )}
        {trips.data && trips.data.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Dates</th>
                  <th>Where</th>
                  <th className="num">Spent</th>
                  <th className="num">Txns</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {trips.data.map((trip) => {
                  const key = `${trip.first}-${trip.last}-${trip.currencies.join(",")}`;
                  const open = openTrip === key;
                  return (
                    <Fragment key={key}>
                      <tr>
                        <td style={{ whiteSpace: "nowrap" }}>
                          <button className="link-btn" style={{ fontWeight: 600 }} onClick={() => setOpenTrip(open ? null : key)}>
                            {open ? "▾ " : "▸ "}{fmtRange(trip.first, trip.last)}
                          </button>
                        </td>
                        <td>{trip.label} <span className="muted">({trip.currencies.join(", ")})</span></td>
                        <td className="num">{trip.base_total} {trip.base_currency}</td>
                        <td className="num">{trip.transaction_count}</td>
                        <td>
                          <div className="form-row" style={{ gap: 6, justifyContent: "flex-end", flexWrap: "wrap" }}>
                            <TripCountry
                              pending={setCountry.isPending}
                              onSet={(code) => setCountry.mutate({ ids: trip.transaction_ids, country: code })}
                            />
                            <button
                              className="btn btn--sm"
                              disabled={makeProject.isPending}
                              onClick={() => createFor(trip)}
                            >
                              Create project
                            </button>
                          </div>
                        </td>
                      </tr>
                      {open && (
                        <tr>
                          <td colSpan={5} style={{ background: "rgba(127,127,127,0.05)" }}>
                            <ul className="kv" style={{ margin: "6px 0", maxWidth: 560 }}>
                              {trip.transactions.map((t) => (
                                <li key={t.id}>
                                  <span>
                                    <span className="muted">{t.transaction_date}</span> ·{" "}
                                    <Link to={`/transactions?focus=${t.id}`} title="Open this transaction">
                                      {t.description}
                                    </Link>
                                  </span>
                                  <span style={{ whiteSpace: "nowrap" }}>
                                    {t.amount} {t.currency}
                                    <span className="muted"> · ≈ {t.base_amount} {trip.base_currency}</span>
                                  </span>
                                </li>
                              ))}
                            </ul>
                            <p className="muted" style={{ margin: "0 0 6px", fontSize: "0.8rem" }}>
                              Tip: <strong>Create project</strong> groups these into a project — then add more
                              spend from the Transactions page by setting a row's <strong>Project</strong>.
                            </p>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
