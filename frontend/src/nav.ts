// Sidebar navigation definition (spec §25). Pages are stubs in Stage 0 and get
// filled in across later stages.

export interface NavItem {
  path: string;
  label: string;
  icon: string;
  ownerOnly?: boolean; // shown only to the owner (administrator)
  // Shown to the restricted `child` role (which sees nothing else). Mirrors
  // `_CHILD_ALLOWED_PREFIXES` in backend/app/main.py — keep the two in sync.
  childVisible?: boolean;
}

export const NAV_ITEMS: NavItem[] = [
  { path: "/", label: "Dashboard", icon: "📊" },
  { path: "/search", label: "Search", icon: "🔍" },
  { path: "/import", label: "Import", icon: "📥" },
  { path: "/transactions", label: "Transactions", icon: "💳" },
  { path: "/categories", label: "Categories", icon: "🏷️" },
  { path: "/vendors", label: "Vendors", icon: "🏬" },
  { path: "/rules", label: "Rules", icon: "⚙️" },
  { path: "/projects", label: "Projects", icon: "📁" },
  { path: "/travel", label: "Travel", icon: "✈️" },
  { path: "/business", label: "Business", icon: "💼" },
  { path: "/budgets", label: "Budgets", icon: "🎯" },
  { path: "/savings", label: "Savings", icon: "💰" },
  { path: "/investments", label: "Investments", icon: "📈" },
  { path: "/accounts", label: "Accounts", icon: "🏦" },
  { path: "/assets", label: "Cars & assets", icon: "🚗" },
  { path: "/energy", label: "Energy", icon: "⚡" },
  { path: "/allowance", label: "Allowance", icon: "🧸", childVisible: true },
  { path: "/subscriptions", label: "Subscriptions", icon: "🔁" },
  { path: "/receipts", label: "Receipts", icon: "🧾" },
  { path: "/review", label: "Review Queue", icon: "🔎" },
  { path: "/users", label: "Users", icon: "👥", ownerOnly: true },
  { path: "/logs", label: "Logs", icon: "📜", ownerOnly: true },
  { path: "/settings", label: "Settings", icon: "🔧" },
];

// The nav key for a page = its path without the leading slash (e.g. "/budgets" →
// "budgets"). This is what the per-user "blocked pages" list (#108) stores.
export function navKey(path: string): string {
  return path.replace(/^\//, "");
}

// Pages the owner can restrict for an individual non-admin user (#108) — mirrors
// BLOCKABLE_NAV in backend/app/services/auth_service.py. Dashboard, Settings,
// Users and Logs are deliberately excluded (landing page / RBAC-gated / owner-only).
const NON_BLOCKABLE = new Set(["/", "/settings", "/users", "/logs"]);
export const BLOCKABLE_NAV_ITEMS: NavItem[] = NAV_ITEMS.filter(
  (i) => !NON_BLOCKABLE.has(i.path) && !i.ownerOnly,
);
