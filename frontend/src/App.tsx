import { lazy, Suspense, useState, type ReactNode } from "react";
import { Route, Routes } from "react-router-dom";
import { useMutation, useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { QRCodeSVG } from "qrcode.react";
import Sidebar from "./components/Sidebar";
// Route targets are code-split with React.lazy so each page ships as its own chunk,
// fetched on navigation rather than baked into the initial bundle. This shrinks the
// first download (helpful over HA ingress on a Pi) and complements the vendor split.
// Only the shell (Sidebar, gates, dialog provider) stays eager, so it renders instantly.
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Search = lazy(() => import("./pages/Search"));
const Import = lazy(() => import("./pages/Import"));
const Transactions = lazy(() => import("./pages/Transactions"));
const Categories = lazy(() => import("./pages/Categories"));
const Vendors = lazy(() => import("./pages/Vendors"));
const Rules = lazy(() => import("./pages/Rules"));
const Projects = lazy(() => import("./pages/Projects"));
const Travel = lazy(() => import("./pages/Travel"));
const Business = lazy(() => import("./pages/Business"));
const Budgets = lazy(() => import("./pages/Budgets"));
const Savings = lazy(() => import("./pages/Savings"));
const Investments = lazy(() => import("./pages/Investments"));
const Accounts = lazy(() => import("./pages/Accounts"));
const Assets = lazy(() => import("./pages/Assets"));
const Energy = lazy(() => import("./pages/Energy"));
const Allowance = lazy(() => import("./pages/Allowance"));
const Subscriptions = lazy(() => import("./pages/Subscriptions"));
const Receipts = lazy(() => import("./pages/Receipts"));
const ReviewQueue = lazy(() => import("./pages/ReviewQueue"));
const Settings = lazy(() => import("./pages/Settings"));
const Users = lazy(() => import("./pages/Users"));
const FamilySetup = lazy(() => import("./pages/FamilySetup"));
const Setup = lazy(() => import("./pages/Setup"));
const Logs = lazy(() => import("./pages/Logs"));
import { ApiError, getMe, getSecurityStatus, getSettings, mfaEnable, mfaSetup, mfaVerify, unlockDatabase, type Me, type SecurityStatus } from "./api/client";
import { setDisplayCurrency } from "./lib/money";
import { NAV_ITEMS } from "./nav";
import { DialogProvider } from "./components/dialogs";

// Mount the in-app dialog system once, above everything — every gate, shell and
// page (and the non-React AI-approval gate) shares this single modal (FE-10).
export default function App() {
  return (
    <DialogProvider>
      <AppRoutes />
    </DialogProvider>
  );
}

function AppRoutes() {
  // If the database is encrypted and locked, gate the whole app behind unlock.
  const status = useQuery({ queryKey: ["security-status"], queryFn: getSecurityStatus });
  // Who is using the app (resolved from HA ingress identity). Drives the
  // approval gate and the owner-only nav. Gated on the security status having
  // resolved (and the DB being unlocked) so we don't fire a wasted /me request
  // before we even know whether the app is locked.
  const me = useQuery({
    queryKey: ["me"],
    queryFn: getMe,
    enabled: status.isSuccess && !status.data?.locked,
  });
  // Keep the app-wide display currency in sync with the configured base currency, so
  // money() renders the right symbol everywhere (FE-5). Set during render so it's
  // current before any child card formats — App re-renders when settings resolve or
  // the base currency changes, cascading fresh values down.
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: getSettings,
    enabled: status.isSuccess && !status.data?.locked && me.data?.status === "approved",
  });
  setDisplayCurrency(settings.data?.base_currency);

  // Resolve the lock/identity state before rendering any real shell, so the
  // owner-only UI can't flash for a non-owner (or a locked DB) while these load,
  // and a failed load shows a recoverable error instead of a blank hang.
  const gate = resolveGate(status, me);
  if (gate) {
    return gate;
  }

  // The child role is a narrow view: mount only the routes flagged `childVisible`
  // in the nav config, so the sidebar (which filters by the same flag) and the
  // router can't drift apart. `childPages` is the single place to wire a new
  // child-visible page to its component.
  if (me.data?.role === "child") {
    const childPages: Record<string, ReactNode> = { "/allowance": <Allowance /> };
    const childItems = NAV_ITEMS.filter((i) => i.childVisible && childPages[i.path]);
    return (
      <AppShell role="child">
        <Suspense fallback={<RouteFallback />}>
          <Routes>
            {childItems.map((i) => <Route key={i.path} path={i.path} element={childPages[i.path]} />)}
            <Route path="*" element={<Allowance />} />
          </Routes>
        </Suspense>
      </AppShell>
    );
  }

  return (
    <AppShell
      role={me.data?.role ?? "owner"}
      canManageTabs={me.data?.can_manage_settings ?? false}
      blockedNavKeys={me.data?.blocked_nav_keys ?? []}
    >
      <Suspense fallback={<RouteFallback />}>
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
          <Route path="/energy" element={<Energy />} />
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
      </Suspense>
    </AppShell>
  );
}

// Everything that must resolve (or block) before a role-specific shell can render:
// status load/error, DB lock, identity load/error, account approval and MFA. Returns
// the element to show, or `null` once the user is cleared through to their shell.
// Kept out of App() so the component stays under the cognitive-complexity limit.
function resolveGate(
  status: UseQueryResult<SecurityStatus>,
  me: UseQueryResult<Me>,
): ReactNode | null {
  if (status.isError) {
    return <AppError onRetry={() => { status.refetch(); }} />;
  }
  if (!status.data) {
    return <AppLoading />;
  }
  if (status.data.locked) {
    return <UnlockGate failedRecent={status.data.failed_unlocks?.recent ?? 0} />;
  }
  // Unlocked → `me` is enabled; wait for it before choosing owner/child/gate.
  if (me.isError) {
    return <AppError onRetry={() => { me.refetch(); }} />;
  }
  if (!me.data) {
    return <AppLoading />;
  }
  if (me.data.status !== "approved") {
    return <AccountGate status={me.data.status} name={me.data.display_name} />;
  }
  // Admin requires MFA but the user hasn't enrolled (#157) — make them set it up.
  if (me.data.mfa_setup_required) {
    return <MfaSetupGate />;
  }
  if (me.data.mfa_required) {
    return <MfaGate />;
  }
  return null;
}

