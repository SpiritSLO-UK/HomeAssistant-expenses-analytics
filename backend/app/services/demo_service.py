"""Demo data loader (backlog #16).

Loads a small, fabricated dataset so a new user can see the app populated
without uploading real statements. Runs through the real import pipeline (parse
-> dedupe -> auto-categorise), so re-running is idempotent thanks to source-hash
dedup. Generated in-code (no packaged CSV files needed) so it works inside the
add-on image too.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.services import import_service

# Fabricated Curve-format statement spanning two months. Negative = spend.
_DEMO_CSV = """Date,Description,Amount,Currency,Card,Category
2026-04-02,TESCO STORES 3142 DARTFORD,-54.20,GBP,Visa,Groceries
2026-04-04,SHELL DARTFORD,-61.30,GBP,Visa,Fuel
2026-04-06,NETFLIX.COM,-10.99,GBP,Visa,Subscriptions
2026-04-09,SCREWFIX DIRECT,-44.80,GBP,Visa,DIY
2026-04-12,COSTA COFFEE 482,-3.85,GBP,Visa,Eating Out
2026-04-15,SALARY ACME LTD,2450.00,GBP,Visa,Income
2026-04-18,B&Q 1123 DARTFORD,-128.40,GBP,Visa,DIY
2026-04-22,PETS AT HOME 221,-31.00,GBP,Visa,Pets
2026-04-27,SAINSBURYS S/MKT,-48.10,GBP,Visa,Groceries
2026-05-02,TESCO STORES 3142 DARTFORD,-42.18,GBP,Visa,Groceries
2026-05-03,SCREWFIX DIRECT DARTFORD,-38.99,GBP,Visa,DIY
2026-05-05,TfL TRAVEL CHARGE,-6.40,GBP,Visa,Transport
2026-05-09,AMAZON MARKETPLACE,-23.49,GBP,Visa,Shopping
2026-05-12,SPOTIFY UK,-11.99,GBP,Visa,Subscriptions
2026-05-15,SALARY ACME LTD,2450.00,GBP,Visa,Income
2026-05-20,DELIVEROO,-24.50,GBP,Visa,Eating Out
2026-05-24,BOOTS 1124 DARTFORD,-14.49,GBP,Visa,Health
"""


def load_demo(db: Session) -> dict:
    """Import the demo dataset. Idempotent (duplicates are skipped)."""
    result = import_service.create_import(
        db,
        filename="demo-curve.csv",
        content=_DEMO_CSV.encode("utf-8"),
        parser_id="curve_csv",
    )
    confirmed = import_service.confirm_import(db, result["import_id"])
    return confirmed["report"]
