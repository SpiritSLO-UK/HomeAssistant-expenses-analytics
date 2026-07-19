// Sidebar navigation definition (spec §25). Pages are stubs in Stage 0 and get
// filled in across later stages.

import type { NavLayout } from "./api/client";
import { getHiddenNavKeys, getNavOrder } from "./prefs";

export interface NavItem {
  path: string;
  label: string;
  icon: string;
  ownerOnly?: boolean; // shown only to the owner (administrator)
  // Shown only to users who can manage settings (owner or an owner-granted member).
  // Gated the same way the old Settings Tags card was (`can_manage_settings`).
  manageSettingsOnly?: boolean;
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
  { path: "/tags", label: "Tags", icon: "🔖", manageSettingsOnly: true },
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

// --- Grouped navigation (customisable sidebar, grouped-nav PR2/4) ---
//
// The sidebar renders GROUPS. A group references its members BY PATH; NAV_ITEMS
// above stays the single registry of icon/label/gates (a member inherits its
// gate wherever it appears). A group carries its own header icon/label; members
// may optionally override their own icon/label (used by the PR3 editor — the
// default layout below sets no overrides).

export interface NavGroupItem {
  path: string;
  label?: string; // per-member override (else the registry label is used)
  icon?: string; // per-member override (else the registry icon is used)
  hidden?: boolean; // member hidden from the sidebar/sub-tabs (still URL-reachable)
}

export interface NavGroup {
  id: string;
  label?: string; // header label (absent on a single-item "standalone" group)
  icon?: string; // header icon
  items: NavGroupItem[];
}

// The approved default layout: 8 entries drawn from the 24 registry items. A
// group with a single item renders as a plain sidebar link (a "standalone"); a
// multi-item group renders as a header whose members appear as page sub-tabs.
export const DEFAULT_NAV_GROUPS: NavGroup[] = [
  { id: "dashboard", items: [{ path: "/" }] },
  { id: "search", items: [{ path: "/search" }] },
  {
    id: "money",
    label: "Money",
    icon: "💳",
    items: [{ path: "/import" }, { path: "/transactions" }, { path: "/receipts" }, { path: "/review" }],
  },
  {
    id: "library",
    label: "Library",
    icon: "🏷️",
    items: [{ path: "/categories" }, { path: "/tags" }, { path: "/vendors" }, { path: "/rules" }],
  },
  {
    id: "wealth",
    label: "Wealth",
    icon: "💰",
    items: [{ path: "/accounts" }, { path: "/savings" }, { path: "/investments" }, { path: "/assets" }],
  },
  {
    id: "plans",
    label: "Plans",
    icon: "🎯",
    items: [
      { path: "/budgets" },
      { path: "/projects" },
      { path: "/travel" },
      { path: "/business" },
      { path: "/subscriptions" },
      { path: "/allowance" },
    ],
  },
  { id: "energy", items: [{ path: "/energy" }] },
  {
    id: "system",
    label: "System",
    icon: "🔧",
    items: [{ path: "/users" }, { path: "/logs" }, { path: "/settings" }],
  },
];

// Core pages that can never be hidden — you always need a way home and to
// Settings. Referenced when seeding the layout from legacy per-device hide prefs.
const ALWAYS_SHOWN = new Set(["/", "/settings"]);

const ITEM_BY_PATH = new Map(NAV_ITEMS.map((i) => [i.path, i] as const));

export function navItemForPath(path: string): NavItem | undefined {
  return ITEM_BY_PATH.get(path);
}

// The default group a path ships in (its home when a saved layout is missing it),
// falling back to the System group for anything not placed in the defaults.
function defaultGroupIdForPath(path: string): string {
  for (const g of DEFAULT_NAV_GROUPS) {
    if (g.items.some((it) => it.path === path)) return g.id;
  }
  return "system";
}

// First load with no server layout: start from the defaults but carry over the
// user's existing per-device prefs — hidden tabs become hidden members, and the
// flat saved order re-sorts members within each group.
function seededDefaultLayout(): NavGroup[] {
  const hidden = getHiddenNavKeys();
  const order = getNavOrder();
  const orderIndex = (p: string) => {
    const i = order.indexOf(p);
    return i === -1 ? Number.MAX_SAFE_INTEGER : i;
  };
  return DEFAULT_NAV_GROUPS.map((g) => ({
    ...g,
    items: [...g.items]
      .map((it) => ({
        ...it,
        hidden: !ALWAYS_SHOWN.has(it.path) && hidden.has(it.path) ? true : it.hidden,
      }))
      .sort((a, b) => orderIndex(a.path) - orderIndex(b.path)),
  }));
}

// Resolve the layout to render from the (possibly null) server-saved layout,
// generalising the old `mergeNavOrder`:
//   1. no saved layout   → the default layout, seeded from legacy per-device prefs.
//   2. saved layout      → keep it, but append any registry path missing from every
//      saved group into the group it ships in (System fallback), so pages added in
//      a future release still surface.
//   3. drop any saved member whose path is no longer a registry item (removed page).
export function resolveLayout(saved: NavLayout | null | undefined): NavGroup[] {
  if (saved == null) return seededDefaultLayout();

  const groups: NavGroup[] = saved.groups.map((g) => {
    const def = DEFAULT_NAV_GROUPS.find((d) => d.id === g.id);
    return {
      id: g.id,
      label: g.label ?? def?.label,
      icon: g.icon ?? def?.icon,
      items: g.items.filter((it) => ITEM_BY_PATH.has(it.path)).map((it) => ({ ...it })),
    };
  });

  const present = new Set(groups.flatMap((g) => g.items.map((it) => it.path)));
  for (const item of NAV_ITEMS) {
    if (present.has(item.path)) continue;
    const gid = defaultGroupIdForPath(item.path);
    let target = groups.find((g) => g.id === gid);
    if (!target) {
      const def = DEFAULT_NAV_GROUPS.find((d) => d.id === gid);
      target = { id: gid, label: def?.label, icon: def?.icon, items: [] };
      groups.push(target);
    }
    target.items.push({ path: item.path });
  }
  return groups;
}

// Role / per-user visibility context, matching the old flat sidebar filtering.
export interface NavVisibilityCtx {
  role: string;
  canManageTabs: boolean; // owner or an owner-granted member (can_manage_settings, #28)
  blockedNavKeys: string[]; // pages the owner restricted for this user (#108)
}

// A member ready to render: registry gates resolved, member overrides applied.
export interface NavRenderItem {
  path: string;
  label: string;
  icon: string;
}

// Does an item pass the role/visibility gates? Mirrors the old `roleItems` filter
// (child → childVisible only; ownerOnly → owner; blocked non-owner-only pages
// dropped; manageSettingsOnly needs canManageTabs).
function itemPassesGate(item: NavItem, ctx: NavVisibilityCtx, blocked: Set<string>): boolean {
  if (ctx.role === "child") return item.childVisible === true;
  if (item.ownerOnly) return ctx.role === "owner";
  if (blocked.has(navKey(item.path))) return false;
  if (item.manageSettingsOnly && !ctx.canManageTabs) return false;
  return true;
}

// The visible members of a group after gating + the member `hidden` flag, in
// layout order. Empty when the whole group should be hidden.
export function visibleGroupItems(group: NavGroup, ctx: NavVisibilityCtx): NavRenderItem[] {
  const blocked = new Set(ctx.blockedNavKeys);
  const out: NavRenderItem[] = [];
  for (const member of group.items) {
    if (member.hidden) continue;
    const item = ITEM_BY_PATH.get(member.path);
    if (!item || !itemPassesGate(item, ctx, blocked)) continue;
    out.push({ path: item.path, label: member.label ?? item.label, icon: member.icon ?? item.icon });
  }
  return out;
}

// Flat routes only — exact match, with "/" never swallowing other paths.
export function pathMatches(itemPath: string, currentPath: string): boolean {
  if (itemPath === "/") return currentPath === "/";
  return currentPath === itemPath;
}

// --- Nav editor helpers (grouped-nav PR3/4) ---
//
// Does a path pass the role/visibility gates for this user? The editor uses this
// to filter the editable item pool so a user can never surface a page they can't
// reach (e.g. an owner-only or settings-gated page). Unknown paths never pass.
export function pathPassesGate(path: string, ctx: NavVisibilityCtx): boolean {
  const item = ITEM_BY_PATH.get(path);
  if (!item) return false;
  return itemPassesGate(item, ctx, new Set(ctx.blockedNavKeys));
}

// The registry default label/icon for a path, used by the editor when a member
// override is empty (so an empty rename field falls back to the default name).
export function defaultLabelForPath(path: string): string {
  return ITEM_BY_PATH.get(path)?.label ?? path;
}

export function defaultIconForPath(path: string): string {
  return ITEM_BY_PATH.get(path)?.icon ?? "";
}

// Is this a user-created custom group (vs a built-in default group)? Custom groups
// are the only ones the editor lets you delete; their ids are `custom:<random>`.
export function isCustomGroupId(id: string): boolean {
  return id.startsWith("custom:");
}

// Serialise the editor's working NavGroup[] into the persisted NavLayout blob the
// PR1 API stores (dropping empty overrides so the saved blob stays lean). v=1
// matches the backend's normalise_nav_layout / stored version.
export function toNavLayout(groups: NavGroup[]): NavLayout {
  return {
    v: 1,
    groups: groups.map((g) => ({
      id: g.id,
      ...(g.label ? { label: g.label } : {}),
      ...(g.icon ? { icon: g.icon } : {}),
      items: g.items.map((it) => ({
        path: it.path,
        ...(it.label ? { label: it.label } : {}),
        ...(it.icon ? { icon: it.icon } : {}),
        ...(it.hidden ? { hidden: true } : {}),
      })),
    })),
  };
}
