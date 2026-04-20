from __future__ import annotations

import asyncio
import logging
from typing import Iterable

import numpy as np

from app.config import get_settings

log = logging.getLogger(__name__)

_model = None


def _load() -> None:
    global _model
    if _model is not None:
        return
    from fastembed import TextEmbedding
    s = get_settings()
    log.info("loading embedding model %s", s.embed_model)
    _model = TextEmbedding(model_name=s.embed_model)
    log.info("embedding model ready")


def warmup_embedder() -> None:
    """Call from a thread on startup so the first real request isn't slow."""
    _load()
    # run one throwaway embedding to prime ONNX session
    list(_model.embed(["warmup"]))  # type: ignore[union-attr]


def _embed_sync(texts: list[str]) -> list[np.ndarray]:
    _load()
    return [np.asarray(v, dtype=np.float32) for v in _model.embed(texts)]  # type: ignore[union-attr]


async def embed(texts: Iterable[str]) -> list[np.ndarray]:
    text_list = list(texts)
    if not text_list:
        return []
    return await asyncio.to_thread(_embed_sync, text_list)


async def embed_one(text: str) -> np.ndarray:
    result = await embed([text])
    return result[0]
