// Helpers for user-entered numeric/money fields, which are plain text inputs.
//
// Inputs are sent to the API as strings, so an empty or non-numeric box used to
// post "NaN", and computed values (e.g. gallons→litres) posted full float noise
// like "22.730450000000002". These keep submitted values clean and guard the
// submit so a non-numeric / negative entry can't be sent (FE-4). Pair with
// inputMode="decimal" on the input for a numeric mobile keyboard.

/** Parse a user-entered amount → a finite, non-negative number, or null if invalid. */
export function parseAmount(input: string | null | undefined): number | null {
  if (input == null) return null;
  const t = String(input).trim();
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

/** True when `input` is a valid non-negative amount — for disabling a submit button. */
export function isAmount(input: string | null | undefined): boolean {
  return parseAmount(input) !== null;
}

/** Round `n` to at most `dp` decimals, as a clean string with no float noise. */
export function roundStr(n: number, dp = 2): string {
  return String(Number(n.toFixed(dp)));
}
