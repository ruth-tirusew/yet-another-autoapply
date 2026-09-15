"""Google Gemini LLM provider."""

from __future__ import annotations

import json
from typing import Any

import requests
from pydantic import BaseModel

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _url(self, model: str, action: str = "generateContent") -> str:
        return f"{GEMINI_BASE}/{model}:{action}?key={self.api_key}"

    def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        format_schema: type[BaseModel] | dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> str:
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []
        for msg in messages:
            if msg["role"] == "system":
                system_parts.append(msg["content"])
                continue
            role = "user" if msg["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg["content"]}]})

        body: dict[str, Any] = {"contents": contents}
        if system_parts:
            body["systemInstruction"] = {"parts": [{"text": "\n".join(system_parts)}]}
        gen_config: dict[str, Any] = {}
        if format_schema:
            gen_config["responseMimeType"] = "application/json"
        if options and "num_predict" in options:
            gen_config["maxOutputTokens"] = options["num_predict"]
        if options and options.get("temperature") is not None:
            gen_config["temperature"] = options["temperature"]
        if gen_config:
            body["generationConfig"] = gen_config

        resp = requests.post(self._url(model), json=body, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """Embed a batch via the batchEmbedContents endpoint."""
        requests_body = {
            "requests": [
                {"model": f"models/{model}", "content": {"parts": [{"text": text}]}}
                for text in texts
            ]
        }
        resp = requests.post(self._url(model, "batchEmbedContents"), json=requests_body, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        return [[float(x) for x in e.get("values", [])] for e in data.get("embeddings", [])]

    def list_models(self) -> list[str]:
        from src.llm.catalog import PROVIDER_MODELS

        return list(PROVIDER_MODELS["gemini"])

    def health_check(self, model: str) -> dict[str, Any]:
        import time

        try:
            start = time.perf_counter()
            resp = requests.post(
                self._url(model),
                json={"contents": [{"role": "user", "parts": [{"text": "Reply with exactly: ok"}]}]},
                timeout=60,
            )
            resp.raise_for_status()
            latency_ms = int((time.perf_counter() - start) * 1000)
            parts = resp.json()["candidates"][0]["content"]["parts"]
            content = "".join(p.get("text", "") for p in parts)[:200]
            return {"ok": True, "provider": self.name, "model": model, "latency_ms": latency_ms, "sample_response": content}
        except Exception as e:
            return {"ok": False, "provider": self.name, "model": model, "latency_ms": 0, "error": str(e)}
