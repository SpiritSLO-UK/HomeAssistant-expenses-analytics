"""Schemas for the accounts API (shared vs private; backlog #66/#82)."""

from __future__ import annotations

from pydantic import BaseModel, Field

# Account types the model documents (account.py).
ACCOUNT_TYPES = {
    "current_account", "credit_card", "savings", "loan", "mortgage", "cash",
    "investment", "pension", "other",
}


class AccountOut(BaseModel):
    id: int
    name: str
    institution: str | None
    account_type: str
    currency: str
    is_active: bool
    owner_user_id: int | None
    owner_name: str | None  # display name of the owner, when set
    is_shared: bool
    is_private: bool  # owner set AND not shared → visible only to owner (+ admin)
    in_use: bool = False  # has referencing rows → can only be merged, not deleted


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    account_type: str | None = None
    is_shared: bool | None = None
    owner_user_id: int | None = None  # changing this is owner/admin-only


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    account_type: str = "current_account"
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    institution: str | None = Field(default=None, max_length=200)
    owner_user_id: int | None = None  # admin only; a non-admin's account is owned by them
    is_shared: bool = False


class AccountMerge(BaseModel):
    target_id: int  # the account the source is folded into (then the source is deleted)
