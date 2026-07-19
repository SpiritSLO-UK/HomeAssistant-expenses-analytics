import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createAllocation, listCategories, listUsers, type Transaction } from "../api/client";
import { isAmount } from "../lib/num";
import { useAlert } from "./dialogs";

/**
 * Parent action on a transaction: attribute it (whole, or a part) to a child's
 * allowance list — without changing the parent's own books (backlog #82). Hidden
 * when there are no child accounts.
 */
export default function AssignToChildButton({ txn, base }: Readonly<{ txn: Transaction; base: string }>) {
  const qc = useQueryClient();
  const alert = useAlert();
  // Reference data rendered per transaction row — a staleTime keeps these from
  // refetching on every row mount / window focus (see main.tsx global default).
  const users = useQuery({ queryKey: ["users"], queryFn: listUsers, staleTime: 60_000 });
  const categories = useQuery({ queryKey: ["categories"], queryFn: listCategories, staleTime: 60_000 });
  const children = (users.data ?? []).filter((u) => u.role === "child");
  const [open, setOpen] = useState(false);
  const [childId, setChildId] = useState("");
  const [amount, setAmount] = useState("");
  const [catId, setCatId] = useState("");

  // The select shows the first child until one is picked, so make that the
  // explicit selected value (not value="") — otherwise the displayed child and
  // the submitted children[0] fallback are only implicitly aligned (React warns
  // about a controlled value with no matching option).
  const effectiveChildId = childId || String(children[0]?.id ?? "");
  // A blank part means "the whole purchase"; a non-blank part must be a valid
  // positive amount before we let it submit.
  const amountOk = amount.trim() === "" || isAmount(amount);

  const assign = useMutation({
    mutationFn: () =>
      createAllocation({
        child_id: Number(effectiveChildId),
        transaction_id: txn.id,
        amount: amount || undefined, // blank = the whole purchase
        category_id: catId ? Number(catId) : txn.category_id,
      }),
    onSuccess: () => {
      setOpen(false);
      setAmount("");
      // The allocation moves money onto the child's allowance and off the parent's
      // books, so refresh every view that reflects it: the allowance lists, the
      // dashboard (incl. its per-child allowance card), and the transactions list.
      qc.invalidateQueries({ queryKey: ["allowance"] });
      qc.invalidateQueries({ queryKey: ["dash-allowance"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      qc.invalidateQueries({ queryKey: ["transactions"] });
    },
    onError: (e) => { alert({ message: String(e instanceof Error ? e.message : e) }); },
  });

  if (children.length === 0) return null;
  if (!open) {
    return (
      <button className="link-btn" type="button" style={{ marginLeft: 6 }} title="Show this on a child's allowance" onClick={() => setOpen(true)}>
        → child
      </button>
    );
  }
  return (
    <span style={{ display: "inline-flex", gap: 4, alignItems: "center", marginLeft: 6 }}>
      <select value={effectiveChildId} onChange={(e) => setChildId(e.target.value)}>
        {children.map((c) => <option key={c.id} value={c.id}>{c.display_name}</option>)}
      </select>
      <select value={catId} onChange={(e) => setCatId(e.target.value)} title="Category (defaults to the transaction's)">
        <option value="">category…</option>
        {categories.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
      </select>
      <input
        placeholder={`part (${base})`}
        value={amount}
        inputMode="decimal"
        style={{ width: 80, ...(amountOk ? {} : { borderColor: "#c0392b" }) }}
        title="Leave blank to assign the whole purchase"
        onChange={(e) => setAmount(e.target.value)}
      />
      <button className="btn btn--sm" type="button" disabled={assign.isPending || !amountOk} onClick={() => assign.mutate()}>
        {assign.isPending ? "…" : "assign"}
      </button>
      <button className="link-btn" type="button" onClick={() => setOpen(false)}>×</button>
    </span>
  );
}
