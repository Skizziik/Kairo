from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str


def _search_blocking(query: str, limit: int) -> list[SearchHit]:
    # ddgs lib name changed across versions — try both imports.
    try:
        from ddgs import DDGS  # type: ignore
    except ImportError:
        from duckduckgo_search import DDGS  # type: ignore
    hits: list[SearchHit] = []
    with DDGS() as ddgs:
        results = ddgs.text(query, region="ru-ru", safesearch="off", max_results=limit)
        for r in results or []:
            hits.append(
                SearchHit(
                    title=(r.get("title") or "").strip(),
                    url=(r.get("href") or r.get("url") or "").strip(),
                    snippet=(r.get("body") or r.get("snippet") or "").strip(),
                )
            )
    return hits


async def web_search(query: str, limit: int = 5) -> list[SearchHit]:
    query = (query or "").strip()
    if not query:
        return []
    if len(query) > 300:
        query = query[:300]
    try:
        return await asyncio.to_thread(_search_blocking, query, limit)
    except Exception:
        log.exception("web_search failed")
        return []
