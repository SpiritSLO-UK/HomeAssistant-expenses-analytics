import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createCategory,
  deleteCategory,
  listCategories,
  mergeCategory,
  updateCategory,
  type Category,
} from "../api/client";

// Cloud-AI privacy levels a category can be set to (spec §22.4, §28). Each level
// has its own icon so the three read at a glance in the chip selector + legend.
const PRIVACY_OPTIONS: { value: string; label: string }[] = [
  { value: "normal", label: "☁️ cloud OK" },
  { value: "sensitive", label: "🛡️ sensitive" },
  { value: "never_cloud", label: "🔒 never cloud" },
];

export default function Categories() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["categories"], queryFn: listCategories });
  const [name, setName] = useState("");
  const [colour, setColour] = useState("#4CAF50");
  const [mergeSource, setMergeSource] = useState("");
  const [mergeTarget, setMergeTarget] = useState("");

  const create = useMutation({
    mutationFn: () => createCategory({ name, colour }),
    onSuccess: () => {
      setName("");
      qc.invalidateQueries({ queryKey: ["categories"] });
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => deleteCategory(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["categories"] }),
  });

  // Merge changes category assignments across transactions/budgets/etc, so refresh
  // everything that reads them, not just the category list.
  const merge = useMutation({
    mutationFn: (v: { source: number; target: number }) => mergeCategory(v.source, v.target),
    onSuccess: () => {
      setMergeSource("");
      setMergeTarget("");
      qc.invalidateQueries();
    },
  });

  // Let the user choose what each category may send to cloud AI (#28).
  const setPrivacy = useMutation({
    mutationFn: (v: { id: number; level: string }) =>
      updateCategory(v.id, { privacy_sensitivity: v.level }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["categories"] }),
  });

  const cats = data ?? [];
  const nameOf = (id: string) => cats.find((c) => String(c.id) === id)?.name ?? "";

  function confirmDelete(c: Category) {
    const msg = c.is_system
      ? `Delete the built-in category "${c.name}"? Transactions using it become uncategorised. ` +
        `You can restore built-ins later with "Import library".`
      : `Delete the category "${c.name}"? Transactions using it become uncategorised.`;
    if (window.confirm(msg)) remove.mutate(c.id);
  }

  function confirmMerge() {
    if (!mergeSource || !mergeTarget || mergeSource === mergeTarget) return;
    if (
      window.confirm(
        `Merge "${nameOf(mergeSource)}" into "${nameOf(mergeTarget)}"? ` +
          `Everything in the first moves to the second, then the first is removed.`,
      )
    ) {
      merge.mutate({ source: Number(mergeSource), target: Number(mergeTarget) });
    }
  }

  return (
    <div className="page">
      <h1 className="page__title">Categories</h1>

      <div className="card">
        <h2 className="card__title">Add a category</h2>
        <div className="form-row">
          <input placeholder="Category name" value={name} onChange={(e) => setName(e.target.value)} />
          <input type="color" value={colour} onChange={(e) => setColour(e.target.value)} title="Colour" />
          <button className="btn" disabled={!name || create.isPending} onClick={() => create.mutate()}>
            Add
          </button>
        </div>
      </div>

      <div className="card">
        <h2 className="card__title">Merge categories</h2>
        <p className="muted" style={{ marginTop: 0, fontSize: "0.85rem" }}>
          Move everything from one category into another, then remove the first — handy for folding
          duplicates or a built-in you don't use into one you do.
        </p>
        <div className="form-row" style={{ alignItems: "center", flexWrap: "wrap" }}>
          <select value={mergeSource} onChange={(e) => setMergeSource(e.target.value)}>
            <option value="">Merge…</option>
            {cats.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
          <span className="muted">into</span>
          <select value={mergeTarget} onChange={(e) => setMergeTarget(e.target.value)}>
            <option value="">…another category</option>
            {cats.filter((c) => String(c.id) !== mergeSource).map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
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

      <div className="card">
        <h2 className="card__title">Category library ({cats.length})</h2>
        {isLoading && <p className="muted">Loading…</p>}
        <div className="chips">
          {cats.map((c: Category) => (
            <span key={c.id} className="chip">
              <span className="chip__dot" style={{ background: c.colour ?? "#bbb" }} />
              {c.name}
              <select
                className="chip__priv"
                value={c.privacy_sensitivity}
                title="What this category may send to cloud AI — pick 🔒 never cloud to keep it fully on-device"
                onChange={(e) => setPrivacy.mutate({ id: c.id, level: e.target.value })}
              >
                {PRIVACY_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
              <button
                className="chip__x"
                title={c.is_system ? "Delete (built-in)" : "Delete"}
                onClick={() => confirmDelete(c)}
              >
                ×
              </button>
            </span>
          ))}
        </div>
        <p className="muted" style={{ marginTop: 12 }}>
          <strong>Cloud-AI privacy</strong> (per category, your choice): <strong>☁️ cloud OK</strong> = may be
          sent to cloud AI (always globally redacted first); <strong>🛡️ sensitive</strong> = extra-redacted
          before any cloud send; <strong>🔒 never cloud</strong> = never sent to a cloud provider, kept
          fully on-device. AI is off by default regardless. Any category — including built-ins — can be
          renamed, recoloured, deleted (its transactions become uncategorised) or merged into another;
          deleted built-ins can be restored with “Import library”.
        </p>
      </div>
    </div>
  );
}
