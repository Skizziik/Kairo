from __future__ import annotations

import logging
from functools import lru_cache

from openai import AsyncOpenAI
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
    return AsyncOpenAI(api_key=s.cerebras_api_key, base_url=s.cerebras_base_url)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    retry=retry_if_exception_type(Exception),
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
