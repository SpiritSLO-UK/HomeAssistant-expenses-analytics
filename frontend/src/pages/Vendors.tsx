import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addVendorAlias,
  createVendor,
  deleteVendor,
  listCategories,
  listVendors,
  setVendorDefaultCategory,
  updateVendor,
  type Vendor,
} from "../api/client";
import CountrySelect from "../components/CountrySelect";
import { money } from "../lib/money";

export default function Vendors() {
  const qc = useQueryClient();
  const vendors = useQuery({ queryKey: ["vendors"], queryFn: listVendors });
  const categories = useQuery({ queryKey: ["categories"], queryFn: listCategories });

  const [name, setName] = useState("");
  const [alias, setAlias] = useState("");

  const [err, setErr] = useState<string | null>(null);
  const fail = (e: unknown) => setErr(String(e));
  // Cleared on success too, so a stale banner doesn't outlive a later success (FE-3).
  const invalidate = () => {
    setErr(null);
    qc.invalidateQueries({ queryKey: ["vendors"] });
  };

  const create = useMutation({
    mutationFn: () => createVendor({ canonical_name: name, alias: alias || undefined }),
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

  const catName = (id: number | null) =>
    categories.data?.find((c) => c.id === id)?.name ?? "—";

  return (
    <div className="page">
      <h1 className="page__title">Vendors</h1>
      {err && <p className="status status--error">{err}</p>}

      <div className="card">
        <h2 className="card__title">Add a vendor</h2>
        <div className="form-row">
          <input placeholder="Canonical name (e.g. Tesco)" value={name} onChange={(e) => setName(e.target.value)} />
          <input placeholder="Alias to match (e.g. TESCO)" value={alias} onChange={(e) => setAlias(e.target.value)} />
          <button className="btn" disabled={!name || create.isPending} onClick={() => create.mutate()}>
            Add vendor
          </button>
        </div>
        <p className="muted">
          Aliases use a case-insensitive “contains” match against the raw transaction description.
        </p>
      </div>

      <div className="card">
        <h2 className="card__title">Vendors ({vendors.data?.length ?? 0})</h2>
        {vendors.isLoading && <p className="muted">Loading…</p>}
        {vendors.data?.length === 0 && (
          <p className="muted">
            No vendors yet. Add one above, or categorise a transaction with “remember vendor”.
          </p>
        )}
        {vendors.data && vendors.data.length > 0 && (
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
                {vendors.data.map((v: Vendor) => (
                  <tr key={v.id}>
                    <td>{v.canonical_name}</td>
                    <td className="muted">
                      {v.aliases.map((a) => a.alias).join(", ") || "—"}{" "}
                      <AliasAdder onAdd={(value) => addAlias.mutate({ id: v.id, alias: value })} />
                    </td>
                    <td>
                      <select
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
                      <button className="btn btn--ghost" onClick={() => remove.mutate(v.id)}>
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
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && value) {
            onAdd(value);
            setValue("");
            setOpen(false);
          }
          if (e.key === "Escape") setOpen(false);
        }}
      />
    </span>
  );
}
