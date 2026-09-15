"""LLM provider protocol."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel


class LLMProvider(Protocol):
    name: str

    def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        format_schema: type[BaseModel] | dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> str: ...

    def list_models(self) -> list[str]: ...

    def health_check(self, model: str) -> dict[str, Any]: ...
