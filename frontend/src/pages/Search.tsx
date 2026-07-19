import { useEffect, useMemo, useState, type CSSProperties, type KeyboardEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { searchAll } from "../api/client";
import { formatDate, useDateFormat } from "../lib/date";

// Highlight applied to the currently keyboard-selected result.
const activeStyle: CSSProperties = { outline: "2px solid #3b82f6", outlineOffset: 2, borderRadius: 4 };

// Global search: type anything to find transactions, vendors, categories or
// projects and jump straight to them. The query is mirrored in the URL (?q=) so
// the sidebar quick-search can deep-link here. Results are keyboard-navigable:
// ArrowDown/ArrowUp move a highlighted selection through the flattened list and
// Enter opens it, reusing the exact same deep-links as clicking (added in #304).
export default function Search() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const dateFmt = useDateFormat();
  const initial = params.get("q") ?? "";
  const [term, setTerm] = useState(initial);
  const [debounced, setDebounced] = useState(initial);
  const [activeIndex, setActiveIndex] = useState(-1);
  // Whether the user is actually driving the results with the keyboard. The
  // visible highlight and the navigation hint are gated on this so mouse users
  // never see a selected row (or clutter) they did not ask for. The ARIA
  // attributes below stay wired up regardless, so screen-reader users are
  // unaffected.
  const [keyboardActive, setKeyboardActive] = useState(false);

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

  // Flattened, ordered view of every result with its DOM id and deep-link. The
  // order and the `to` targets mirror the rendered sections exactly so keyboard
  // activation and clicking always land on the same place.
  const flat = useMemo(() => {
    if (!r) return [];
    return [
      ...r.transactions.map((t) => ({ domId: `sr-tx-${t.id}`, to: `/transactions?focus=${t.id}` })),
      ...r.vendors.map((v) => ({ domId: `sr-vd-${v.id}`, to: `/transactions?vendor_id=${v.id}` })),
      ...r.categories.map((c) => ({ domId: `sr-cat-${c.id}`, to: `/transactions?category_id=${c.id}` })),
      ...r.projects.map((p) => ({ domId: `sr-prj-${p.id}`, to: `/transactions?project_id=${p.id}` })),
    ];
  }, [r]);

  const activeId = activeIndex >= 0 ? flat[activeIndex]?.domId : undefined;

  // Reset the highlight whenever the query (and therefore the result set) changes.
  useEffect(() => {
    setActiveIndex(-1);
    setKeyboardActive(false);
  }, [debounced]);

  // Once the keyboard has been used, any mouse activity hands control back to
  // the mouse: the visible highlight and hint disappear so mouse users are not
  // shown a selection they never asked for. Listeners are only attached while
  // keyboard mode is on to avoid a permanent global handler.
  useEffect(() => {
    if (!keyboardActive) return;
    const backToMouse = () => setKeyboardActive(false);
    window.addEventListener("mousemove", backToMouse);
    window.addEventListener("pointerdown", backToMouse);
    return () => {
      window.removeEventListener("mousemove", backToMouse);
      window.removeEventListener("pointerdown", backToMouse);
    };
  }, [keyboardActive]);

  // Keep the highlighted result scrolled into view as the user arrows through.
  useEffect(() => {
    if (!activeId) return;
    document.getElementById(activeId)?.scrollIntoView({ block: "nearest" });
  }, [activeId]);

  const onInputKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (flat.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setKeyboardActive(true);
      setActiveIndex((i) => (i + 1) % flat.length);
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setKeyboardActive(true);
      setActiveIndex((i) => (i <= 0 ? flat.length - 1 : i - 1));
      return;
    }
    if (e.key === "Enter" && activeIndex >= 0) {
      const item = flat[activeIndex];
      if (item) navigate(item.to);
    }
  };

  const styleFor = (domId: string): CSSProperties | undefined =>
    keyboardActive && activeId === domId ? activeStyle : undefined;

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
          placeholder="Try: groceries category:Food after:2026-01-01"
          value={term}
          onChange={(e) => setTerm(e.target.value)}
          onKeyDown={onInputKeyDown}
          role="combobox"
          aria-expanded={flat.length > 0}
          aria-controls="search-results"
          aria-activedescendant={activeId}
          style={{ width: "100%", fontSize: "1.05rem", padding: "8px 10px" }}
        />
        {debounced.trim().length < 2 && (
          <p className="muted" style={{ marginBottom: 0 }}>Type at least two characters.</p>
        )}
        {keyboardActive && flat.length > 0 && (
          <p className="muted" style={{ marginBottom: 0 }}>Use ↑ and ↓ to navigate, Enter to open.</p>
        )}
        <details style={{ marginTop: 8 }}>
          <summary className="muted" style={{ cursor: "pointer" }}>Filter tips</summary>
          <div className="muted" style={{ marginTop: 6, fontSize: "0.9rem" }}>
            <p style={{ marginTop: 0 }}>
              Mix these tokens into your text to narrow the results:
            </p>
            <ul style={{ margin: "0 0 6px", paddingLeft: 18 }}>
              <li><code>category:Food</code> - only that category (exact name, case-insensitive)</li>
              <li><code>after:2026-01-01</code> / <code>before:2026-03-31</code> - date bounds (inclusive)</li>
              <li><code>2026-01-01..2026-03-31</code> - an inclusive date range</li>
            </ul>
            <p style={{ marginBottom: 0 }}>
              Dates are <code>YYYY-MM-DD</code> or a whole month <code>YYYY-MM</code>. A bare word also matches
              transaction tags. Example: <code>groceries category:Food after:2026-01-01</code>.
            </p>
          </div>
        </details>
      </div>

      {debounced.trim().length >= 2 && (
        <div id="search-results">
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
                      <Link id={`sr-tx-${t.id}`} style={styleFor(`sr-tx-${t.id}`)} to={`/transactions?focus=${t.id}`}>
                        {t.description}
                      </Link>{" "}
                      <span className="muted">· {formatDate(t.transaction_date, dateFmt)}</span>
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
                  <Link
                    key={v.id}
                    id={`sr-vd-${v.id}`}
                    className="chip"
                    style={styleFor(`sr-vd-${v.id}`)}
                    to={`/transactions?vendor_id=${v.id}`}
                  >
                    {v.name}
                  </Link>
                ))}
              </div>
            </div>
          )}

          {r && r.categories.length > 0 && (
            <div className="card">
              <h2 className="card__title">Categories ({r.categories.length})</h2>
              <div className="chips">
                {r.categories.map((c) => (
                  <Link
                    key={c.id}
                    id={`sr-cat-${c.id}`}
                    className="chip"
                    style={styleFor(`sr-cat-${c.id}`)}
                    to={`/transactions?category_id=${c.id}`}
                  >
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
                    <span>
                      <Link id={`sr-prj-${p.id}`} style={styleFor(`sr-prj-${p.id}`)} to={`/transactions?project_id=${p.id}`}>
                        {p.name}
                      </Link>
                    </span>
                    <span className="tag">{p.status}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
