// Light/dark theme handling. The initial <html data-theme> is set as early as
// possible by a tiny inline script in index.html (so there's no flash of the
// wrong theme); this module keeps it in sync with the user's choice and — while
// they're on "System" — with the OS setting.
import { getThemePref, setThemePref } from "./prefs";

export type ThemePref = "system" | "light" | "dark";

const DARK_QUERY = "(prefers-color-scheme: dark)";

function resolve(pref: ThemePref): "light" | "dark" {
  if (pref === "system") {
    return globalThis.matchMedia?.(DARK_QUERY).matches ? "dark" : "light";
  }
  return pref;
}

export function applyTheme(pref: ThemePref = getThemePref()): void {
  document.documentElement.dataset.theme = resolve(pref);
}

/** Persist the user's theme choice and apply it in one call. */
export function setTheme(pref: ThemePref): void {
  setThemePref(pref);
  applyTheme(pref);
}

// Attach the OS-change listener at most once, even if initTheme runs again under
// HMR / tests / a double mount (it used to stack a new, non-removable listener
// each time).
let osListenerAttached = false;

export function initTheme(): void {
  applyTheme();
  if (osListenerAttached) return;
  const mql = globalThis.matchMedia?.(DARK_QUERY);
  if (!mql) return;
  // Follow the OS light/dark setting live, but only while the user is on "System".
  const onChange = () => {
    if (getThemePref() === "system") applyTheme("system");
  };
  if (typeof mql.addEventListener === "function") {
    mql.addEventListener("change", onChange);
  } else {
    // Legacy Safari (<14) exposes only the deprecated addListener.
    (mql as MediaQueryList & { addListener(cb: () => void): void }).addListener(onChange);
  }
  osListenerAttached = true;
}
