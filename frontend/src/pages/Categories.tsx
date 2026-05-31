import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createCategory,
  deleteCategory,
  listCategories,
  type Category,
} from "../api/client";

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
              {c.privacy_sensitivity !== "normal" && (
                <span className="chip__badge" title={c.privacy_sensitivity}>
                  {c.privacy_sensitivity === "never_cloud" ? "🔒" : "•"}
                </span>
              )}
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
          🔒 = never sent to cloud AI. System categories from the library can't be deleted here
          (merge/archive arrives in a later stage).
        </p>
      </div>
    </div>
  );
}
