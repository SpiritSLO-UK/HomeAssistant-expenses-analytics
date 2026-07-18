import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createCategory,
  deleteCategory,
  getCategoryPrivacyDefault,
  getMe,
  listCategories,
  mergeCategory,
  setAllCategoryPrivacy,
  updateCategory,
  type Category,
} from "../api/client";
import { useConfirm } from "../components/dialogs";

// Cloud-AI privacy levels a category can be set to (spec §22.4, §28). Each level
// has its own icon so the three read at a glance in the chip selector + legend.
const PRIVACY_OPTIONS: { value: string; label: string }[] = [
  { value: "normal", label: "☁️ cloud OK" },
  { value: "sensitive", label: "🛡️ sensitive" },
  { value: "never_cloud", label: "🔒 never cloud" },
];

export default function Categories() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { data, isLoading } = useQuery({ queryKey: ["categories"], queryFn: listCategories });
  const [name, setName] = useState("");
  const [colour, setColour] = useState("#4CAF50");
  const [mergeSource, setMergeSource] = useState("");
  const [mergeTarget, setMergeTarget] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const fail = (e: unknown) => setErr(String(e));

  // Bulk recolour: tick several categories, pick one colour, and apply it to all
  // at once by looping the existing per-category update call over the selection.
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [bulkColour, setBulkColour] = useState("#4CAF50");
  const toggleSelected = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // Who's looking? Cloud-AI privacy is a settings concern (owner / settings-manager);
  // merge + delete are structural (owner only). Hide those controls otherwise — the
  // backend enforces the same, this just keeps them out of sight (backlog #28).
  const me = useQuery({ queryKey: ["me"], queryFn: getMe });
  const canManageSettings = me.data?.can_manage_settings === true;
  const isAdmin = me.data?.is_admin === true;

  // Only fetch the privacy default for users who can actually see the card — the
  // endpoint is settings-manager-gated, so a non-manager would just get a 403.
  const privacyDefault = useQuery({
    queryKey: ["category-privacy"],
    queryFn: getCategoryPrivacyDefault,
    enabled: canManageSettings,
  });

  // One cloud-AI privacy level applied to every category at once (#28); the
  // per-category fine-tuning lives behind the "Advanced" reveal below.
  const applyPrivacy = useMutation({
    mutationFn: (level: string) => setAllCategoryPrivacy(level),
    onSuccess: () => {
      setErr(null);
      qc.invalidateQueries({ queryKey: ["categories"] });
      qc.invalidateQueries({ queryKey: ["category-privacy"] });
    },
    onError: fail,
  });

  const create = useMutation({
    mutationFn: () => createCategory({ name, colour }),
    onSuccess: () => {
      setErr(null);
      setName("");
      qc.invalidateQueries({ queryKey: ["categories"] });
    },
    onError: fail,
  });

  const remove = useMutation({
    mutationFn: (id: number) => deleteCategory(id),
    onSuccess: () => {
      setErr(null);
      qc.invalidateQueries({ queryKey: ["categories"] });
    },
    onError: fail,
  });

  // Merge re-points transactions from the source category onto the target and
  // removes the source, so refresh the category list plus the caches that read
  // category assignments — the transactions list and the dashboard's category /
  // vendor / geo / member breakdowns — rather than blowing away every query.
  const merge = useMutation({
    mutationFn: (v: { source: number; target: number }) => mergeCategory(v.source, v.target),
    onSuccess: () => {
      setErr(null);
      setMergeSource("");
      setMergeTarget("");
      qc.invalidateQueries({ queryKey: ["categories"] });
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["dash-categories"] });
      qc.invalidateQueries({ queryKey: ["dash-vendors"] });
      qc.invalidateQueries({ queryKey: ["dash-geo"] });
      qc.invalidateQueries({ queryKey: ["dash-by-member"] });
      qc.invalidateQueries({ queryKey: ["summary"] });
    },
    onError: fail,
  });

  // Let the user choose what each category may send to cloud AI (#28).
  const setPrivacy = useMutation({
    mutationFn: (v: { id: number; level: string }) =>
      updateCategory(v.id, { privacy_sensitivity: v.level }),
    onSuccess: () => {
      setErr(null);
      qc.invalidateQueries({ queryKey: ["categories"] });
    },
    onError: fail,
  });

  // Apply one colour to every selected category, reusing the same per-category
  // update call — recolouring shifts the dashboard's category swatches too, so
  // refresh that breakdown alongside the list.
  const bulkRecolour = useMutation({
    mutationFn: (ids: number[]) =>
      Promise.all(ids.map((id) => updateCategory(id, { colour: bulkColour }))),
    onSuccess: () => {
      setErr(null);
      setSelected(new Set());
      qc.invalidateQueries({ queryKey: ["categories"] });
      qc.invalidateQueries({ queryKey: ["dash-categories"] });
    },
    onError: fail,
  });

  const cats = data ?? [];
  const nameOf = (id: string) => cats.find((c) => String(c.id) === id)?.name ?? "";

  // Guard against creating a second category with the same name (case-insensitive)
  // — the button stays enabled but we surface a clear message instead of firing a
  // request the backend would reject.
  function submitCreate() {
    const trimmed = name.trim();
    if (!trimmed) return;
    if (cats.some((c) => c.name.trim().toLowerCase() === trimmed.toLowerCase())) {
      setErr(`A category called "${trimmed}" already exists.`);
      return;
    }
    create.mutate();
  }

  async function confirmDelete(c: Category) {
    const msg = c.is_system
      ? `Delete the built-in category "${c.name}"? Transactions using it become uncategorised. ` +
        `You can restore built-ins later with "Import library".`
      : `Delete the category "${c.name}"? Transactions using it become uncategorised.`;
    if (await confirm({ message: msg, confirmLabel: "Delete", danger: true })) remove.mutate(c.id);
  }

  async function confirmMerge() {
    if (!mergeSource || !mergeTarget || mergeSource === mergeTarget) return;
    if (
      await confirm({
        message:
          `Merge "${nameOf(mergeSource)}" into "${nameOf(mergeTarget)}"? ` +
          `Everything in the first moves to the second, then the first is removed.`,
        confirmLabel: "Merge",
      })
    ) {
      merge.mutate({ source: Number(mergeSource), target: Number(mergeTarget) });
    }
  }

  return (
    <div className="page">
      <h1 className="page__title">Categories</h1>
      {err && <p className="status status--error">{err}</p>}

      {/* 1. The library itself — the main thing you look at — with inline add. */}
      <div className="card">
        <h2 className="card__title">Category library ({cats.length})</h2>
        <div className="form-row" style={{ marginBottom: 10 }}>
          <input
            aria-label="New category name"
            placeholder="New category name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <input
            type="color"
            aria-label="Colour for new category"
            value={colour}
            onChange={(e) => setColour(e.target.value)}
            title="Colour"
          />
          <button className="btn" disabled={!name || create.isPending} onClick={submitCreate}>
            Add
          </button>
        </div>
        {isLoading && <p className="muted">Loading…</p>}
        <div className="chips">
          {cats.map((c: Category) => (
            <span key={c.id} className="chip">
              <input
                type="checkbox"
                className="chip__sel"
                aria-label={`Select ${c.name} for bulk recolour`}
                checked={selected.has(c.id)}
                onChange={() => toggleSelected(c.id)}
              />
              <span className="chip__dot" style={{ background: c.colour ?? "#bbb" }} />
              {c.name}
              {advanced && (
                <select
                  className="chip__priv"
                  aria-label={`Cloud-AI privacy for ${c.name}`}
                  value={c.privacy_sensitivity}
                  title="What this category may send to cloud AI — pick 🔒 never cloud to keep it fully on-device"
                  onChange={(e) => setPrivacy.mutate({ id: c.id, level: e.target.value })}
                >
                  {PRIVACY_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              )}
              {isAdmin && (
                <button
                  className="chip__x"
                  aria-label={c.is_system ? `Delete built-in category ${c.name}` : `Delete category ${c.name}`}
                  title={c.is_system ? "Delete (built-in)" : "Delete"}
                  onClick={() => confirmDelete(c)}
                >
                  ×
                </button>
              )}
            </span>
          ))}
        </div>
        {/* Bulk recolour — tick categories above, choose a colour, apply to all. */}
        <div className="form-row" style={{ marginTop: 10, alignItems: "center", flexWrap: "wrap" }}>
          <span className="muted">Bulk recolour ({selected.size} selected):</span>
          <input
            type="color"
            aria-label="Colour to apply to selected categories"
            value={bulkColour}
            onChange={(e) => setBulkColour(e.target.value)}
            title="Colour to apply to selected categories"
          />
          <button
            className="btn btn--sm"
            disabled={selected.size === 0 || bulkRecolour.isPending}
            onClick={() => bulkRecolour.mutate(Array.from(selected))}
          >
            {bulkRecolour.isPending ? "Applying…" : "Apply colour to selected"}
          </button>
          {selected.size > 0 && (
            <button className="btn btn--sm btn--ghost" onClick={() => setSelected(new Set())}>
              Clear selection
            </button>
          )}
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

      {/* 2. Cloud-AI privacy — one level for all, advanced reveal for per-category.
          Owner / settings-manager only (it governs what may leave the device). */}
      {canManageSettings && (
      <div className="card">
        <h2 className="card__title">Cloud-AI privacy</h2>
        <p className="muted" style={{ marginTop: 0, fontSize: "0.85rem" }}>
          Set one level for every category in one go — you don't have to configure each. Fine-tune
          individual categories with “Advanced” (the per-category picker then appears in the library
          above). AI is off by default regardless.
        </p>
        <div className="form-row" style={{ alignItems: "center", flexWrap: "wrap" }}>
          <span className="muted">Apply to all:</span>
          {PRIVACY_OPTIONS.map((o) => (
            <button
              key={o.value}
              className={"btn btn--sm" + (privacyDefault.data?.level === o.value ? "" : " btn--ghost")}
              disabled={applyPrivacy.isPending}
              onClick={async () => {
                if (
                  await confirm({
                    message: `Set every category to “${o.label}”? This overwrites any per-category choices.`,
                  })
                ) {
                  applyPrivacy.mutate(o.value);
                }
              }}
            >
              {o.label}
            </button>
          ))}
        </div>
        <label className="checkbox" style={{ marginTop: 10 }}>
          <input type="checkbox" checked={advanced} onChange={(e) => setAdvanced(e.target.checked)} />{" "}
          Advanced — set the level per category
        </label>
      </div>
      )}

      {/* 3. Merge categories — structural/destructive, so owner only. */}
      {isAdmin && (
      <div className="card">
        <h2 className="card__title">Merge categories</h2>
        <p className="muted" style={{ marginTop: 0, fontSize: "0.85rem" }}>
          Move everything from one category into another, then remove the first — handy for folding
          duplicates or a built-in you don't use into one you do.
        </p>
        <div className="form-row" style={{ alignItems: "center", flexWrap: "wrap" }}>
          <select aria-label="Category to merge from" value={mergeSource} onChange={(e) => setMergeSource(e.target.value)}>
            <option value="">Merge…</option>
            {cats.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
          <span className="muted">into</span>
          <select aria-label="Category to merge into" value={mergeTarget} onChange={(e) => setMergeTarget(e.target.value)}>
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
      )}
    </div>
  );
}
