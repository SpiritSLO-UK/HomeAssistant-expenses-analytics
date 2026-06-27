// Display-side currency formatting.
//
// The household's *base* currency is an app-wide singleton — there is exactly one
// configured value, so we keep it in module scope and set it once from settings
// near the app root (App.tsx) rather than threading `base` through dozens of cards
// and props. `money()` falls back to GBP until settings have loaded, and renders a
// proper localised symbol (£/€/$) for the configured currency.
//
// Note: this is purely how *base-currency totals* are rendered. A transaction's own
// stored currency is a per-row concern handled separately (it's shown as e.g.
// "12.34 USD"); this helper is for the figures the backend has already converted to
// the base currency.
let _displayCurrency = "GBP";

/** Set the app-wide base currency used by `money()` (called from App on settings load). */
export function setDisplayCurrency(code: string | null | undefined): void {
  if (code) _displayCurrency = code;
}

/** The currently configured base currency code. */
export function displayCurrency(): string {
  return _displayCurrency;
}

/**
 * Format an amount in the configured base currency (or an explicit `currency`),
 * with a localised symbol. Returns an em dash for null / non-numeric input.
 */
export function money(
  value: string | number | null | undefined,
  currency: string = _displayCurrency,
): string {
  const n = Number(value);
  if (value == null || value === "" || !Number.isFinite(n)) return "—";
  return n.toLocaleString(undefined, {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}
