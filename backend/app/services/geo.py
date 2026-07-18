"""Geography helpers for the spend-by-location map (spec §16.3).

A transaction's country is the **vendor's country** when set, otherwise inferred
from the transaction **currency** (a reasonable default — most spend is in the
home currency = the home country, and foreign-currency rows surface where travel
spend went). Everything is local; no geocoding service is called (privacy).
"""

from __future__ import annotations

from app.services._country_names import COUNTRY_NAMES as _ISO_COUNTRY_NAMES

# Currency → ISO-3166 alpha-2 (best-effort default when a vendor has no country).
# EUR has no single ISO country (it spans the Eurozone), so it deliberately
# buckets to the "EU" pseudo-code rather than dropping to "Unknown" — this keeps
# untagged EUR spend grouped. The FE WorldMap renders "EU" in the legend but
# never plots a centroid for it (#355); a per-txn/vendor country (#79) always
# wins over this coarse fallback. name("EU") → "Eurozone" (see COUNTRY_NAMES).
CURRENCY_COUNTRY = {
    "GBP": "GB", "USD": "US", "EUR": "EU", "JPY": "JP", "CNY": "CN",
    "AUD": "AU", "CAD": "CA", "CHF": "CH", "HKD": "HK", "SGD": "SG",
    "NZD": "NZ", "SEK": "SE", "NOK": "NO", "DKK": "DK", "PLN": "PL",
    "INR": "IN", "ZAR": "ZA", "AED": "AE", "THB": "TH", "MXN": "MX",
    # More single-country currencies travel commonly produces — so the map
    # credits the actual country instead of "Unknown" (SR-F4).
    "CZK": "CZ", "HUF": "HU", "TRY": "TR", "BRL": "BR", "ISK": "IS",
    "KRW": "KR", "ILS": "IL", "MYR": "MY", "PHP": "PH", "IDR": "ID",
}

# Display names for every ISO-3166-1 alpha-2 code (generated — see
# _country_names.py + scripts/gen_countries.mjs), plus the "EU" pseudo-code the
# EUR currency fallback above maps to. It is labelled "Eurozone" (not a bare "EU"
# code) so the dashboard/legend read cleanly; the settings country picker omits it
# since it isn't a real country.
COUNTRY_NAMES = {**_ISO_COUNTRY_NAMES, "EU": "Eurozone"}


def country_for(
    currency: str | None,
    vendor_country: str | None,
    txn_country: str | None = None,
    default_country: str | None = None,
) -> str | None:
    """Resolve a transaction's country code, or None when it can't be inferred.

    Precedence: the transaction's own country (e.g. tagged for a trip to Spain) →
    the vendor's country → the household default vendor country (a settings-level
    fallback, never overrides the above) → inferred from the currency (the coarsest
    fallback)."""
    for explicit in (txn_country, vendor_country, default_country):
        if explicit:
            return explicit.upper()
    if currency:
        return CURRENCY_COUNTRY.get(currency.upper())
    return None


def name(code: str | None) -> str:
    if not code:
        return "Unknown"
    return COUNTRY_NAMES.get(code.upper(), code.upper())


def flag(code: str | None) -> str:
    """The regional-indicator flag emoji for a 2-letter code (🏳️ when unknown)."""
    if not code or len(code) != 2 or not code.isalpha():
        return "\U0001F3F3️"  # 🏳️
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in code.upper())
