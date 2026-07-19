import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  approveUser,
  createBudget,
  getAllowanceSummary,
  getMe,
  listAccounts,
  listMembers,
  listUsers,
  updateAccount,
  updateBudget,
  updateUser,
  type Member,
  type User,
} from "../api/client";
import { useOptimisticSelect } from "../hooks/useOptimisticSelect";

const STEPS = ["People", "Shared accounts", "Kids' allowance", "Done"];
const ROLES = ["owner", "member", "viewer", "child"];

// A guided onboarding flow for the owner: approve people + roles, share/keep-private
// accounts, and give kids an allowance — all on top of the existing primitives.
export default function FamilySetup() {
  const qc = useQueryClient();
  const [step, setStep] = useState(0);
  const [err, setErr] = useState<string | null>(null);

  const me = useQuery({ queryKey: ["me"], queryFn: getMe });
  const users = useQuery({ queryKey: ["users"], queryFn: listUsers, enabled: me.data?.is_admin === true });
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: listAccounts });
  const members = useQuery({ queryKey: ["members"], queryFn: listMembers });

  const onErr = (e: unknown) => {
    const msg = String(e instanceof Error ? e.message : e);
    setErr(msg.includes("step_up") ? "That needs a fresh two-factor code — do it from the Users page." : msg);
  };
  // Narrowed from a blanket invalidateQueries(): this wizard only ever changes
  // people/roles, account sharing, and the kids' pocket-money budgets, so refresh
  // just those queries. ["dash-allowance"] is a prefix that matches every child's
  // ["dash-allowance", id] summary.
  const ok = () => {
    setErr(null);
    for (const key of [["users"], ["members"], ["accounts"], ["budget-summary"], ["dash-allowance"]]) {
      qc.invalidateQueries({ queryKey: key });
    }
  };

  const role = useMutation({ mutationFn: (v: { id: number; role: string }) => updateUser(v.id, { role: v.role }), onSuccess: ok, onError: onErr });
  const approve = useMutation({ mutationFn: (id: number) => approveUser(id), onSuccess: ok, onError: onErr });
  const deny = useMutation({ mutationFn: (id: number) => updateUser(id, { status: "disabled" }), onSuccess: ok, onError: onErr });
  const account = useMutation({
    mutationFn: (v: { id: number; patch: { is_shared?: boolean; owner_user_id?: number | null } }) => updateAccount(v.id, v.patch),
    onSuccess: ok,
    onError: onErr,
  });

  // Optimistic overlays for the select-on-change controls (FE-8): each reflects
  // the chosen value immediately and, on a failed mutation, snaps back to the
  // server value. The mutations keep their own onError (onErr) for the message,
  // so the overlays revert silently. One instance per select-kind, keyed by row id.
  const roleSelect = useOptimisticSelect<number, string>();
  const shareSelect = useOptimisticSelect<number, string>();
  const ownerSelect = useOptimisticSelect<number, number | null>();

  if (me.data && !me.data.is_admin) {
    return (
      <div className="page">
        <h1 className="page__title">Family setup</h1>
        <p className="status status--error">Only an owner (administrator) can set up the household.</p>
      </div>
    );
  }

  const allUsers = users.data ?? [];
  const pending = allUsers.filter((u) => u.status === "pending");
  const children = allUsers.filter((u) => u.role === "child" && u.status === "approved");

  return (
    <div className="page">
      <div className="page__head">
        <h1 className="page__title">Family setup</h1>
        <Link className="link-btn" to="/users">Users page →</Link>
      </div>

      <ol className="wizard-steps">
        {STEPS.map((s, i) => {
          const stepClass = i < step ? "is-done" : "";
          return (
            <li key={s} className={i === step ? "is-current" : stepClass}>
              <span className="wizard-steps__num">{i < step ? "✓" : i + 1}</span> {s}
            </li>
          );
        })}
      </ol>

      {err && <p className="status status--error">{err}</p>}

      {step === 0 && (
        <div className="card">
          <h2 className="card__title">1. People &amp; roles</h2>
          <p className="muted">
            People appear here when they open the add-on through Home Assistant. Approve who should have
            access and pick a role: <em>owner</em> (admin), <em>member</em> (read/write), <em>viewer</em>{" "}
            (read-only), or <em>child</em> (allowance-only).
          </p>
          {pending.length > 0 && (
            <p className="status status--warn">{pending.length} person/people awaiting approval.</p>
          )}
          {allUsers.length <= 1 && (
            <p className="muted">
              It's just you so far. Anyone else who opens the add-on will show up here to approve.
            </p>
          )}
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Name</th><th>Role</th><th>Status</th><th></th></tr></thead>
              <tbody>
                {allUsers.map((u: User) => (
                  <tr key={u.id} style={{ opacity: u.status === "disabled" ? 0.55 : 1 }}>
                    <td>{u.display_name}{u.id === me.data?.id && <span className="muted"> (you)</span>}</td>
                    <td>
                      <select
                        value={roleSelect.valueFor(u.id, u.role)}
                        disabled={u.id === me.data?.id}
                        onChange={(e) => {
                          const nextRole = e.target.value;
                          roleSelect.choose(u.id, nextRole, () => role.mutateAsync({ id: u.id, role: nextRole }));
                        }}
                      >
                        {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                      </select>
                    </td>
                    <td>{u.status}</td>
                    <td>
                      {u.status === "pending" && (
                        <>
                          <button className="btn btn--sm" type="button" onClick={() => approve.mutate(u.id)}>Approve</button>{" "}
                          <button className="link-btn" type="button" onClick={() => deny.mutate(u.id)}>deny</button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {step === 1 && (
        <div className="card">
          <h2 className="card__title">2. Shared vs private accounts</h2>
          <p className="muted">
            A <strong>shared</strong> account is visible to the whole household. Mark an account{" "}
            <strong>private</strong> and assign an <strong>owner</strong> to keep it (and its transactions)
            off everyone else's dashboards, budgets and lists — only the owner and you see it.
          </p>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Account</th><th>Visibility</th><th>Owner</th></tr></thead>
              <tbody>
                {(accounts.data ?? []).map((a) => (
                  <tr key={a.id}>
                    <td>{a.name}<span className="muted"> · {a.account_type.replace(/_/g, " ")}</span></td>
                    <td>
                      <select
                        value={shareSelect.valueFor(a.id, a.is_shared ? "shared" : "private")}
                        onChange={(e) => {
                          const nextShared = e.target.value === "shared";
                          shareSelect.choose(a.id, e.target.value, () =>
                            account.mutateAsync({ id: a.id, patch: { is_shared: nextShared } }),
                          );
                        }}
                      >
                        <option value="shared">Shared</option>
                        <option value="private">Private</option>
                      </select>
                    </td>
                    <td>
                      <select
                        value={ownerSelect.valueFor(a.id, a.owner_user_id ?? null) ?? ""}
                        onChange={(e) => {
                          const nextOwner = e.target.value ? Number(e.target.value) : null;
                          ownerSelect.choose(a.id, nextOwner, () =>
                            account.mutateAsync({ id: a.id, patch: { owner_user_id: nextOwner } }),
                          );
                        }}
                      >
                        <option value="">— household —</option>
                        {(members.data ?? []).map((m: Member) => <option key={m.id} value={m.id}>{m.display_name}</option>)}
                      </select>
                    </td>
                  </tr>
                ))}
                {(accounts.data ?? []).length === 0 && (
                  <tr><td colSpan={3} className="muted">No accounts yet — import a statement first (Import page).</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {step === 2 && (
        <div className="card">
          <h2 className="card__title">3. Kids' allowance</h2>
          <p className="muted">
            Give each <em>child</em> a monthly pocket-money budget. Their allowance view then tracks spend
            you assign to them against it — without touching your own books.
          </p>
          {children.length === 0 ? (
            <p className="muted">
              No children yet. Set someone's role to <em>child</em> in step 1, then come back here.
            </p>
          ) : (
            <ul className="kv">
              {children.map((c) => <KidRow key={c.id} child={c} onError={onErr} onDone={ok} />)}
            </ul>
          )}
        </div>
      )}

      {step === 3 && (
        <div className="card">
          <h2 className="card__title">🎉 All set</h2>
          <p className="muted">
            Your household is configured. You can revisit any of this from the <Link to="/users">Users</Link>,
            <Link to="/accounts"> Accounts</Link> and <Link to="/allowance"> Allowance</Link> pages.
          </p>
          <Link className="btn" to="/">Go to the dashboard</Link>
        </div>
      )}

      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 14 }}>
        <button className="btn btn--ghost" type="button" disabled={step === 0} onClick={() => setStep((s) => s - 1)}>← Back</button>
        {step < STEPS.length - 1 && (
          <button className="btn" type="button" onClick={() => setStep((s) => s + 1)}>
            {step === STEPS.length - 2 ? "Finish" : "Next →"}
          </button>
        )}
      </div>
    </div>
  );
}

function KidRow({ child, onError, onDone }: Readonly<{ child: User; onError: (e: unknown) => void; onDone: () => void }>) {
  const summary = useQuery({ queryKey: ["dash-allowance", child.id], queryFn: () => getAllowanceSummary(child.id) });
  const [amount, setAmount] = useState("");
  const existing = summary.data?.budgets[0];
  // Upsert, don't stack: if this child already has a budget, PATCH its amount;
  // otherwise create one. Previously this always POSTed, so re-running the wizard
  // (or editing an amount) piled up duplicate "Pocket money" budgets per child.
  const set = useMutation({
    mutationFn: () =>
      existing
        ? updateBudget(existing.budget_id, { amount })
        : createBudget({ owner_user_id: child.id, name: "Pocket money", period: "monthly", amount }),
    onSuccess: () => { setAmount(""); onDone(); summary.refetch(); },
    onError,
  });
  return (
    <li>
      <span>
        {child.display_name}
        {existing && <span className="muted"> · {existing.spent} / {existing.amount} this {existing.period.replace("ly", "")}</span>}
      </span>
      <span style={{ display: "flex", gap: 6 }}>
        <input
          style={{ width: 90 }}
          placeholder="amount"
          inputMode="decimal"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
        />
        <button className="btn btn--sm" type="button" disabled={!amount || set.isPending} onClick={() => set.mutate()}>
          {existing ? "Update budget" : "Set pocket money"}
        </button>
      </span>
    </li>
  );
}
