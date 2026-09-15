"""Auth dependencies for FastAPI routes."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import Request

from src.db import get_user_by_id
from src.tenant import set_tenant_user_id
from src.web.auth.sessions import get_session_user_id


class RequiresLogin(Exception):
    def __init__(self, next_url: str):
        self.next_url = next_url


def get_current_user(request: Request) -> dict[str, Any] | None:
    user_id = get_session_user_id(request)
    if user_id is None:
        return None
    return get_user_by_id(user_id)


async def require_user(request: Request) -> dict[str, Any]:
    user = get_current_user(request)
    if not user:
        next_path = str(request.url.path)
        if request.url.query:
            next_path = f"{next_path}?{request.url.query}"
        raise RequiresLogin(quote(next_path, safe=""))
    set_tenant_user_id(user["id"])
    return user
