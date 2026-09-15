"""Shared LLM helpers — multi-provider via factory."""

from __future__ import annotations

import json
import re
from typing import Any

from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel

from src.llm.factory import get_provider
from src.settings import PROMPTS_DIR, get_config

_env = Environment(loader=FileSystemLoader(str(PROMPTS_DIR)), autoescape=False)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _strip_model_artifacts(text: str) -> str:
    text = text.strip()
    for suffix in ("\x3c/th\x6e\x6b\x3e", "</think>"):
        if suffix in text:
            text = text.split(suffix, 1)[-1].strip()
    return text


def render_template(template_name: str, **ctx: Any) -> str:
    return _env.get_template(template_name).render(**ctx)


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = _FENCE_RE.sub("", text).strip()
    return text


def _repair_json(text: str) -> str:
    text = _strip_fences(text)
    text = _TRAILING_COMMA_RE.sub(r"\1", text)
    return text


def extract_json(text: str) -> dict:
    text = _strip_model_artifacts(text)
    text = _repair_json(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        chunk = text[start : end + 1]
        chunk = _TRAILING_COMMA_RE.sub(r"\1", chunk)
        try:
            return json.loads(chunk)
        except json.JSONDecodeError:
            pass
    raise ValueError("No JSON found in LLM response")


def chat(
    prompt: str,
    system: str = "",
    model: str | None = None,
    schema: type[BaseModel] | None = None,
    options: dict[str, Any] | None = None,
    temperature: float | None = None,
) -> str:
    cfg = get_config()
    if temperature is not None:
        options = {**(options or {}), "temperature": float(temperature)}
    model = model or cfg["default_model"]
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    provider = get_provider()
    return provider.chat(model, messages, format_schema=schema, options=options)


def chat_json(
    prompt: str,
    system: str = "",
    model: str | None = None,
    schema: type[BaseModel] | None = None,
    *,
    num_predict: int = 4096,
    temperature: float | None = None,
) -> dict:
    json_options: dict[str, Any] = {"num_predict": num_predict}
    if temperature is not None:
        json_options["temperature"] = float(temperature)
    content = chat(
        prompt,
        system=system,
        model=model,
        schema=schema,
        options=json_options,
    )
    try:
        return extract_json(content)
    except (ValueError, json.JSONDecodeError):
        retry_system = (system + "\nReturn ONLY a single valid JSON object. No markdown, no prose.").strip()
        content = chat(
            prompt + "\n\nRespond with raw JSON only.",
            system=retry_system,
            model=model,
            schema=schema,
            options=json_options,
        )
        try:
            return extract_json(content)
        except (ValueError, json.JSONDecodeError) as err:
            tail = content[-120:].replace("\n", "\\n")
            raise ValueError(f"No JSON found in LLM response (len={len(content)}, tail={tail!r})") from err
