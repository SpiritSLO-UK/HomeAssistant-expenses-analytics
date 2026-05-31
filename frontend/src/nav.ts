// Sidebar navigation definition (spec §25). Pages are stubs in Stage 0 and get
// filled in across later stages.

export interface NavItem {
  path: string;
  label: string;
  icon: string;
}

export const NAV_ITEMS: NavItem[] = [
  { path: "/", label: "Dashboard", icon: "📊" },
  { path: "/import", label: "Import", icon: "📥" },
  { path: "/transactions", label: "Transactions", icon: "💳" },
  { path: "/categories", label: "Categories", icon: "🏷️" },
  { path: "/vendors", label: "Vendors", icon: "🏬" },
  { path: "/rules", label: "Rules", icon: "⚙️" },
  { path: "/projects", label: "Projects", icon: "📁" },
  { path: "/budgets", label: "Budgets", icon: "🎯" },
  { path: "/review", label: "Review Queue", icon: "🔎" },
  { path: "/settings", label: "Settings", icon: "🔧" },
];
