import { useState } from "react";
import { Route, Routes } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Sidebar from "./components/Sidebar";
import Dashboard from "./pages/Dashboard";
import Import from "./pages/Import";
import Transactions from "./pages/Transactions";
import Categories from "./pages/Categories";
import Vendors from "./pages/Vendors";
import Rules from "./pages/Rules";
import Projects from "./pages/Projects";
import Budgets from "./pages/Budgets";
import Subscriptions from "./pages/Subscriptions";
import Receipts from "./pages/Receipts";
import ReviewQueue from "./pages/ReviewQueue";
import Settings from "./pages/Settings";
import Users from "./pages/Users";
import { getMe, getSecurityStatus, mfaVerify, unlockDatabase } from "./api/client";

export default function App() {
  // If the database is encrypted and locked, gate the whole app behind unlock.
  const status = useQuery({ queryKey: ["security-status"], queryFn: getSecurityStatus });
  // Who is using the app (resolved from HA ingress identity). Drives the
  // approval gate and the owner-only nav.
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, enabled: !status.data?.locked });

  if (status.data?.locked) {
    return <UnlockGate failedRecent={status.data.failed_unlocks?.recent ?? 0} />;
  }

  if (me.data && me.data.status !== "approved") {
    return <AccountGate status={me.data.status} name={me.data.display_name} />;
  }

  if (me.data?.mfa_required) {
    return <MfaGate />;
  }

  return (
    <div className="layout">
      <Sidebar isAdmin={me.data?.is_admin ?? false} />
      <main className="content">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/import" element={<Import />} />
          <Route path="/transactions" element={<Transactions />} />
          <Route path="/categories" element={<Categories />} />
          <Route path="/vendors" element={<Vendors />} />
          <Route path="/rules" element={<Rules />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/budgets" element={<Budgets />} />
          <Route path="/subscriptions" element={<Subscriptions />} />
          <Route path="/receipts" element={<Receipts />} />
          <Route path="/review" element={<ReviewQueue />} />
          <Route path="/users" element={<Users />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Dashboard />} />
        </Routes>
      </main>
    </div>
  );
}

function MfaGate() {
  const qc = useQueryClient();
  const [code, setCode] = useState("");
  const verify = useMutation({
    mutationFn: () => mfaVerify(code),
    onSuccess: () => {
      setCode("");
      qc.invalidateQueries(); // refetch /me (clears the gate) + all data
    },
  });

  return (
    <div className="unlock">
      <div className="unlock__card">
        <h1>🔐 Two-factor verification</h1>
        <p className="muted">Enter the 6-digit code from your authenticator app.</p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (code) verify.mutate();
          }}
        >
          <input
            inputMode="numeric"
            autoFocus
            placeholder="123456"
            value={code}
            maxLength={8}
            onChange={(e) => setCode(e.target.value.replace(/[^0-9]/g, ""))}
          />
          <button className="btn" type="submit" disabled={!code || verify.isPending}>
            {verify.isPending ? "Verifying…" : "Verify"}
          </button>
        </form>
        {verify.isError && <p className="status status--error">That code didn't match. Try again.</p>}
      </div>
    </div>
  );
}

function AccountGate({ status, name }: { status: string; name: string }) {
  const pending = status === "pending";
  return (
    <div className="unlock">
      <div className="unlock__card">
        <h1>{pending ? "⏳ Awaiting approval" : "🚫 Access disabled"}</h1>
        <p className="muted">
          {pending ? (
            <>
              Hi {name} — your account is waiting for an administrator to approve it.
              You'll have access as soon as they do.
            </>
          ) : (
            <>Your account has been disabled. Contact the household owner if this is unexpected.</>
          )}
        </p>
      </div>
    </div>
  );
}

function UnlockGate({ failedRecent = 0 }: { failedRecent?: number }) {
  const qc = useQueryClient();
  const [passphrase, setPassphrase] = useState("");
  const unlock = useMutation({
    mutationFn: () => unlockDatabase(passphrase),
    onSuccess: () => {
      setPassphrase("");
      qc.invalidateQueries(); // refetch status + all data now that we're unlocked
    },
  });

  return (
    <div className="unlock">
      <div className="unlock__card">
        <h1>🔒 Database locked</h1>
        <p className="muted">
          This database is encrypted. Enter your passphrase to unlock it for this session.
        </p>
        {failedRecent > 0 && (
          <p className="status status--warn">
            ⚠️ {failedRecent} failed unlock attempt{failedRecent > 1 ? "s" : ""} in the last hour.
          </p>
        )}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (passphrase) unlock.mutate();
          }}
        >
          <input
            type="password"
            autoFocus
            placeholder="Passphrase"
            value={passphrase}
            onChange={(e) => setPassphrase(e.target.value)}
          />
          <button className="btn" type="submit" disabled={!passphrase || unlock.isPending}>
            {unlock.isPending ? "Unlocking…" : "Unlock"}
          </button>
        </form>
        {unlock.isError && <p className="status status--error">Wrong passphrase.</p>}
        <p className="muted" style={{ fontSize: "0.78rem" }}>
          Lost the passphrase? The data cannot be recovered.
        </p>
      </div>
    </div>
  );
}