// Lightweight fallback shown inside the already-rendered shell while a lazy page
// chunk is fetched. Kept plain (no focus trap, no spinner that lingers) so it
// resolves instantly and never gets in the way of navigation or the e2e suite.
function RouteFallback() {
  return <div className="muted" style={{ padding: "1.5rem" }}>Loading…</div>;
}

// Neutral placeholder shown while the lock/identity state is still resolving, so
// no role-specific shell is rendered (and can't flash) before we know the role.
function AppLoading() {
  return (
    <div className="unlock">
      <div className="unlock__card">
        <h1>💷 Finance</h1>
        <p className="muted">Loading…</p>
      </div>
    </div>
  );
}

// Recoverable error state when the initial status/identity load fails, rather
// than leaving the user on a blank hang (FE-App item 4).
function AppError({ onRetry }: Readonly<{ onRetry: () => void }>) {
  return (
    <div className="unlock">
      <div className="unlock__card">
        <h1>⚠️ Couldn't load</h1>
        <p className="muted">
          Something went wrong reaching the server. Check your connection and try again.
        </p>
        <button className="btn" onClick={onRetry}>Retry</button>
      </div>
    </div>
  );
}

function AppShell({
  role,
  canManageTabs = false,
  blockedNavKeys = [],
  children,
}: Readonly<{
  role: string;
  canManageTabs?: boolean;
  blockedNavKeys?: string[];
  children: ReactNode;
}>) {
  const [navOpen, setNavOpen] = useState(false);
  return (
    <div className="layout">
      <Sidebar
        role={role}
        canManageTabs={canManageTabs}
        blockedNavKeys={blockedNavKeys}
        open={navOpen}
        onNavigate={() => setNavOpen(false)}
      />
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

// Shown when an admin has required MFA for this user but they haven't enrolled
// (#157). Walks them through setup → confirm. `mfaEnable` turns MFA on but does NOT
// mint a session; `mfaVerify` is what mints the app-entry session (it calls
// setSessionToken) — so we chain them with the same code and a successful confirm
// drops the user straight into the app without a second prompt.
function MfaSetupGate() {
  const qc = useQueryClient();
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [code, setCode] = useState("");
  const enable = useMutation({
    mutationFn: async () => {
      await mfaEnable(code);
      // Mint the app-entry session with the same code. If the TOTP period rolled
      // over between enable and verify this throws — MFA is already on and the
      // pending secret is now consumed, so we can't retry enable in place. We let
      // the error propagate (surfaced below as enable.isError) rather than swallow
      // it, and onSettled re-fetches `me`, which flips to mfa_required and routes
      // the user to the app-entry gate to enter a fresh code.
      await mfaVerify(code);
    },
    // Runs on success and failure: on success the session is minted and `me`
    // clears the gate into the app; on a rollover failure `me` becomes
    // mfa_required, sending the user to the entry gate for a fresh code.
    onSettled: () => qc.invalidateQueries(),
  });
  const begin = useMutation({ mutationFn: mfaSetup, onSuccess: (s) => setSetup(s) });
  return (
    <div className="unlock">
      <div className="unlock__card">
        <h1>🔐 Two-factor required</h1>
        <p className="muted">Your administrator requires two-factor authentication for your account. Set it up to continue.</p>
        {!setup && (
          <button className="btn" disabled={begin.isPending} onClick={() => begin.mutate()}>
            {begin.isPending ? "Preparing…" : "Set up two-factor"}
          </button>
        )}
        {setup && (
          <>
            <p className="muted">Scan this with an authenticator app (or enter the secret), then type the 6-digit code.</p>
            <div style={{ background: "#fff", padding: 12, borderRadius: 8, display: "inline-block" }}>
              <QRCodeSVG value={setup.otpauth_uri} size={176} />
            </div>
            <p className="muted" style={{ fontSize: "0.78rem", wordBreak: "break-all" }}>Secret: <code>{setup.secret}</code></p>
            <form onSubmit={(e) => { e.preventDefault(); if (code) enable.mutate(); }}>
              <input
                inputMode="numeric"
                autoFocus
                placeholder="123456"
                maxLength={8}
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              />
              <button className="btn" type="submit" disabled={!code || enable.isPending}>
                {enable.isPending ? "Enabling…" : "Enable & continue"}
              </button>
            </form>
            {enable.isError && <p className="status status--error">That code didn't match. Try again.</p>}
          </>
        )}
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

// Surface the REAL server error (e.g. "Passphrase is required.", a rate-limit
// message) rather than always blaming a wrong passphrase. Mirrors the ApiError
// detail extraction used elsewhere (Import.tsx errorMessage, Settings fail path).
function unlockErrorMessage(error: unknown): string {
  const fallback = "Could not unlock. Check the key and try again.";
  if (error instanceof ApiError) {
    return typeof error.body?.detail === "string" ? error.body.detail : fallback;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
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
        {unlock.isError && <p className="status status--error">{unlockErrorMessage(unlock.error)}</p>}
        <p className="muted" style={{ fontSize: "0.78rem" }}>
          Lost the passphrase? The data cannot be recovered.
        </p>
      </div>
    </div>
  );
}
