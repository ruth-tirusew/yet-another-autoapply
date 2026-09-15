"""Ollama LLM provider."""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel


class OllamaProvider:
    name = "ollama"

    def __init__(self, host: str = "http://localhost:11434"):
        self.host = host.rstrip("/")
        os.environ["OLLAMA_HOST"] = self.host

    def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        format_schema: type[BaseModel] | dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> str:
        import ollama

        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if format_schema:
            kwargs["format"] = (
                format_schema.model_json_schema()
                if hasattr(format_schema, "model_json_schema")
                else format_schema
            )
        if options:
            kwargs["options"] = options
        response = ollama.chat(**kwargs)
        return response["message"]["content"]

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Ollama accepts a list input and returns one vector each."""
        import ollama

        os.environ["OLLAMA_HOST"] = self.host
        response = ollama.embed(model=model, input=list(texts))
        vectors = (
            response.get("embeddings")
            if isinstance(response, dict)
            else getattr(response, "embeddings", None)
        )
        if not vectors:
            single = (
                response.get("embedding")
                if isinstance(response, dict)
                else getattr(response, "embedding", None)
            )
            if single:
                vectors = [single]
        if not vectors:
            raise RuntimeError(f"Ollama returned no embeddings for model {model}")
        return [[float(x) for x in v] for v in vectors]

    def list_models(self) -> list[str]:
        import ollama

        try:
            models_resp = ollama.list()
            return [m.get("model", m.get("name", "")) for m in models_resp.get("models", [])]
        except Exception:
            return []

    def health_check(self, model: str) -> dict[str, Any]:
        import time

        import ollama

        try:
            start = time.perf_counter()
            response = ollama.chat(
                model=model,
                messages=[{"role": "user", "content": "Reply with exactly: ok"}],
            )
            latency_ms = int((time.perf_counter() - start) * 1000)
            content = response.get("message", {}).get("content", "")[:200]
            return {"ok": True, "provider": self.name, "model": model, "latency_ms": latency_ms, "sample_response": content}
        except Exception as e:
            return {"ok": False, "provider": self.name, "model": model, "latency_ms": 0, "error": str(e)}
