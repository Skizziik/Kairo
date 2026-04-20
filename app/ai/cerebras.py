from __future__ import annotations

import logging
from functools import lru_cache

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def client() -> AsyncOpenAI:
    s = get_settings()
    # Total budget for Cerebras before we fall back: ~5s.
    return AsyncOpenAI(
        api_key=s.cerebras_api_key,
        base_url=s.cerebras_base_url,
        timeout=5.0,
        max_retries=0,  # tenacity handles it, only for specific exception types
    )


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=0.3, min=0.3, max=1),
    retry=retry_if_exception_type((APIConnectionError, APITimeoutError)),
    reraise=True,
)
async def chat(
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 800,
    model: str | None = None,
) -> str:
    s = get_settings()
    resp = await client().chat.completions.create(
        model=model or s.cerebras_model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()
