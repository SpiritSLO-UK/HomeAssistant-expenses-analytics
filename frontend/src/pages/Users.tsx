import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  ApiError,
  approveUser,
  deleteUser,
  getMe,
  isStepUpError,
  listUsers,
  mfaStepUp,
  updateUser,
  type User,
} from "../api/client";
import { BLOCKABLE_NAV_ITEMS, navKey } from "../nav";

const ROLES = ["owner", "member", "viewer", "child"];

type UserPatch = {
  role?: string;
  status?: string;
  can_manage_settings?: boolean;
  blocked_nav_keys?: string[];
  mfa_policy?: string;
};
const STATUSES = ["pending", "approved", "disabled"];

const ROLE_HINT: Record<string, string> = {
  owner: "Administrator — full access",
  member: "Read + write finance data",
  viewer: "Read-only",
  child: "Read-only, limited view",
};

export default function Users() {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);

  // Admin actions can require a fresh MFA step-up (#124). Every challenged action
  // enqueues its own replay closure here — a queue, not a single shared slot — so a
  // rapid second action can't clobber the first pending replay. On a successful
  // step-up we drain the queue and replay each challenged action exactly once.
  const stepUpReplays = useRef<Array<() => void>>([]);
  const [stepUpOpen, setStepUpOpen] = useState(false);
  const [stepCode, setStepCode] = useState("");
  // Which user's page-access checklist is open (#108).
  const [restricting, setRestricting] = useState<number | null>(null);

  const me = useQuery({ queryKey: ["me"], queryFn: getMe });
  const users = useQuery({
    queryKey: ["users"],
    queryFn: listUsers,
    enabled: me.data?.is_admin === true,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["users"] });
    qc.invalidateQueries({ queryKey: ["me"] });
  };

  // On a step-up challenge, enqueue the failed action's own replay closure and open
  // the code prompt; on any other failure surface the real error. Each replay is
  // captured per-call from the mutation's own variables, so a rapid second challenge
  // adds to the queue instead of clobbering the first (fixes the shared-slot loss).
  const handleError = (e: unknown, replay: () => void) => {
    if (isStepUpError(e)) {
      stepUpReplays.current.push(replay);
      setStepUpOpen(true);
      return;
    }
    setErr(humanError(e));
  };

  const patch = useMutation({
    mutationFn: (v: { id: number; patch: UserPatch }) => updateUser(v.id, v.patch),
    // Optimistically apply the change to the cached row so the control reflects
    // intent immediately (and lets rapid successive edits build on each other).
    onMutate: async (v) => {
      await qc.cancelQueries({ queryKey: ["users"] });
      const prev = qc.getQueryData<User[]>(["users"]);
      qc.setQueryData<User[]>(["users"], (old) =>
        (old ?? []).map((u) => (u.id === v.id ? { ...u, ...v.patch } : u)),
      );
      return { prev };
    },
    onSuccess: () => setErr(null),
    onError: (e, v, ctx) => {
      if (ctx?.prev) qc.setQueryData(["users"], ctx.prev); // roll back the optimistic edit
      handleError(e, () => patch.mutate(v));
    },
    onSettled: () => invalidate(),
  });
  const approve = useMutation({
    mutationFn: (id: number) => approveUser(id),
    onSuccess: () => { setErr(null); invalidate(); },
    onError: (e, id) => handleError(e, () => approve.mutate(id)),
  });
  const remove = useMutation({
    mutationFn: (id: number) => deleteUser(id),
    onSuccess: () => { setErr(null); invalidate(); },
    onError: (e, id) => handleError(e, () => remove.mutate(id)),
  });

  const stepUp = useMutation({
    mutationFn: () => mfaStepUp(stepCode),
    onSuccess: () => {
      setStepUpOpen(false);
      setStepCode("");
      const replays = stepUpReplays.current;
      stepUpReplays.current = [];
      for (const replay of replays) replay(); // replay every challenged action once
    },
    onError: () => setErr("That code didn't match. Try again."),
  });

  const doPatch = (id: number, p: UserPatch) => patch.mutate({ id, patch: p });
  const doApprove = (id: number) => approve.mutate(id);
  const doRemove = (id: number) => remove.mutate(id);

  if (me.data && !me.data.is_admin) {
    return (
      <div className="page">
        <h1 className="page__title">Users</h1>
        <p className="status status--error">
          Only an owner (administrator) can manage users.
        </p>
      </div>
    );
  }

  const pending = (users.data ?? []).filter((u) => u.status === "pending");
  const restrictingUser =
    restricting === null ? null : ((users.data ?? []).find((u) => u.id === restricting) ?? null);

  return (
    <div className="page">
      <div className="page__head">
        <h1 className="page__title">Users &amp; access</h1>
        <Link className="btn btn--ghost" to="/setup">🧭 Setup wizard</Link>
      </div>
      <p className="muted">
        People are identified by Home Assistant when they open this add-on. A new person
        appears here as <strong>pending</strong> and has no access until you approve them.
        Roles: <em>owner</em> (admin), <em>member</em> (read/write), <em>viewer</em>/<em>child</em> (read-only).
      </p>
      {err && <p className="status status--error">{err}</p>}

      {stepUpOpen && (
        <div className="card" style={{ borderLeft: "3px solid #2d7" }}>
          <h2 className="card__title">🔐 Confirm it's you</h2>
          <p className="muted">
            Admin actions need a fresh two-factor code. Enter the current code to continue —
            your last action will run automatically.
          </p>
          <form
            className="form-row"
            onSubmit={(e) => { e.preventDefault(); if (stepCode) stepUp.mutate(); }}
          >
            <input
              inputMode="numeric"
              autoFocus
              placeholder="123456"
              maxLength={8}
              value={stepCode}
              onChange={(e) => setStepCode(e.target.value.replace(/\D/g, ""))}
              style={{ width: 120 }}
            />
            <button className="btn" type="submit" disabled={!stepCode || stepUp.isPending}>
              {stepUp.isPending ? "Verifying…" : "Verify"}
            </button>
            <button className="btn btn--ghost" type="button" onClick={() => { setStepUpOpen(false); setStepCode(""); stepUpReplays.current = []; }}>
              Cancel
            </button>
          </form>
        </div>
      )}

      {pending.length > 0 && (
        <div className="card" style={{ borderLeft: "3px solid #e0a800" }}>
          <h2 className="card__title">⏳ Awaiting approval ({pending.length})</h2>
          <div className="table-wrap">
            <table className="table">
              <tbody>
                {pending.map((u) => (
                  <tr key={u.id}>
                    <td>{u.display_name}</td>
                    <td className="muted">{u.external_id}</td>
                    <td style={{ textAlign: "right" }}>
                      <button className="btn btn--sm" onClick={() => doApprove(u.id)}>
                        Approve
                      </button>{" "}
                      <button
                        className="link-btn"
                        onClick={() => {
                          if (globalThis.confirm(`Deny "${u.display_name}"? They won't get access.`))
                            doPatch(u.id, { status: "disabled" });
                        }}
                      >
                        deny
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="card">
        {users.isLoading && <p className="muted">Loading…</p>}
        {users.data && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th title="May view + change the general Settings and customise nav tabs">Manage settings</th>
                  <th title="Which pages this person can reach">Pages</th>
                  <th title="Two-factor status + whether it's required">MFA</th>
                  <th>Last seen</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {users.data.map((u: User) => {
                  const isMe = u.id === me.data?.id;
                  return (
                    <tr key={u.id} style={{ opacity: u.status === "disabled" ? 0.55 : 1 }}>
                      <td>
                        {u.display_name}
                        {isMe && <span className="muted"> (you)</span>}
                      </td>
                      <td>
                        <select
                          value={u.role}
                          title={ROLE_HINT[u.role]}
                          onChange={(e) => doPatch(u.id, { role: e.target.value })}
                        >
                          {ROLES.map((r) => (
                            <option key={r} value={r}>{r}</option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <select
                          value={u.status}
                          onChange={(e) => doPatch(u.id, { status: e.target.value })}
                        >
                          {STATUSES.map((s) => (
                            <option key={s} value={s}>{s}</option>
                          ))}
                        </select>
                      </td>
                      <td style={{ textAlign: "center" }}>
                        {u.role === "owner" ? (
                          <span className="muted" title="Owners always manage settings">always</span>
                        ) : (
                          <input
                            type="checkbox"
                            checked={u.can_manage_settings}
                            title="Allow this member to manage the general Settings + nav tabs"
                            onChange={(e) => doPatch(u.id, { can_manage_settings: e.target.checked })}
                          />
                        )}
                      </td>
                      <td style={{ textAlign: "center" }}>
                        {u.role === "owner" ? (
                          <span className="muted" title="Owners can reach every page">all</span>
                        ) : (
                          <button
                            className="link-btn"
                            title="Choose which pages this person can reach"
                            onClick={() => setRestricting(restricting === u.id ? null : u.id)}
                          >
                            {u.blocked_nav_keys.length ? `${u.blocked_nav_keys.length} hidden` : "all"}
                            {restricting === u.id ? " ▲" : " ▾"}
                          </button>
                        )}
                      </td>
                      <td style={{ textAlign: "center", whiteSpace: "nowrap" }}>
                        <span title={u.mfa_enabled ? "Two-factor is set up" : "Not set up yet"}>
                          {u.mfa_enabled ? "🔐" : "—"}
                        </span>{" "}
                        <select
                          value={u.mfa_policy}
                          title="Require two-factor for this user (they're blocked from the app until they enrol)"
                          onChange={(e) => doPatch(u.id, { mfa_policy: e.target.value })}
                        >
                          <option value="optional">optional</option>
                          <option value="required">required</option>
                        </select>
                      </td>
                      <td className="muted">{u.last_seen_at ? u.last_seen_at.replace("T", " ").slice(0, 16) : "—"}</td>
                      <td>
                        {isMe ? (
                          <span className="muted" title="You can't delete your own account">—</span>
                        ) : (
                          <button
                            className="link-btn"
                            onClick={() => {
                              if (globalThis.confirm(`Remove "${u.display_name}"? They lose all access.`))
                                doRemove(u.id);
                            }}
                          >
                            remove
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {restrictingUser && restrictingUser.role !== "owner" && (
        <RestrictPanel
          user={restrictingUser}
          onToggle={(keys) => doPatch(restrictingUser.id, { blocked_nav_keys: keys })}
          onClose={() => setRestricting(null)}
        />
      )}
    </div>
  );
}

// Per-user page-access checklist (#108): tick a page to hide it from this person
// (also blocked server-side). Each toggle persists immediately.
function RestrictPanel({ user, onToggle, onClose }: Readonly<{
  user: User;
  onToggle: (keys: string[]) => void;
  onClose: () => void;
}>) {
  // Track the current selection locally so a rapid second toggle builds on the
  // first instead of on the (still-stale) server prop — otherwise the earlier
  // change is dropped before the refetch lands. Re-sync when the prop settles.
  const [keys, setKeys] = useState<string[]>(user.blocked_nav_keys);
  useEffect(() => {
    setKeys(user.blocked_nav_keys);
  }, [user.blocked_nav_keys]);
  const blocked = new Set(keys);
  const toggle = (key: string) => {
    const next = new Set(keys);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    const arr = [...next];
    setKeys(arr);
    onToggle(arr);
  };
  return (
    <div className="card" style={{ borderLeft: "3px solid #6aa9ff" }}>
      <h2 className="card__title">Hide pages from {user.display_name}</h2>
      <p className="muted">
        Ticked pages are hidden from this person's sidebar <strong>and</strong> blocked server-side.
        Dashboard and Settings always stay available.
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(190px, 1fr))", gap: 6 }}>
        {BLOCKABLE_NAV_ITEMS.map((item) => {
          const key = navKey(item.path);
          return (
            <label key={key} className="checkbox">
              <input type="checkbox" checked={blocked.has(key)} onChange={() => toggle(key)} />{" "}
              {item.icon} {item.label}
            </label>
          );
        })}
      </div>
      <div style={{ marginTop: 10 }}>
        <button className="btn btn--ghost" onClick={onClose}>Done</button>
      </div>
    </div>
  );
}

function humanError(e: unknown): string {
  // Surface a friendlier hint for the last-owner guard specifically; for any other
  // failure show the real server detail rather than mislabelling it "last owner".
  if (e instanceof ApiError) {
    const detail = typeof e.body?.detail === "string" ? e.body.detail : null;
    if (detail?.toLowerCase().includes("last active owner"))
      return "That change isn't allowed (you can't remove the last owner).";
    if (detail) return detail;
  }
  return String(e);
}
