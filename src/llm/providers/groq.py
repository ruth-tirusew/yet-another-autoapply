"""Groq LLM provider (OpenAI-compatible API)."""

from __future__ import annotations

import json
from typing import Any

import requests
from pydantic import BaseModel

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqProvider:
    name = "groq"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        format_schema: type[BaseModel] | dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> str:
        payload: dict[str, Any] = {"model": model, "messages": messages}
        if format_schema:
            payload["response_format"] = {"type": "json_object"}
            if messages and messages[0].get("role") == "system":
                messages[0]["content"] += "\nRespond with a single valid JSON object only."
            else:
                messages.insert(0, {"role": "system", "content": "Respond with a single valid JSON object only."})
        if options and "num_predict" in options:
            payload["max_tokens"] = options["num_predict"]
        if options and options.get("temperature") is not None:
            payload["temperature"] = options["temperature"]
        resp = requests.post(GROQ_API_URL, headers=self._headers(), json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def list_models(self) -> list[str]:
        from src.llm.catalog import PROVIDER_MODELS

        return list(PROVIDER_MODELS["groq"])

    def health_check(self, model: str) -> dict[str, Any]:
        import time

        try:
            start = time.perf_counter()
            resp = requests.post(
                GROQ_API_URL,
                headers=self._headers(),
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
                    "max_tokens": 16,
                },
                timeout=60,
            )
            resp.raise_for_status()
            latency_ms = int((time.perf_counter() - start) * 1000)
            content = resp.json()["choices"][0]["message"]["content"][:200]
            return {"ok": True, "provider": self.name, "model": model, "latency_ms": latency_ms, "sample_response": content}
        except Exception as e:
            return {"ok": False, "provider": self.name, "model": model, "latency_ms": 0, "error": str(e)}
