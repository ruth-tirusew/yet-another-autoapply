"""Static model catalogs per provider."""

from __future__ import annotations

PROVIDER_MODELS: dict[str, list[str]] = {
    "ollama": [],
    "groq": [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "llama3-70b-8192",
        "gemma2-9b-it",
        "mixtral-8x7b-32768",
    ],
    "gemini": [
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.5-pro",
    ],
}

CLOUD_PROVIDERS = frozenset({"groq", "gemini"})
