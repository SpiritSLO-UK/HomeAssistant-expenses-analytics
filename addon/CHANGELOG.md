<!-- Home Assistant Supervisor reads the add-on changelog from this file
     (addon/CHANGELOG.md) and renders it on the add-on page. It deliberately
     carries ONLY THE CURRENT RELEASE, so that page stays short and current;
     the full history lives in the repo-root /CHANGELOG.md. At release time,
     replace the entry below with the new version's user-facing notes. -->
# Changelog

## v1.2.4 - 2026-09-24

> Provided "as is", no warranty, not financial advice - keep your own backups.

A small feature release on top of v1.2.3. Data and config carry over; no new
database migrations.

### What's new

- **20 more base currencies to choose from.** Settings offered ten base
  currencies, so if your money is in zloty, rand, rupees or won you had to run
  the whole app in someone else's currency. The list now holds all 30 currencies
  our online rate source (Frankfurter, which publishes European Central Bank
  rates) covers: the previous ten plus Brazilian real, Czech koruna, Danish
  krone, Hungarian forint, Indonesian rupiah, Israeli new shekel, Indian rupee,
  Icelandic krona, South Korean won, Mexican peso, Malaysian ringgit, Norwegian
  krone, New Zealand dollar, Philippine peso, Polish zloty, Romanian leu, Swedish
  krona, Thai baht, Turkish lira and South African rand.

  The list matches that source on purpose: whichever base you pick, **turning on
  online exchange rates keeps working** instead of leaving you to type rates in
  by hand. Spend in any of the new currencies now also shows against the right
  country on the spending map.

  Nothing changes if you're happy where you are - your base currency, your stored
  amounts and any exchange rates you've saved are all untouched, and a
  transaction could already be in any currency at all.

---

Older releases: see the
[full changelog](https://github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics/blob/main/CHANGELOG.md)
on GitHub.
