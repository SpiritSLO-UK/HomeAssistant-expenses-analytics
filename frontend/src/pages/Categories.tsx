import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createCategory,
  deleteCategory,
  listCategories,
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

  // Let the user choose what each category may send to cloud AI (#28).
  const setPrivacy = useMutation({
    mutationFn: (v: { id: number; level: string }) =>
      updateCategory(v.id, { privacy_sensitivity: v.level }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["categories"] }),
  });

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
        <h2 className="card__title">Category library ({data?.length ?? 0})</h2>
        {isLoading && <p className="muted">Loading…</p>}
        <div className="chips">
          {data?.map((c: Category) => (
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
              {!c.is_system && (
                <button
                  className="chip__x"
                  title="Delete"
                  onClick={() => remove.mutate(c.id)}
                >
                  ×
                </button>
              )}
            </span>
          ))}
        </div>
        <p className="muted" style={{ marginTop: 12 }}>
          <strong>Cloud-AI privacy</strong> (per category, your choice): <strong>☁️ cloud OK</strong> = may be
          sent to cloud AI (always globally redacted first); <strong>🛡️ sensitive</strong> = extra-redacted
          before any cloud send; <strong>🔒 never cloud</strong> = never sent to a cloud provider, kept
          fully on-device. AI is off by default regardless. System categories can't be deleted here
          (merge/archive arrives in a later stage).
        </p>
      </div>
    </div>
  );
}
