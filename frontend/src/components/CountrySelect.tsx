import { type CSSProperties } from "react";
import { useQuery } from "@tanstack/react-query";
import { getCountries } from "../api/client";

// A country picker backed by the ISO list from /api/settings/countries. Stores the
// alpha-2 code; the blank option clears it (→ null). Shared by Vendors + Travel.
export default function CountrySelect({
  value,
  onChange,
  disabled,
  style,
  title,
}: Readonly<{
  value: string | null;
  onChange: (code: string | null) => void;
  disabled?: boolean;
  style?: CSSProperties;
  title?: string;
}>) {
  const { data } = useQuery({ queryKey: ["countries"], queryFn: getCountries, staleTime: Infinity });
  const countries = data ?? [];
  // Pin the United Kingdom to the top — it's the most common choice for this user
  // base — then the rest alphabetically (already sorted by the endpoint).
  const PINNED = "GB";
  const pinned = countries.find((c) => c.code === PINNED);
  const rest = countries.filter((c) => c.code !== PINNED);
  // Keep a saved value that isn't in the list (e.g. a legacy free-text code) selectable.
  const known = !value || countries.some((c) => c.code === value);
  return (
    <select
      value={value ?? ""}
      disabled={disabled}
      style={style}
      title={title ?? "Country (used by the spending-by-location map)"}
      onChange={(e) => onChange(e.target.value || null)}
    >
      <option value="">—</option>
      {!known && value && <option value={value}>{value}</option>}
      {pinned && <option value={pinned.code}>{pinned.name}</option>}
      {pinned && rest.length > 0 && <option disabled>──────────</option>}
      {rest.map((c) => (
        <option key={c.code} value={c.code}>{c.name}</option>
      ))}
    </select>
  );
}
