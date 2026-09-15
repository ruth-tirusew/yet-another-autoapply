"""Password reset request and completion."""

from __future__ import annotations

import logging
from typing import Any

from src.db import (
    create_password_reset_token,
    get_password_reset_token,
    get_user_by_email,
    mark_password_reset_token_used,
)
from src.settings import password_reset_show_link, smtp_configured
from src.web.auth.email_send import build_reset_url, send_password_reset_email
from src.web.auth.passwords import hash_password
from src.web.auth.users import AuthError

logger = logging.getLogger(__name__)


def request_password_reset(email: str) -> dict[str, Any]:
    """
    Create a reset token for the user if the account exists.

    Returns {"sent": True, "dev_link": optional URL when dev mode shows link inline}.
    Always use generic messaging in the UI when dev_link is None.
    """
    user = get_user_by_email(email.strip().lower())
    if not user:
        return {"sent": False, "dev_link": None}

    raw_token = create_password_reset_token(user["id"])
    reset_url = build_reset_url(raw_token)

    if smtp_configured():
        try:
            send_password_reset_email(user["email"], reset_url)
        except Exception:
            logger.exception("Failed to send password reset email to %s", user["email"])
            if password_reset_show_link():
                return {"sent": True, "dev_link": reset_url}
            raise
        return {"sent": True, "dev_link": None}

    if password_reset_show_link():
        logger.info("Password reset link (dev): %s", reset_url)
        return {"sent": True, "dev_link": reset_url}

    logger.warning(
        "Password reset requested but SMTP is not configured and PASSWORD_RESET_DEV is off"
    )
    return {"sent": True, "dev_link": None}


def reset_password_with_token(raw_token: str, new_password: str) -> None:
    if len(new_password) < 8:
        raise AuthError("Password must be at least 8 characters")

    row = get_password_reset_token(raw_token)
    if not row:
        raise AuthError("This reset link is invalid or has expired")

    from src.db import update_user

    update_user(row["user_id"], password_hash=hash_password(new_password))
    mark_password_reset_token_used(raw_token)
