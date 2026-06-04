import { useState, type ReactNode } from "react";
import { Route, Routes } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Sidebar from "./components/Sidebar";
import Dashboard from "./pages/Dashboard";
import Search from "./pages/Search";
import Import from "./pages/Import";
import Transactions from "./pages/Transactions";
import Categories from "./pages/Categories";
import Vendors from "./pages/Vendors";
import Rules from "./pages/Rules";
import Projects from "./pages/Projects";
import Travel from "./pages/Travel";
import Business from "./pages/Business";
import Budgets from "./pages/Budgets";
import Savings from "./pages/Savings";
import Investments from "./pages/Investments";
import Accounts from "./pages/Accounts";
import Assets from "./pages/Assets";
import Allowance from "./pages/Allowance";
import Subscriptions from "./pages/Subscriptions";
import Receipts from "./pages/Receipts";
import ReviewQueue from "./pages/ReviewQueue";
import Settings from "./pages/Settings";
import Users from "./pages/Users";
import FamilySetup from "./pages/FamilySetup";
import Setup from "./pages/Setup";
import Logs from "./pages/Logs";
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

  // The child role is a narrow allowance view — only that route is mounted.
  if (me.data?.role === "child") {
    return (
      <AppShell role="child">
        <Routes>
          <Route path="/allowance" element={<Allowance />} />
          <Route path="*" element={<Allowance />} />
        </Routes>
      </AppShell>
    );
  }

  return (
    <AppShell role={me.data?.role ?? "owner"} canManageTabs={me.data?.can_manage_settings ?? false}>
      <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/search" element={<Search />} />
          <Route path="/import" element={<Import />} />
          <Route path="/transactions" element={<Transactions />} />
          <Route path="/categories" element={<Categories />} />
          <Route path="/vendors" element={<Vendors />} />
          <Route path="/rules" element={<Rules />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/travel" element={<Travel />} />
          <Route path="/business" element={<Business />} />
          <Route path="/budgets" element={<Budgets />} />
          <Route path="/savings" element={<Savings />} />
          <Route path="/investments" element={<Investments />} />
          <Route path="/accounts" element={<Accounts />} />
          <Route path="/assets" element={<Assets />} />
          <Route path="/allowance" element={<Allowance />} />
          <Route path="/subscriptions" element={<Subscriptions />} />
          <Route path="/receipts" element={<Receipts />} />
          <Route path="/review" element={<ReviewQueue />} />
          <Route path="/users" element={<Users />} />
          <Route path="/setup" element={<Setup />} />
          <Route path="/family-setup" element={<FamilySetup />} />
          <Route path="/logs" element={<Logs />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Dashboard />} />
      </Routes>
    </AppShell>
  );
}

function AppShell({
  role,
  canManageTabs = false,
  children,
}: Readonly<{
  role: string;
  canManageTabs?: boolean;
  children: ReactNode;
}>) {
  const [navOpen, setNavOpen] = useState(false);
  return (
    <div className="layout">
      <Sidebar role={role} canManageTabs={canManageTabs} open={navOpen} onNavigate={() => setNavOpen(false)} />
      {navOpen && (
        <button className="nav-backdrop" aria-label="Close menu" onClick={() => setNavOpen(false)} />
      )}
      <div className="content-col">
        <div className="mobile-topbar">
          <button className="hamburger" aria-label="Open menu" onClick={() => setNavOpen(true)}>
            ☰
          </button>
          <span className="mobile-topbar__brand">💷 Finance</span>
        </div>
        <main className="content">{children}</main>
      </div>
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
            onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
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

function AccountGate({ status, name }: Readonly<{ status: string; name: string }>) {
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

function UnlockGate({ failedRecent = 0 }: Readonly<{ failedRecent?: number }>) {
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
