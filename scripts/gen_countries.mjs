// Generate backend/app/services/_country_names.py — the ISO-3166-1 alpha-2 country
// names used by app.services.geo (spend-by-location map) and the country pickers.
// Names come from Node's Intl.DisplayNames so we don't hand-maintain ~250 entries.
// Run from the repo root:  node scripts/gen_countries.mjs
import { writeFileSync } from "node:fs";

const dn = new Intl.DisplayNames(["en"], { type: "region", fallback: "none" });
// Macro-regions / exceptionally-reserved codes that Intl knows but aren't countries.
const EXCLUDE = new Set(["EU", "EZ", "UN", "QO", "ZZ", "XA", "XB", "AC", "CP", "DG", "EA", "IC", "TA", "UK"]);
const A = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

const out = {};
for (const a of A) {
  for (const b of A) {
    const code = a + b;
    if (EXCLUDE.has(code)) continue;
    const name = dn.of(code);
    if (name && name !== code) out[code] = name;
  }
}

const header =
  '"""ISO-3166-1 alpha-2 country names — GENERATED, do not hand-edit.\n\n' +
  "Produced by scripts/gen_countries.mjs (Node Intl.DisplayNames). Consumed by\n" +
  'app.services.geo for the spend-by-location map and the country pickers."""\n\n';
writeFileSync(
  "backend/app/services/_country_names.py",
  header + "COUNTRY_NAMES = " + JSON.stringify(out, null, 4) + "\n",
  "utf8",
);
console.log("countries written:", Object.keys(out).length);
