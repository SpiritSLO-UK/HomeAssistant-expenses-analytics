import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ACCOUNT_TYPES,
  createAccount,
  deleteAccount,
  getMe,
  listAccounts,
  listUsers,
  mergeAccount,
  updateAccount,
  type Account,
  type User,
} from "../api/client";
import { useServerState } from "../lib/useServerState";

function badge(a: Account): { label: string; colour: string } {
  if (a.owner_user_id === null) return { label: "Shared · household", colour: "#3aa55a" };
  if (a.is_shared) return { label: `Shared · ${a.owner_name ?? "owned"}`, colour: "#6aa9ff" };
  return { label: `Private · ${a.owner_name ?? "owner"}`, colour: "#e0a800" };
}

const alertErr = (e: unknown) => globalThis.alert(String(e instanceof Error ? e.message : e));

function useInvalidateAccounts() {
  const qc = useQueryClient();
  return () => {
    for (const key of ["accounts", "transactions", "dashboard", "savings-summary", "investments"]) {
      qc.invalidateQueries({ queryKey: [key] });
    }
  };
}

export default function Accounts() {
  const me = useQuery({ queryKey: ["me"], queryFn: getMe });
  const isAdmin = me.data?.is_admin === true;
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: listAccounts });
  const users = useQuery({ queryKey: ["users"], queryFn: listUsers, enabled: isAdmin });

  return (
    <div className="page">
      <div className="page__head">
        <h1 className="page__title">Accounts</h1>
      </div>
      <p className="muted">
        Mark an account <strong>private</strong> to keep it (and its transactions) off everyone else's
        dashboards, budgets, exports and lists — only you and the household owner see it. Accounts left
        as <strong>shared</strong> are visible to every approved member. {isAdmin
          ? "As the owner you can assign an account to a person, and add, rename, delete or merge accounts."
          : "You can add an account and change the ones you own."}
      </p>

      <NewAccountCard isAdmin={isAdmin} />

      {accounts.isLoading && <p className="muted">Loading…</p>}
      {accounts.data?.length === 0 && (
        <div className="card"><p className="muted">No accounts yet — add one above, or they're created when you import a statement.</p></div>
      )}
      {accounts.data && accounts.data.length > 0 && (
        <div className="card">
          {accounts.data.map((a) => (
            <AccountRow
              key={a.id}
              account={a}
              isAdmin={isAdmin}
              meId={me.data?.id ?? -1}
              users={users.data ?? []}
              accounts={accounts.data ?? []}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function NewAccountCard({ isAdmin }: Readonly<{ isAdmin: boolean }>) {
  const invalidate = useInvalidateAccounts();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [type, setType] = useState("current_account");
  const [currency, setCurrency] = useState("");

  const create = useMutation({
    mutationFn: () => createAccount({ name: name.trim(), account_type: type, currency: currency.trim() || undefined }),
    onSuccess: () => { invalidate(); setName(""); setCurrency(""); setType("current_account"); setOpen(false); },
    onError: alertErr,
  });

  if (!open) {
    return (
      <div style={{ margin: "8px 0" }}>
        <button className="btn" onClick={() => setOpen(true)}>➕ New account</button>
      </div>
    );
  }
  return (
    <div className="card">
      <h2 className="card__title">New account</h2>
      <div className="form-row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <input placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        <select value={type} onChange={(e) => setType(e.target.value)}>
          {ACCOUNT_TYPES.map((t) => <option key={t} value={t}>{t.replace("_", " ")}</option>)}
        </select>
        <input
          placeholder="Currency (optional)"
          maxLength={3}
          value={currency}
          onChange={(e) => setCurrency(e.target.value.toUpperCase())}
          style={{ width: 150 }}
        />
        <button className="btn" disabled={!name.trim() || create.isPending} onClick={() => create.mutate()}>
          {create.isPending ? "Adding…" : "Add account"}
        </button>
        <button className="btn btn--ghost" onClick={() => setOpen(false)}>Cancel</button>
      </div>
      <p className="muted" style={{ fontSize: "0.85rem", marginBottom: 0 }}>
        {isAdmin
          ? "Created as a shared household account — assign an owner below if it's personal."
          : "Created as your own private account."}
      </p>
    </div>
  );
}

function AccountRow({ account, isAdmin, meId, users, accounts }: Readonly<{
  account: Account;
  isAdmin: boolean;
  meId: number;
  users: User[];
  accounts: Account[];
}>) {
  const invalidate = useInvalidateAccounts();
  const canEdit = isAdmin || account.owner_user_id === meId;
  const b = badge(account);
  const [renaming, setRenaming] = useState(false);
  const [newName, setNewName] = useServerState(account.name);

  const patch = useMutation({
    mutationFn: (p: { name?: string; is_shared?: boolean; owner_user_id?: number | null }) =>
      updateAccount(account.id, p),
    onSuccess: () => { invalidate(); setRenaming(false); },
    onError: alertErr,
  });

  return (
    <div style={{ borderTop: "1px solid #2a2a2a", padding: "10px 0", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
      <div>
        {renaming ? (
          <span className="form-row" style={{ gap: 6, alignItems: "center" }}>
            <input value={newName} onChange={(e) => setNewName(e.target.value)} autoFocus />
            <button className="btn" disabled={!newName.trim() || patch.isPending} onClick={() => patch.mutate({ name: newName.trim() })}>Save</button>
            <button className="btn btn--ghost" onClick={() => { setRenaming(false); setNewName(account.name); }}>Cancel</button>
          </span>
        ) : (
          <>
            <strong>{account.name}</strong>{" "}
            <span className="muted">· {account.account_type.replace("_", " ")} · {account.currency}</span>
            <div><span className="tag" style={{ background: b.colour }}>{b.label}</span></div>
          </>
        )}
      </div>

      <div className="form-row" style={{ gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        {canEdit && !renaming && (
          <button className="btn btn--ghost" onClick={() => { setNewName(account.name); setRenaming(true); }}>Rename</button>
        )}
        {isAdmin && (
          <label className="muted">
            Owner{" "}
            <select
              value={account.owner_user_id ?? ""}
              onChange={(e) => patch.mutate({ owner_user_id: e.target.value ? Number(e.target.value) : null })}
              disabled={patch.isPending}
            >
              <option value="">Household (shared)</option>
              {users.map((u) => <option key={u.id} value={u.id}>{u.display_name}</option>)}
            </select>
          </label>
        )}
        {canEdit && account.owner_user_id !== null && (
          <label className="checkbox">
            <input
              type="checkbox"
              checked={account.is_shared}
              disabled={patch.isPending}
              onChange={(e) => patch.mutate({ is_shared: e.target.checked })}
            />{" "}
            Shared with household
          </label>
        )}
        {isAdmin && <AccountAdminControls account={account} accounts={accounts} />}
      </div>
    </div>
  );
}

// Delete (empty accounts) or merge (accounts that still have data) — owner-only.
function AccountAdminControls({ account, accounts }: Readonly<{ account: Account; accounts: Account[] }>) {
  const invalidate = useInvalidateAccounts();
  const [mergeTarget, setMergeTarget] = useState<number | "">("");
  const others = accounts.filter((a) => a.id !== account.id);

  const del = useMutation({
    mutationFn: () => deleteAccount(account.id),
    onSuccess: invalidate,
    onError: alertErr,
  });
  const merge = useMutation({
    mutationFn: (target: number) => mergeAccount(account.id, target),
    onSuccess: () => { invalidate(); setMergeTarget(""); },
    onError: alertErr,
  });

  if (!account.in_use) {
    return (
      <button
        className="btn btn--ghost"
        disabled={del.isPending}
        onClick={() => {
          if (globalThis.confirm(`Delete account "${account.name}"? This can't be undone.`)) del.mutate();
        }}
      >
        {del.isPending ? "Deleting…" : "Delete"}
      </button>
    );
  }
  if (others.length === 0) return null; // has data but nowhere to merge into
  return (
    <span className="form-row" style={{ gap: 4, alignItems: "center" }}>
      <select
        value={mergeTarget}
        onChange={(e) => setMergeTarget(e.target.value ? Number(e.target.value) : "")}
        title="This account still has data, so it can't be deleted — merge it into another."
      >
        <option value="">Merge into…</option>
        {others.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
      </select>
      <button
        className="btn btn--ghost"
        disabled={mergeTarget === "" || merge.isPending}
        onClick={() => {
          const t = others.find((o) => o.id === mergeTarget);
          if (t && globalThis.confirm(`Move everything from "${account.name}" into "${t.name}", then delete "${account.name}"?`)) {
            merge.mutate(mergeTarget as number);
          }
        }}
      >
        {merge.isPending ? "Merging…" : "Merge"}
      </button>
    </span>
  );
}
