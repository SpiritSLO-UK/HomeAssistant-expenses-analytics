import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  getMe,
  listAccounts,
  listUsers,
  updateAccount,
  type Account,
  type User,
} from "../api/client";

function badge(a: Account): { label: string; colour: string } {
  if (a.owner_user_id === null) return { label: "Shared · household", colour: "#3aa55a" };
  if (a.is_shared) return { label: `Shared · ${a.owner_name ?? "owned"}`, colour: "#6aa9ff" };
  return { label: `Private · ${a.owner_name ?? "owner"}`, colour: "#e0a800" };
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
          ? "As the owner you can assign an account to a person and see everything."
          : "You can change the accounts you own."}
      </p>

      {accounts.isLoading && <p className="muted">Loading…</p>}
      {accounts.data && accounts.data.length === 0 && (
        <div className="card"><p className="muted">No accounts yet — they're created when you import a statement.</p></div>
      )}
      {accounts.data && accounts.data.length > 0 && (
        <div className="card">
          {accounts.data.map((a) => (
            <AccountRow key={a.id} account={a} isAdmin={isAdmin} meId={me.data?.id ?? -1} users={users.data ?? []} />
          ))}
        </div>
      )}
    </div>
  );
}

function AccountRow({ account, isAdmin, meId, users }: Readonly<{
  account: Account;
  isAdmin: boolean;
  meId: number;
  users: User[];
}>) {
  const qc = useQueryClient();
  const canEdit = isAdmin || account.owner_user_id === meId;
  const b = badge(account);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["accounts"] });
    qc.invalidateQueries({ queryKey: ["transactions"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
    qc.invalidateQueries({ queryKey: ["savings-summary"] });
  };
  const patch = useMutation({
    mutationFn: (p: { is_shared?: boolean; owner_user_id?: number | null }) => updateAccount(account.id, p),
    onSuccess: invalidate,
    onError: (e) => globalThis.alert(String(e instanceof Error ? e.message : e)),
  });

  return (
    <div style={{ borderTop: "1px solid #2a2a2a", padding: "10px 0", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
      <div>
        <strong>{account.name}</strong>{" "}
        <span className="muted">· {account.account_type.replace("_", " ")} · {account.currency}</span>
        <div>
          <span className="tag" style={{ background: b.colour }}>{b.label}</span>
        </div>
      </div>

      <div className="form-row" style={{ gap: 8, alignItems: "center" }}>
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
      </div>
    </div>
  );
}
