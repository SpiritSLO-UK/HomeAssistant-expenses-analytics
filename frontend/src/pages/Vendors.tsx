import { useState } from "react";
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

export default function Vendors() {
  const qc = useQueryClient();
  const vendors = useQuery({ queryKey: ["vendors"], queryFn: listVendors });
  const categories = useQuery({ queryKey: ["categories"], queryFn: listCategories });

  const [name, setName] = useState("");
  const [alias, setAlias] = useState("");

  const invalidate = () => qc.invalidateQueries({ queryKey: ["vendors"] });

  const create = useMutation({
    mutationFn: () => createVendor({ canonical_name: name, alias: alias || undefined }),
    onSuccess: () => {
      setName("");
      setAlias("");
      invalidate();
    },
  });
  const addAlias = useMutation({
    mutationFn: (v: { id: number; alias: string }) => addVendorAlias(v.id, v.alias),
    onSuccess: invalidate,
  });
  const setCategory = useMutation({
    mutationFn: (v: { id: number; categoryId: number | null }) =>
      setVendorDefaultCategory(v.id, v.categoryId),
    onSuccess: invalidate,
  });
  const setCountry = useMutation({
    mutationFn: (v: { id: number; country: string | null }) => updateVendor(v.id, { country: v.country }),
    onSuccess: invalidate,
  });
  const remove = useMutation({ mutationFn: (id: number) => deleteVendor(id), onSuccess: invalidate });

  const catName = (id: number | null) =>
    categories.data?.find((c) => c.id === id)?.name ?? "—";

  return (
    <div className="page">
      <h1 className="page__title">Vendors</h1>

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
                      <CountryInput
                        value={v.country}
                        onSave={(code) => setCountry.mutate({ id: v.id, country: code })}
                      />
                    </td>
                    <td className="num">{v.transaction_count}</td>
                    <td className="num">£{Math.abs(Number(v.total_amount)).toFixed(2)}</td>
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

// A tiny 2-letter ISO country input that saves on blur/Enter (e.g. GB, US, FR).
function CountryInput({ value, onSave }: { value: string | null; onSave: (code: string | null) => void }) {
  const [text, setText] = useState(value ?? "");
  const commit = () => {
    const code = text.trim().toUpperCase().slice(0, 2);
    const next = code || null;
    if (next !== (value ?? null)) onSave(next);
  };
  return (
    <input
      placeholder="—"
      value={text}
      maxLength={2}
      style={{ width: 48, textTransform: "uppercase" }}
      title="ISO country code, e.g. GB, US, FR (used by the spending-by-location map)"
      onChange={(e) => setText(e.target.value.replace(/[^A-Za-z]/g, ""))}
      onBlur={commit}
      onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
    />
  );
}

function AliasAdder({ onAdd }: { onAdd: (alias: string) => void }) {
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
