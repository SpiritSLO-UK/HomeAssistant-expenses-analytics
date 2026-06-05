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

// The display name the backend would create (it title-cases an all-caps
// signature, e.g. "TESCO STORES" → "Tesco Stores"), so the preview matches.
export function recommendedVendorName(merchantRaw: string | null | undefined, description: string): string {
  const sig = deriveVendorSignature(merchantRaw || description || "");
  if (sig && sig === sig.toUpperCase()) {
    return sig.toLowerCase().replace(/\b[a-z]/g, (c) => c.toUpperCase());
  }
  return sig;
}
