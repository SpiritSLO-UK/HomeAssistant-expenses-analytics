import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createAllocation, listCategories, listUsers, type Transaction } from "../api/client";

/**
 * Parent action on a transaction: attribute it (whole, or a part) to a child's
 * allowance list — without changing the parent's own books (backlog #82). Hidden
 * when there are no child accounts.
 */
export default function AssignToChildButton({ txn, base }: { txn: Transaction; base: string }) {
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: listUsers });
  const categories = useQuery({ queryKey: ["categories"], queryFn: listCategories });
  const children = (users.data ?? []).filter((u) => u.role === "child");
  const [open, setOpen] = useState(false);
  const [childId, setChildId] = useState("");
  const [amount, setAmount] = useState("");
  const [catId, setCatId] = useState("");

  const assign = useMutation({
    mutationFn: () =>
      createAllocation({
        child_id: Number(childId || children[0]?.id),
        transaction_id: txn.id,
        amount: amount || undefined, // blank = the whole purchase
        category_id: catId ? Number(catId) : txn.category_id,
      }),
    onSuccess: () => {
      setOpen(false);
      setAmount("");
      qc.invalidateQueries({ queryKey: ["allowance"] });
    },
    onError: (e) => globalThis.alert(String(e instanceof Error ? e.message : e)),
  });

  if (children.length === 0) return null;
  if (!open) {
    return (
      <button className="link-btn" style={{ marginLeft: 6 }} title="Show this on a child's allowance" onClick={() => setOpen(true)}>
        → child
      </button>
    );
  }
  return (
    <span style={{ display: "inline-flex", gap: 4, alignItems: "center", marginLeft: 6 }}>
      <select value={childId} onChange={(e) => setChildId(e.target.value)}>
        {children.map((c) => <option key={c.id} value={c.id}>{c.display_name}</option>)}
      </select>
      <select value={catId} onChange={(e) => setCatId(e.target.value)} title="Category (defaults to the transaction's)">
        <option value="">category…</option>
        {categories.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
      </select>
      <input
        placeholder={`part (${base})`}
        value={amount}
        style={{ width: 80 }}
        title="Leave blank to assign the whole purchase"
        onChange={(e) => setAmount(e.target.value)}
      />
      <button className="btn btn--sm" disabled={assign.isPending} onClick={() => assign.mutate()}>
        {assign.isPending ? "…" : "assign"}
      </button>
      <button className="link-btn" onClick={() => setOpen(false)}>×</button>
    </span>
  );
}
