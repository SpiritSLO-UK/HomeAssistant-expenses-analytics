import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { listTags, getTagUsage, mergeTags, deleteUnusedTags } from "../api/client";
import { useConfirm, useAlert } from "../components/dialogs";

// Household-wide tag housekeeping (moved off Settings into its own page). Merge
// duplicate tags and clear out unused ones, with a usage list. The per-transaction
// tagging itself lives on the Transactions page ("+ tag") — this page is only the
// cleanup surface for the merge/usage/prune service functions. Owner-or-manager
// gated (same as the old Settings Tags card): the nav item is `manageSettingsOnly`.
export default function Tags() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const alert = useAlert();
  const tags = useQuery({ queryKey: ["tags"], queryFn: listTags });
  const usage = useQuery({ queryKey: ["tags-usage"], queryFn: getTagUsage });
  const [sourceId, setSourceId] = useState("");
  const [targetId, setTargetId] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const counts = new Map((usage.data ?? []).map((u) => [u.id, u.count]));

  function refresh() {
    qc.invalidateQueries({ queryKey: ["tags"] });
    qc.invalidateQueries({ queryKey: ["tags-usage"] });
    qc.invalidateQueries({ queryKey: ["transactions"] }); // a merge re-points txn tags
  }

  const merge = useMutation({
    mutationFn: (v: { source: number; target: number }) => mergeTags(v.source, v.target),
    onSuccess: (surviving) => {
      setErr(null);
      setMsg(`Tags merged into "${surviving.name}".`);
      setSourceId("");
      setTargetId("");
      refresh();
    },
    onError: (e) => { setMsg(null); setErr(String(e)); },
  });

  const prune = useMutation({
    mutationFn: deleteUnusedTags,
    onSuccess: (r) => {
      refresh();
      const m = r.deleted === 1 ? "Removed 1 unused tag." : `Removed ${r.deleted} unused tags.`;
      void alert({ message: m });
    },
    onError: (e) => { setMsg(null); setErr(String(e)); },
  });

  const nameOf = (id: number): string => tags.data?.find((t) => t.id === id)?.name ?? "tag";

  async function doMerge() {
    const source = Number(sourceId);
    const target = Number(targetId);
    if (!source || !target || source === target) return;
    const ok = await confirm({
      message: `Merge "${nameOf(source)}" into "${nameOf(target)}"? Its transactions move to "${nameOf(target)}" and "${nameOf(source)}" is deleted.`,
      confirmLabel: "Merge",
      danger: true,
    });
    if (ok) merge.mutate({ source, target });
  }

  async function doPrune() {
    const ok = await confirm({
      message: "Remove every tag that no transaction uses?",
      confirmLabel: "Remove unused",
      danger: true,
    });
    if (ok) prune.mutate();
  }

  const list = tags.data ?? [];
  const canMerge = Boolean(sourceId) && Boolean(targetId) && sourceId !== targetId;

  return (
    <div className="page">
      <h1 className="page__title">Tags</h1>

      {msg && <p className="status status--ok">{msg}</p>}
      {err && <p className="status status--error">{err}</p>}

      <div className="card">
        <h2 className="card__title">Tag housekeeping</h2>
        <p className="muted" style={{ marginTop: 0, fontSize: "0.85rem" }}>
          Merge duplicate tags or remove ones no transaction uses. To add or remove a tag on a
          transaction, use the “+ tag” button on the Transactions page.
        </p>
        {list.length === 0 && <p className="muted">No tags yet.</p>}
        {list.length > 0 && (
          <ul className="kv">
            {list.map((t) => (
              <li key={t.id}>
                <span>{t.name}</span>
                <span>{counts.get(t.id) ?? 0}</span>
              </li>
            ))}
          </ul>
        )}
        {list.length > 1 && (
          <div className="form-row" style={{ flexWrap: "wrap", gap: 8, alignItems: "flex-end" }}>
            <label>
              Merge
              <select value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
                <option value="">source tag…</option>
                {list.map((t) => (
                  <option key={t.id} value={t.id}>{t.name}</option>
                ))}
              </select>
            </label>
            <label>
              into
              <select value={targetId} onChange={(e) => setTargetId(e.target.value)}>
                <option value="">target tag…</option>
                {list.map((t) => (
                  <option key={t.id} value={t.id}>{t.name}</option>
                ))}
              </select>
            </label>
            <button className="btn" disabled={!canMerge || merge.isPending} onClick={doMerge}>
              {merge.isPending ? "Merging…" : "Merge"}
            </button>
          </div>
        )}
        <div style={{ marginTop: 12 }}>
          <button className="btn btn--danger" disabled={prune.isPending} onClick={doPrune}>
            {prune.isPending ? "Removing…" : "Remove unused tags"}
          </button>
        </div>
      </div>
    </div>
  );
}
