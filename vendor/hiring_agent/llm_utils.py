"""
Utility functions for LLM providers.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class _AppProviderAdapter:
    """Adapts src.llm's string-returning provider to hiring_agent's
    Ollama-shaped dict interface (``{"message": {"content": ...}}``).

    hiring_agent's own provider stack only knew Ollama and Gemini, had no
    Groq support, and read API keys from the environment instead of the
    app's per-user encrypted credentials. Delegating here means hiring_agent
    automatically gets whichever provider the app's Settings → Models is
    configured to use.
    """

    def __init__(self, provider: Any):
        self._provider = provider

    def chat(
        self,
        model: str,
        messages: list[dict],
        options: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        chat_options = dict(options) if options else {}
        chat_options.pop("stream", None)
        text = self._provider.chat(
            model,
            messages,
            format_schema=kwargs.get("format"),
            options=chat_options or None,
        )
        return {"message": {"role": "assistant", "content": text}}


def extract_json_from_response(response_text: str) -> str:
    """
    Extract JSON content from markdown code blocks.

    Args:
        response_text: Text that may contain JSON wrapped in markdown code blocks

    Returns:
        Text with markdown code block syntax removed
    """

    response_text = response_text.strip()
    if "<think>" in response_text:
        think_start = response_text.find("<think>")
        think_end = response_text.find("</think>")
        if think_start != -1 and think_end != -1:
            response_text = response_text[:think_start] + response_text[think_end + 8 :]

    # Remove leading ```json if present
    if response_text.startswith("```json"):
        response_text = response_text[7:]
    # Remove trailing ``` if present
    if response_text.endswith("```"):
        response_text = response_text[:-3]
    return response_text


def initialize_llm_provider(model_name: str) -> Any:
    """
    Initialize the LLM provider for the current tenant, delegating to the
    parent app's provider factory (src.llm.factory.get_provider) so
    hiring_agent always uses whichever provider — Ollama, Groq, or Gemini —
    the user has configured in Settings → Models, with the app's own
    per-user encrypted API keys.

    Args:
        model_name: The name of the model to use (kept for logging only;
            provider selection is driven entirely by the user's config).

    Returns:
        A hiring_agent-compatible provider wrapping the app's provider.
    """
    from src.llm.factory import get_provider

    provider = get_provider()
    logger.info(f"🔄 Using {getattr(provider, 'name', 'app-configured')} provider with model {model_name}")
    return _AppProviderAdapter(provider)
