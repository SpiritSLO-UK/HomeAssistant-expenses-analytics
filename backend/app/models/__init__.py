"""SQLAlchemy models for HA Finance Intelligence (spec §12).

Importing this package registers every model on ``Base.metadata`` so Alembic
autogenerate and ``Base.metadata.create_all`` see the full schema.
"""

from app.db.base import Base
from app.models.account import Account
from app.models.ai_request import AIRequest
from app.models.audit_log import AuditLog
from app.models.budget import Budget
from app.models.category import Category
from app.models.fx_rate import FxRate
from app.models.household import Household
from app.models.project import Project
from app.models.receipt import Receipt, ReceiptItem, TransactionReceiptMatch
from app.models.review_item import ReviewItem
from app.models.rule import Rule
from app.models.savings import SavingsBalance, SavingsGoal
from app.models.setting import Setting
from app.models.statement import Statement
from app.models.subscription import Subscription
from app.models.tag import Tag, transaction_tags
from app.models.transaction import Transaction, TransactionSplit
from app.models.user import User
from app.models.user_session import UserSession
from app.models.vendor import Vendor, VendorAlias

__all__ = [
    "Base",
    "Account",
    "AIRequest",
    "AuditLog",
    "Budget",
    "Category",
    "FxRate",
    "Household",
    "Project",
    "Receipt",
    "ReceiptItem",
    "TransactionReceiptMatch",
    "ReviewItem",
    "Rule",
    "SavingsBalance",
    "SavingsGoal",
    "Setting",
    "Statement",
    "Subscription",
    "Tag",
    "transaction_tags",
    "Transaction",
    "TransactionSplit",
    "User",
    "UserSession",
    "Vendor",
    "VendorAlias",
]
