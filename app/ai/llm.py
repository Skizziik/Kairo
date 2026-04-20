from __future__ import annotations

import logging

from openai import APIError, RateLimitError

from app.ai import cerebras, mistral_chat

log = logging.getLogger(__name__)


async def chat(
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 800,
) -> str:
    """Try Cerebras first; on rate-limit / provider error fall back to Mistral chat."""
    try:
        return await cerebras.chat(messages, temperature=temperature, max_tokens=max_tokens)
    except (RateLimitError, APIError) as e:
        log.warning("Cerebras unavailable (%s), falling back to Mistral", type(e).__name__)
        return await mistral_chat.chat(messages, temperature=temperature, max_tokens=max_tokens)
