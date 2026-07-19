// Display-side date formatting.
//
// The household's date display format is an app-wide preference (Settings →
// Currency & dates), stored like base_currency. Every user-facing date renders
// through `formatDate`, so a single toggle keeps the whole app consistent.
//
// This is DISPLAY only: it never changes how dates are stored, sent to the
// backend, or parsed on import — only how an already-ISO date string is shown.

import { useQuery } from "@tanstack/react-query";

import { getSettings } from "../api/client";

export type DateFormat = "iso" | "us" | "uk";

/** The allowed formats + their Settings labels/examples (order = dropdown order). */
export const DATE_FORMATS: readonly { value: DateFormat; label: string; example: string }[] = [
  { value: "iso", label: "ISO", example: "2026-07-18" },
  { value: "us", label: "US", example: "07/18/2026" },
  { value: "uk", label: "UK", example: "18/07/2026" },
];

const DEFAULT_DATE_FORMAT: DateFormat = "iso";
const ALLOWED = new Set<DateFormat>(DATE_FORMATS.map((f) => f.value));

// One builder per format — a lookup map, never a nested ternary. Each takes the
// already-split, zero-padded ISO parts and assembles the display string.
const BUILDERS: Record<DateFormat, (y: string, m: string, d: string) => string> = {
  iso: (y, m, d) => `${y}-${m}-${d}`,
  us: (y, m, d) => `${m}/${d}/${y}`,
  uk: (y, m, d) => `${d}/${m}/${y}`,
};

/**
 * Format an ISO date string (`YYYY-MM-DD`, or an ISO datetime whose date part is
 * the first 10 chars) into the chosen display format. Returns "" for null/empty.
 *
 * Parsed by splitting on "-" — deliberately NOT via `new Date()`, whose timezone
 * handling can shift a date-only value across a day boundary. An unrecognised
 * value is returned unchanged rather than mangled.
 */
export function formatDate(value: string | null | undefined, fmt: DateFormat): string {
  if (!value) return "";
  const parts = value.slice(0, 10).split("-");
  if (parts.length !== 3) return value;
  const [y, m, d] = parts;
  if (y.length !== 4 || m.length !== 2 || d.length !== 2) return value;
  return (BUILDERS[fmt] ?? BUILDERS.iso)(y, m, d);
}

/** Coerce an arbitrary stored/setting value to a valid DateFormat (default iso). */
export function normaliseDateFormat(value: string | null | undefined): DateFormat {
  return value && ALLOWED.has(value as DateFormat) ? (value as DateFormat) : DEFAULT_DATE_FORMAT;
}

/**
 * The user's chosen date format, read from the shared settings query (default
 * iso). The `["settings"]` query is already fetched app-wide, so React Query
 * dedupes this — pages can call it without adding their own settings fetch.
 */
export function useDateFormat(): DateFormat {
  const settings = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  return normaliseDateFormat(settings.data?.date_format);
}
