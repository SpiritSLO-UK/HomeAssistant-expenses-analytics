// Local, per-browser UI preferences (not synced, not sensitive). Kept separate
// from api/client.ts (which is the server API). All access is defensive so a
// blocked/unavailable localStorage (private mode) just falls back to defaults.

function readString(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeString(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
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

// --- One-time cloud-AI disclaimer (backlog #42) ---

const CLOUD_AI_ACK_KEY = "hafi_cloud_ai_ack";

export function isCloudAiAcknowledged(): boolean {
  return readString(CLOUD_AI_ACK_KEY) === "1";
}

export function setCloudAiAcknowledged(): void {
  writeString(CLOUD_AI_ACK_KEY, "1");
}
