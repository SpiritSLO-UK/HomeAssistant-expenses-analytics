import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addVendorAlias,
  createVendor,
  deleteVendor,
  getMe,
  listCategories,
  listVendors,
  mergeVendors,
  setVendorDefaultCategory,
  updateVendor,
  type Vendor,
} from "../api/client";
import CountrySelect from "../components/CountrySelect";
import { money } from "../lib/money";

// How the vendor table can be ordered. Name is the default; the two numeric
// options sort high→low so the biggest vendors surface first.
type SortKey = "name" | "txns" | "total";
const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: "name", label: "Name (A–Z)" },
  { value: "txns", label: "Most transactions" },
  { value: "total", label: "Highest total" },
];

export default function Vendors() {
  const qc = useQueryClient();
  const vendors = useQuery({ queryKey: ["vendors"], queryFn: listVendors });
  const categories = useQuery({ queryKey: ["categories"], queryFn: listCategories });

  // Merge is structural/destructive (deletes the source), so it's owner-only —
  // the backend enforces the same; hiding it just keeps it out of sight (#334).
  const me = useQuery({ queryKey: ["me"], queryFn: getMe });
  const isAdmin = me.data?.is_admin === true;

  const [name, setName] = useState("");
  const [alias, setAlias] = useState("");
  const [sort, setSort] = useState<SortKey>("name");
  const [mergeSource, setMergeSource] = useState("");
  const [mergeTarget, setMergeTarget] = useState("");

  const [err, setErr] = useState<string | null>(null);
  const fail = (e: unknown) => setErr(String(e));
  // Cleared on success too, so a stale banner doesn't outlive a later success (FE-3).
  const invalidate = () => {
    setErr(null);
    qc.invalidateQueries({ queryKey: ["vendors"] });
  };

  const create = useMutation({
    mutationFn: () => createVendor({ canonical_name: name, alias: alias.trim() || undefined }),
    onSuccess: () => {
      setName("");
      setAlias("");
      invalidate();
    },
    onError: fail,
  });
  const addAlias = useMutation({
    mutationFn: (v: { id: number; alias: string }) => addVendorAlias(v.id, v.alias),
    onSuccess: invalidate,
    onError: fail,
  });
  const setCategory = useMutation({
    mutationFn: (v: { id: number; categoryId: number | null }) =>
      setVendorDefaultCategory(v.id, v.categoryId),
    onSuccess: invalidate,
    onError: fail,
  });
  const setCountry = useMutation({
    mutationFn: (v: { id: number; country: string | null }) => updateVendor(v.id, { country: v.country }),
    onSuccess: invalidate,
    onError: fail,
  });
  const remove = useMutation({ mutationFn: (id: number) => deleteVendor(id), onSuccess: invalidate, onError: fail });

  // Merge re-points the source vendor's transactions + aliases onto the target
  // and deletes the source, so refresh the vendor list plus the caches that read
  // vendor assignments — the transactions list and the dashboard's top-vendors
  // breakdown — rather than blowing away every query.
  const merge = useMutation({
    mutationFn: (v: { source: number; target: number }) => mergeVendors(v.source, v.target),
    onSuccess: () => {
      setErr(null);
      setMergeSource("");
      setMergeTarget("");
      qc.invalidateQueries({ queryKey: ["vendors"] });
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["dash-vendors"] });
    },
    onError: fail,
  });

  const catName = (id: number | null) =>
    categories.data?.find((c) => c.id === id)?.name ?? "—";

  const vendorList = vendors.data ?? [];
  const nameOf = (id: string) => vendorList.find((v) => String(v.id) === id)?.canonical_name ?? "";

  // Sort a copy so we never mutate the query cache's array in place.
  const sortedVendors = [...vendorList].sort((a, b) => {
    if (sort === "txns") return b.transaction_count - a.transaction_count;
    if (sort === "total") return Math.abs(Number(b.total_amount)) - Math.abs(Number(a.total_amount));
    return a.canonical_name.localeCompare(b.canonical_name);
  });

  function confirmMerge() {
    if (!mergeSource || !mergeTarget || mergeSource === mergeTarget) return;
    if (
      globalThis.confirm(
        `Merge "${nameOf(mergeSource)}" into "${nameOf(mergeTarget)}"? ` +
          `Its transactions and aliases move to the second vendor, then the first is deleted.`,
      )
    ) {
      merge.mutate({ source: Number(mergeSource), target: Number(mergeTarget) });
    }
  }

  // Deleting a vendor drops its aliases + unlinks its transactions, so confirm
  // first (the action is otherwise immediate and irreversible).
  const confirmDelete = (v: Vendor) => {
    const linked = v.transaction_count
      ? ` ${v.transaction_count} linked transaction(s) keep their data but lose the vendor link.`
      : "";
    if (globalThis.confirm(`Delete the vendor "${v.canonical_name}"? Its aliases are removed too.${linked}`))
      remove.mutate(v.id);
  };

  return (
    <div className="page">
      <h1 className="page__title">Vendors</h1>
      {err && <p className="status status--error">{err}</p>}

      <div className="card">
        <h2 className="card__title">Add a vendor</h2>
        <div className="form-row">
          <input aria-label="Canonical vendor name" placeholder="Canonical name (e.g. Tesco)" value={name} onChange={(e) => setName(e.target.value)} />
          <input aria-label="Alias to match" placeholder="Alias to match (e.g. TESCO)" value={alias} onChange={(e) => setAlias(e.target.value)} />
          <button className="btn" disabled={!name || create.isPending} onClick={() => create.mutate()}>
            Add vendor
          </button>
        </div>
        <p className="muted">
          Aliases use a case-insensitive “contains” match against the raw transaction description.
        </p>
      </div>

      {/* Merge vendors — structural/destructive, so owner only (#334). */}
      {isAdmin && (
        <div className="card">
          <h2 className="card__title">Merge vendors</h2>
          <p className="muted" style={{ marginTop: 0, fontSize: "0.85rem" }}>
            Fold one vendor into another: its transactions and aliases move to the second vendor, then
            the first is deleted — handy for tidying duplicates of the same merchant.
          </p>
          <div className="form-row" style={{ alignItems: "center", flexWrap: "wrap" }}>
            <select
              aria-label="Vendor to merge (absorbed and deleted)"
              value={mergeSource}
              onChange={(e) => setMergeSource(e.target.value)}
            >
              <option value="">Merge…</option>
              {vendorList.map((v) => (
                <option key={v.id} value={v.id}>{v.canonical_name}</option>
              ))}
            </select>
            <span className="muted">into</span>
            <select
              aria-label="Vendor to keep (absorbs the first)"
              value={mergeTarget}
              onChange={(e) => setMergeTarget(e.target.value)}
            >
              <option value="">…another vendor</option>
              {vendorList
                .filter((v) => String(v.id) !== mergeSource)
                .map((v) => (
                  <option key={v.id} value={v.id}>{v.canonical_name}</option>
                ))}
            </select>
            <button
              className="btn"
              disabled={!mergeSource || !mergeTarget || mergeSource === mergeTarget || merge.isPending}
              onClick={confirmMerge}
            >
              {merge.isPending ? "Merging…" : "Merge"}
            </button>
          </div>
        </div>
      )}

      <div className="card">
        <div className="card__head" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
          <h2 className="card__title" style={{ margin: 0 }}>Vendors ({vendorList.length})</h2>
          {vendorList.length > 0 && (
            <label className="muted" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              Sort{" "}
              <select aria-label="Sort vendors" value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
                {SORT_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </label>
          )}
        </div>
        {vendors.isLoading && <p className="muted">Loading…</p>}
        {vendors.data?.length === 0 && (
          <p className="muted">
            No vendors yet. Add one above, or categorise a transaction with “remember vendor”.
          </p>
        )}
        {vendorList.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Vendor</th>
                  <th>Aliases</th>
                  <th>Default category</th>
                  <th>Country</th>
                  <th className="num">Txns</th>
                  <th className="num">Total</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {sortedVendors.map((v: Vendor) => (
                  <tr key={v.id}>
                    <td>{v.canonical_name}</td>
                    <td className="muted">
                      {v.aliases.map((a) => a.alias).join(", ") || "—"}{" "}
                      <AliasAdder onAdd={(value) => addAlias.mutate({ id: v.id, alias: value })} />
                    </td>
                    <td>
                      <select
                        aria-label={`Default category for ${v.canonical_name}`}
                        value={v.default_category_id ?? ""}
                        onChange={(e) =>
                          setCategory.mutate({
                            id: v.id,
                            categoryId: e.target.value ? Number(e.target.value) : null,
                          })
                        }
                      >
                        <option value="">— ({catName(v.default_category_id)})</option>
                        {categories.data?.map((c) => (
                          <option key={c.id} value={c.id}>
                            {c.name}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <CountrySelect
                        value={v.country}
                        onChange={(code) => setCountry.mutate({ id: v.id, country: code })}
                        style={{ minWidth: 140 }}
                      />
                    </td>
                    <td className="num">
                      {v.transaction_count > 0 ? (
                        <Link to={`/transactions?vendor_id=${v.id}`} title={`See ${v.canonical_name} transactions`}>
                          {v.transaction_count}
                        </Link>
                      ) : (
                        v.transaction_count
                      )}
                    </td>
                    <td className="num">{money(Math.abs(Number(v.total_amount)))}</td>
                    <td>
                      <button className="btn btn--ghost" onClick={() => confirmDelete(v)}>
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function AliasAdder({ onAdd }: Readonly<{ onAdd: (alias: string) => void }>) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  if (!open) {
    return (
      <button className="link-btn" onClick={() => setOpen(true)}>
        + alias
      </button>
    );
  }
  return (
    <span className="inline-add">
      <input
        autoFocus
        value={value}
        placeholder="alias"
        aria-label="New alias"
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          const trimmed = value.trim();
          if (e.key === "Enter" && trimmed) {
            onAdd(trimmed);
            setValue("");
            setOpen(false);
          }
          if (e.key === "Escape") setOpen(false);
        }}
      />
    </span>
  );
}
