from __future__ import annotations

import logging
from typing import Iterable

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings

log = logging.getLogger(__name__)

MISTRAL_EMBED_URL = "https://api.mistral.ai/v1/embeddings"
MISTRAL_MODEL = "mistral-embed"
BATCH_SIZE = 64


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    retry=retry_if_exception_type((httpx.HTTPError,)),
    reraise=True,
)
async def _call(client: httpx.AsyncClient, batch: list[str], key: str) -> list[list[float]]:
    r = await client.post(
        MISTRAL_EMBED_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": MISTRAL_MODEL, "input": batch},
        timeout=20.0,
    )
    r.raise_for_status()
    data = r.json().get("data") or []
    return [d["embedding"] for d in data]


async def embed(texts: Iterable[str]) -> list[list[float]]:
    s = get_settings()
    if not s.mistral_api_key:
        log.warning("MISTRAL_API_KEY not set; embeddings disabled")
        return []
    text_list = [t for t in texts if t and t.strip()]
    if not text_list:
        return []
    out: list[list[float]] = []
    async with httpx.AsyncClient() as c:
        for i in range(0, len(text_list), BATCH_SIZE):
            batch = text_list[i : i + BATCH_SIZE]
            out.extend(await _call(c, batch, s.mistral_api_key))
    return out


async def embed_one(text: str) -> list[float] | None:
    result = await embed([text])
    return result[0] if result else None
