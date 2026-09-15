"""Per-request tenant context for multi-user isolation."""

from __future__ import annotations

from contextvars import ContextVar

current_user_id: ContextVar[int | None] = ContextVar("current_user_id", default=None)


def set_tenant_user_id(user_id: int | None) -> None:
    current_user_id.set(user_id)


def get_tenant_user_id() -> int | None:
    return current_user_id.get()


def resolve_user_id(user_id: int | None = None, *, default: int | None = 1) -> int:
    if user_id is not None:
        return user_id
    uid = current_user_id.get()
    if uid is not None:
        return uid
    if default is not None:
        return default
    raise RuntimeError("No tenant user_id in context")
