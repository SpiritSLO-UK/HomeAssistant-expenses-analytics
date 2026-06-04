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

// --- Dashboard card show/hide (backlog #86) ---

const HIDDEN_CARDS_KEY = "hafi_dashboard_hidden";

export function getHiddenDashboardCards(): Set<string> {
  const raw = readString(HIDDEN_CARDS_KEY);
  if (!raw) return new Set();
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? new Set(parsed.map(String)) : new Set();
  } catch {
    return new Set();
  }
}

export function setHiddenDashboardCards(hidden: Set<string>): void {
  writeString(HIDDEN_CARDS_KEY, JSON.stringify([...hidden]));
}

// --- Dashboard card order (backlog #84) ---

const CARD_ORDER_KEY = "hafi_dashboard_order";

export function getDashboardCardOrder(): string[] {
  const raw = readString(CARD_ORDER_KEY);
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch {
    return [];
  }
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
  const raw = readString(HIDDEN_NAV_KEY);
  if (!raw) return new Set();
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? new Set(parsed.map(String)) : new Set();
  } catch {
    return new Set();
  }
}

export function setHiddenNavKeys(hidden: Set<string>): void {
  writeString(HIDDEN_NAV_KEY, JSON.stringify([...hidden]));
}

// --- Dashboard Mine/Shared/All view toggle (backlog #66/#82) ---

const DASH_VIEW_KEY = "hafi_dashboard_view";

export function getDashboardView(): string {
  return readString(DASH_VIEW_KEY) || "all";
}

export function setDashboardView(view: string): void {
  writeString(DASH_VIEW_KEY, view);
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
