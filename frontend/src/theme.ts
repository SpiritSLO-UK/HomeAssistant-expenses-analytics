// Light/dark theme handling. The initial <html data-theme> is set as early as
// possible by a tiny inline script in index.html (so there's no flash of the
// wrong theme); this module keeps it in sync with the user's choice and — while
// they're on "System" — with the OS setting.
import { getThemePref } from "./prefs";

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

export function initTheme(): void {
  applyTheme();
  // Follow the OS light/dark setting live, but only while the user is on "System".
  globalThis.matchMedia?.(DARK_QUERY).addEventListener("change", () => {
    if (getThemePref() === "system") applyTheme("system");
  });
}
