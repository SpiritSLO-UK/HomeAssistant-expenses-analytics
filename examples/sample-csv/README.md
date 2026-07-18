# Sample CSV files

Anonymised, fabricated bank statement exports used for development, demos and
tests (spec §32.3). **No real bank data** - every value here is made up.

| File | Parser | Notes |
|------|--------|-------|
| `curve-sample.csv` | `curve_csv` | Simplified: Date, Description, Amount, Currency, Card, Category |
| `curve-app-export-sample.csv` | `curve_csv` | Real Curve app export: `Txn Amount (Funding Card)` is **positive for a spend** (negated on import); refunds are negative. Includes **Curve Cash** rows (CPT rewards): `Curve Cash: <merchant>` = earned cashback → Cashback income; a real merchant funded by Curve Cash (with a GBP Foreign Spend) = a spend |
| `barclays-sample.csv` | `barclays_csv` | Number, Date, Account, Amount, Subcategory, Memo |
| `barclaycard-sample.csv` | `barclaycard_csv` | Barclaycard credit card: **no header**; comma-delimited (a spreadsheet paste is tab-delimited - both parse); dates like `05 Jun 26`; debit/credit split (purchases positive, payment negative); `Crv*` rows are Curve-funded |
| `lloyds-sample.csv` | `lloyds_csv` | Separate Debit/Credit columns |
| `monzo-sample.csv` | `monzo_csv` | Wide export; signed Amount |
| `generic-sample.csv` | `generic_csv` | Unknown bank; Money Out/Money In columns (heuristic mapping) |

Amount convention: negative = money out (debit), positive = money in (credit).
