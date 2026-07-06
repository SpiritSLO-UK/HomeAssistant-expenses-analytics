import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { searchAll } from "../api/client";

// Global search: type anything to find transactions, vendors, categories or
// projects and jump straight to them. The query is mirrored in the URL (?q=) so
// the sidebar quick-search can deep-link here.
export default function Search() {
  const [params, setParams] = useSearchParams();
  const initial = params.get("q") ?? "";
  const [term, setTerm] = useState(initial);
  const [debounced, setDebounced] = useState(initial);

  // Debounce typing, and keep ?q= in sync so the result is shareable/back-able.
  useEffect(() => {
    const id = globalThis.setTimeout(() => {
      setDebounced(term);
      setParams(term ? { q: term } : {}, { replace: true });
    }, 250);
    return () => globalThis.clearTimeout(id);
  }, [term, setParams]);

  const q = useQuery({
    queryKey: ["search", debounced],
    queryFn: () => searchAll(debounced),
    enabled: debounced.trim().length >= 2,
  });

  const r = q.data;
  const total =
    (r?.transactions.length ?? 0) + (r?.vendors.length ?? 0) + (r?.categories.length ?? 0) + (r?.projects.length ?? 0);

  return (
    <div className="page">
      <h1 className="page__title">Search</h1>
      <div className="card">
        <label htmlFor="search-input" className="muted" style={{ display: "block", marginBottom: 4 }}>
          Search transactions, vendors, categories, projects
        </label>
        <input
          id="search-input"
          autoFocus
          aria-label="Search transactions, vendors, categories, projects"
          placeholder="Search transactions, vendors, categories, projects…"
          value={term}
          onChange={(e) => setTerm(e.target.value)}
          style={{ width: "100%", fontSize: "1.05rem", padding: "8px 10px" }}
        />
        {debounced.trim().length < 2 && (
          <p className="muted" style={{ marginBottom: 0 }}>Type at least two characters.</p>
        )}
      </div>

      {debounced.trim().length >= 2 && (
        <>
          {q.isLoading && <p className="muted">Searching…</p>}
          {r && total === 0 && (
            <div className="card">
              <p className="muted">No matches for “{debounced}”.</p>
            </div>
          )}
          {r && total > 0 && (
            <p className="muted">
              {total} result{total === 1 ? "" : "s"} for “{debounced}”
            </p>
          )}

          {r && r.transactions.length > 0 && (
            <div className="card">
              <h2 className="card__title">Transactions ({r.transactions.length})</h2>
              <ul className="kv">
                {r.transactions.map((t) => (
                  <li key={t.id}>
                    <span>
                      <Link to={`/transactions?focus=${t.id}`}>{t.description}</Link>{" "}
                      <span className="muted">· {t.transaction_date}</span>
                    </span>
                    <span>{t.amount} {t.currency}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {r && r.vendors.length > 0 && (
            <div className="card">
              <h2 className="card__title">Vendors ({r.vendors.length})</h2>
              <div className="chips">
                {r.vendors.map((v) => (
                  <Link key={v.id} className="chip" to={`/transactions?vendor_id=${v.id}`}>{v.name}</Link>
                ))}
              </div>
            </div>
          )}

          {r && r.categories.length > 0 && (
            <div className="card">
              <h2 className="card__title">Categories ({r.categories.length})</h2>
              <div className="chips">
                {r.categories.map((c) => (
                  <Link key={c.id} className="chip" to={`/transactions?category_id=${c.id}`}>
                    <span className="chip__dot" style={{ background: c.colour ?? "#bbb" }} />
                    {c.name}
                  </Link>
                ))}
              </div>
            </div>
          )}

          {r && r.projects.length > 0 && (
            <div className="card">
              <h2 className="card__title">Projects ({r.projects.length})</h2>
              <ul className="kv">
                {r.projects.map((p) => (
                  <li key={p.id}>
                    <span><Link to={`/transactions?project_id=${p.id}`}>{p.name}</Link></span>
                    <span className="tag">{p.status}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </div>
  );
}
