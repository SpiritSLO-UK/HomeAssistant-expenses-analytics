// Mirror of the backend `vendor_service.derive_vendor_signature`: keep the leading
// tokens until one contains a digit (drops store numbers / locations), so a raw
// bank description like "TESCO STORES 3142 DARTFORD" → "TESCO STORES". Used to
// preview the recommended vendor name for the "Create & link" action.
export function deriveVendorSignature(text: string): string {
  const sig: string[] = [];
  for (const token of (text || "").split(/\s+/)) {
    if (/\d/.test(token)) break;
    if (token) sig.push(token);
  }
  return sig.join(" ").trim() || (text || "").trim();
}

function titleCaseWord(token: string): string {
  return token.charAt(0).toUpperCase() + token.slice(1).toLowerCase();
}

// Normalise a single signature token for display. All-caps bank descriptions
// ("TESCO STORES") read better title-cased ("Tesco Stores"), but blindly
// title-casing mangles short acronyms and brand codes: "BP" → "Bp", "HSBC" →
// "Hsbc", "EE" → "Ee", "O2" → "O2"?, "M&S". So we only title-case genuinely
// word-like all-caps tokens and preserve the rest:
//   - not all-caps (mixed/lower case) → left untouched (author already chose it);
//   - a short all-caps token (≤ 4 chars, e.g. BP, HSBC, EE) → kept as-is;
//   - a token carrying a digit or ampersand (O2, M&S) → kept as-is;
//   - otherwise (TESCO, STORES) → title-cased.
function normaliseToken(token: string): string {
  // Only touch tokens that are wholly upper-case *and* contain a cased letter;
  // this leaves mixed-case tokens and pure punctuation/number tokens alone.
  if (token !== token.toUpperCase() || !/[A-Z]/.test(token)) return token;
  if (token.length <= 4 || /[0-9&]/.test(token)) return token;
  return titleCaseWord(token);
}

// The display name the backend would create for an all-caps signature. NOTE ON
// BACKEND PARITY: `vendor_service.create_from_transaction` / `learn_vendor_category`
// do `if canonical.isupper(): canonical = canonical.title()` — a naive whole-string
// `str.title()` that DOES mangle short acronyms (BP → "Bp", HSBC → "Hsbc"). This
// preview intentionally improves on that by preserving short acronyms/brand codes
// so the suggested name shown to the user is not obviously wrong; the backend value
// may therefore differ for such names until the backend adopts the same heuristic.
// (Backend is out of scope for this change.) For ordinary all-caps words
// ("TESCO STORES" → "Tesco Stores") the two agree.
export function recommendedVendorName(merchantRaw: string | null | undefined, description: string): string {
  const sig = deriveVendorSignature(merchantRaw || description || "");
  if (!sig) return sig;
  return sig.split(/\s+/).map(normaliseToken).join(" ");
}
