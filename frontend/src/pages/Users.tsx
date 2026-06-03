import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  approveUser,
  deleteUser,
  getMe,
  isStepUpError,
  listUsers,
  mfaStepUp,
  updateUser,
  type User,
} from "../api/client";

const ROLES = ["owner", "member", "viewer", "child"];
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

  // Admin actions can require a fresh MFA step-up (#124). When one is challenged
  // we stash the action and replay it after the user enters a code.
  const lastAction = useRef<(() => void) | null>(null);
  const [stepUpOpen, setStepUpOpen] = useState(false);
  const [stepCode, setStepCode] = useState("");

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

  const onError = (e: unknown) => {
    if (isStepUpError(e)) {
      setStepUpOpen(true); // re-prompt; lastAction replays on success
      return;
    }
    setErr(humanError(e));
  };

  const patch = useMutation({
    mutationFn: (v: { id: number; patch: { role?: string; status?: string; can_manage_settings?: boolean } }) =>
      updateUser(v.id, v.patch),
    onSuccess: () => { setErr(null); invalidate(); },
    onError,
  });
  const approve = useMutation({
    mutationFn: (id: number) => approveUser(id),
    onSuccess: () => { setErr(null); invalidate(); },
    onError,
  });
  const remove = useMutation({
    mutationFn: (id: number) => deleteUser(id),
    onSuccess: () => { setErr(null); invalidate(); },
    onError,
  });

  const stepUp = useMutation({
    mutationFn: () => mfaStepUp(stepCode),
    onSuccess: () => {
      setStepUpOpen(false);
      setStepCode("");
      lastAction.current?.(); // replay the challenged action
    },
    onError: () => setErr("That code didn't match. Try again."),
  });

  // Record then run an admin action so it can be replayed after a step-up.
  const doPatch = (id: number, p: { role?: string; status?: string; can_manage_settings?: boolean }) => {
    lastAction.current = () => patch.mutate({ id, patch: p });
    patch.mutate({ id, patch: p });
  };
  const doApprove = (id: number) => {
    lastAction.current = () => approve.mutate(id);
    approve.mutate(id);
  };
  const doRemove = (id: number) => {
    lastAction.current = () => remove.mutate(id);
    remove.mutate(id);
  };

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
              onChange={(e) => setStepCode(e.target.value.replace(/[^0-9]/g, ""))}
              style={{ width: 120 }}
            />
            <button className="btn" type="submit" disabled={!stepCode || stepUp.isPending}>
              {stepUp.isPending ? "Verifying…" : "Verify"}
            </button>
            <button className="btn btn--ghost" type="button" onClick={() => { setStepUpOpen(false); setStepCode(""); }}>
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
                        onClick={() => doPatch(u.id, { status: "disabled" })}
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
                      <td className="muted">{u.last_seen_at ? u.last_seen_at.replace("T", " ").slice(0, 16) : "—"}</td>
                      <td>
                        {isMe ? (
                          <span className="muted" title="You can't delete your own account">—</span>
                        ) : (
                          <button
                            className="link-btn"
                            onClick={() => {
                              if (window.confirm(`Remove "${u.display_name}"? They lose all access.`))
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
    </div>
  );
}

function humanError(e: unknown): string {
  const msg = String(e);
  // fetchJson throws "API ... failed: 400 ...". Surface a friendlier hint for the
  // last-owner guard, which is the common 400 here.
  if (msg.includes("400")) return "That change isn't allowed (you can't remove the last owner).";
  return msg;
}
