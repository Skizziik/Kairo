from __future__ import annotations

import logging

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings

log = logging.getLogger(__name__)

MISTRAL_CHAT_URL = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_CHAT_MODEL = "mistral-small-latest"


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=1, max=3),
    retry=retry_if_exception_type(httpx.HTTPError),
    reraise=True,
)
async def chat(
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 800,
    model: str | None = None,
) -> str:
    s = get_settings()
    if not s.mistral_api_key:
        raise RuntimeError("MISTRAL_API_KEY not set — no fallback provider available")
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(
            MISTRAL_CHAT_URL,
            headers={
                "Authorization": f"Bearer {s.mistral_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model or MISTRAL_CHAT_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        r.raise_for_status()
    data = r.json()
    choices = data.get("choices") or []
    if not choices:
        return ""
    return (choices[0].get("message", {}).get("content") or "").strip()
