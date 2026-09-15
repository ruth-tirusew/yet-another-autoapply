"""Session helpers."""

from __future__ import annotations

import secrets
from typing import Any

from starlette.requests import Request


def get_session_user_id(request: Request) -> int | None:
    raw = request.session.get("user_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def login_user(request: Request, user_id: int) -> None:
    request.session.clear()
    request.session["user_id"] = user_id


def logout_user(request: Request) -> None:
    request.session.clear()


def get_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def validate_csrf(request: Request, token: str | None) -> bool:
    expected = request.session.get("csrf_token")
    if not expected or not token:
        return False
    return secrets.compare_digest(str(expected), str(token))
