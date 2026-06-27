// Local, per-browser UI preferences (not synced, not sensitive). Kept separate
// from api/client.ts (which is the server API). All access is defensive so a
// blocked/unavailable localStorage (private mode) just falls back to defaults.

function readString(key: string): string | null {
  try {
    return globalThis.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeString(key: string, value: string | null): void {
  try {
    if (value === null) globalThis.localStorage.removeItem(key);
    else globalThis.localStorage.setItem(key, value);
  } catch {
    /* localStorage unavailable — preferences just won't persist */
  }
}

// Parse a JSON string-array preference; returns [] on missing/invalid input.
// Shared by the array + Set readers below (a Set is just `new Set(...)` of this).
function readStringArray(key: string): string[] {
  const raw = readString(key);
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch {
    return [];
  }
}

// --- Dashboard card show/hide (backlog #86) ---

const HIDDEN_CARDS_KEY = "hafi_dashboard_hidden";

export function getHiddenDashboardCards(): Set<string> {
  return new Set(readStringArray(HIDDEN_CARDS_KEY));
}

export function setHiddenDashboardCards(hidden: Set<string>): void {
  writeString(HIDDEN_CARDS_KEY, JSON.stringify([...hidden]));
}

// --- Dashboard card order (backlog #84) ---

const CARD_ORDER_KEY = "hafi_dashboard_order";

export function getDashboardCardOrder(): string[] {
  return readStringArray(CARD_ORDER_KEY);
}

export function setDashboardCardOrder(order: string[]): void {
  writeString(CARD_ORDER_KEY, JSON.stringify(order));
}

// --- Resizable table column widths, per table, per device (backlog) ---

function columnWidthsKey(tableKey: string): string {
  return `hafi_colwidths_${tableKey}`;
}

export function getColumnWidths(tableKey: string): Record<string, number> {
  const raw = readString(columnWidthsKey(tableKey));
  if (!raw) return {};
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return {};
    const out: Record<string, number> = {};
    for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof v === "number" && v > 0) out[k] = v;
    }
    return out;
  } catch {
    return {};
  }
}

export function setColumnWidths(tableKey: string, widths: Record<string, number>): void {
  writeString(columnWidthsKey(tableKey), JSON.stringify(widths));
}

// --- Sidebar nav show/hide (per device) ---

const HIDDEN_NAV_KEY = "hafi_nav_hidden";

export function getHiddenNavKeys(): Set<string> {
  return new Set(readStringArray(HIDDEN_NAV_KEY));
}

export function setHiddenNavKeys(hidden: Set<string>): void {
  writeString(HIDDEN_NAV_KEY, JSON.stringify([...hidden]));
}

// --- Sidebar nav order (per device) ---

const NAV_ORDER_KEY = "hafi_nav_order";

export function getNavOrder(): string[] {
  return readStringArray(NAV_ORDER_KEY);
}

export function setNavOrder(order: string[]): void {
  writeString(NAV_ORDER_KEY, JSON.stringify(order));
}

// --- Dashboard Mine/Shared/All view toggle (backlog #66/#82) ---

const DASH_VIEW_KEY = "hafi_dashboard_view";

export function getDashboardView(): string {
  return readString(DASH_VIEW_KEY) || "all";
}

export function setDashboardView(view: string): void {
  writeString(DASH_VIEW_KEY, view);
}

// --- Dashboard selected month + member filter (per device) ---
// Persisted so a reload (or revisit) keeps the month/member you were looking at
// rather than snapping back to the current month.

const DASH_MONTH_KEY = "hafi_dashboard_month";

export function getDashboardMonth(): string {
  return readString(DASH_MONTH_KEY) || "";
}

export function setDashboardMonth(month: string): void {
  writeString(DASH_MONTH_KEY, month);
}

const DASH_MEMBER_KEY = "hafi_dashboard_member";

export function getDashboardMember(): string {
  return readString(DASH_MEMBER_KEY) || "";
}

export function setDashboardMember(memberId: string): void {
  writeString(DASH_MEMBER_KEY, memberId);
}

// --- Light/dark theme (per device) ---

const THEME_KEY = "hafi_theme";

export function getThemePref(): "system" | "light" | "dark" {
  const v = readString(THEME_KEY);
  return v === "light" || v === "dark" ? v : "system";
}

export function setThemePref(pref: "system" | "light" | "dark"): void {
  writeString(THEME_KEY, pref);
}

// --- One-time cloud-AI disclaimer (backlog #42) ---

const CLOUD_AI_ACK_KEY = "hafi_cloud_ai_ack";

export function isCloudAiAcknowledged(): boolean {
  return readString(CLOUD_AI_ACK_KEY) === "1";
}

export function setCloudAiAcknowledged(): void {
  writeString(CLOUD_AI_ACK_KEY, "1");
}

// --- Per-send AI image warning (Q3): sending an image to AI can't be redacted,
// so warn before each send until the user ticks "don't warn me again". ---

const IMAGE_AI_WARN_KEY = "hafi_image_ai_warn_dismissed";

export function isImageAiWarningDismissed(): boolean {
  return readString(IMAGE_AI_WARN_KEY) === "1";
}

export function setImageAiWarningDismissed(): void {
  writeString(IMAGE_AI_WARN_KEY, "1");
}

// --- Reset all UI preferences ---
// Every preference key uses the `hafi_` prefix, so we can clear them all (card
// layout, nav, column widths, theme, dismissed warnings) without touching anything
// else in localStorage. Powers the "Reset UI preferences" button in Settings.
export function clearAllPrefs(): void {
  try {
    const toRemove: string[] = [];
    for (let i = 0; i < globalThis.localStorage.length; i++) {
      const key = globalThis.localStorage.key(i);
      if (key?.startsWith("hafi_")) toRemove.push(key);
    }
    for (const key of toRemove) globalThis.localStorage.removeItem(key);
  } catch {
    /* localStorage unavailable — nothing to clear */
  }
}
