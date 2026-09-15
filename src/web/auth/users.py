"""User registration and account management."""

from __future__ import annotations

from typing import Any

from src.db import (
    create_user,
    get_user_by_email,
    get_user_by_id,
    link_oauth_account,
    update_user,
)
from src.web.auth.passwords import hash_password, verify_password


class AuthError(Exception):
    pass


def register_user(email: str, password: str, display_name: str = "") -> dict[str, Any]:
    email_norm = email.strip().lower()
    if not email_norm or "@" not in email_norm:
        raise AuthError("Valid email is required")
    if len(password) < 8:
        raise AuthError("Password must be at least 8 characters")
    if get_user_by_email(email_norm):
        raise AuthError("An account with this email already exists")
    return create_user(email_norm, hash_password(password), display_name.strip())


def authenticate_user(email: str, password: str) -> dict[str, Any] | None:
    user = get_user_by_email(email.strip().lower())
    if not user or not verify_password(password, user.get("password_hash")):
        return None
    return user


def get_or_create_oauth_user(
    provider: str,
    provider_user_id: str,
    email: str,
    display_name: str = "",
) -> dict[str, Any]:
    from src.db import get_oauth_account

    existing = get_oauth_account(provider, provider_user_id)
    if existing:
        return get_user_by_id(existing["user_id"]) or {}

    email_norm = email.strip().lower()
    user = get_user_by_email(email_norm) if email_norm else None
    if not user:
        user = create_user(email_norm or f"{provider}_{provider_user_id}@oauth.local", None, display_name)
    link_oauth_account(user["id"], provider, provider_user_id)
    return get_user_by_id(user["id"]) or user


def change_password(user_id: int, current_password: str, new_password: str) -> None:
    user = get_user_by_id(user_id)
    if not user:
        raise AuthError("User not found")
    if not user.get("password_hash"):
        raise AuthError("Set a password via account settings first")
    if not verify_password(current_password, user.get("password_hash")):
        raise AuthError("Current password is incorrect")
    if len(new_password) < 8:
        raise AuthError("New password must be at least 8 characters")
    update_user(user_id, password_hash=hash_password(new_password))


def set_password(user_id: int, new_password: str) -> None:
    if len(new_password) < 8:
        raise AuthError("Password must be at least 8 characters")
    update_user(user_id, password_hash=hash_password(new_password))
