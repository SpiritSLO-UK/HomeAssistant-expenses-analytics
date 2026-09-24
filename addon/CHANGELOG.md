<!-- Home Assistant Supervisor reads the add-on changelog from this file
     (addon/CHANGELOG.md) and renders it on the add-on page. It deliberately
     carries ONLY THE CURRENT RELEASE, so that page stays short and current;
     the full history lives in the repo-root /CHANGELOG.md. At release time,
     replace the entry below with the new version's user-facing notes. -->
# Changelog

## v1.2.3 - 2026-09-24

> Provided "as is", no warranty, not financial advice - keep your own backups.

A small feature release on top of v1.2.2, from a user request. Data and config
carry over; no new database migrations.

### What's new

- **Add a transaction by hand.** Until now, every transaction needed a document
  first: a bank statement to import, or a receipt to photograph. That left no way
  to record cash you spent with no receipt, or income that never appears on a
  statement. The **Transactions** page now has an **Add transaction** form
  (amount, date, description, category and account), and the Dashboard's
  **Quick add** links straight to it.

  Type the amount as a positive number and choose **Expense** or **Income** - the
  app applies the sign, so a salary you enter counts as income and a cash coffee
  counts as spend. If you haven't set up a bank account yet, the entry is filed
  under the shared *Cash & receipts* account. New entries are converted to your
  base currency and auto-categorised exactly like imported ones, so they can be
  edited, split, tagged, exported and counted in every total afterwards.

---

Older releases: see the
[full changelog](https://github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics/blob/main/CHANGELOG.md)
on GitHub.
