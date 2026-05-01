"""Anti-Stale Engine — отслеживает n-grams в ответах бота.

После каждого ответа разбивает на 3-grams, инкрементит счётчик в
bot_phrase_freq. На следующем ответе проверяет — если в новом тексте
есть n-grams, использованные >5 раз за 24ч — форсит rewrite.

Ловит "залипание" бота на одних и тех же фразах автоматически.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from app.db import repos

log = logging.getLogger(__name__)


# n-gram длина (3 слова — sweet spot для коротких ответов бота)
NGRAM_SIZE = 3

# Сколько раз фраза должна засветиться за 24ч чтобы считаться "залипшей"
STALE_THRESHOLD = 5

# Лимит на количество n-grams отрабатываемых за один ответ — для perf
MAX_NGRAMS_PER_REPLY = 50

_WORD = re.compile(r"\b\w+\b", re.UNICODE)


def _ngrams(text: str, n: int = NGRAM_SIZE) -> list[str]:
    """3-grams (по словам) lowercased."""
    if not text:
        return []
    words = _WORD.findall(text.lower())
    if len(words) < n:
        return []
    return [" ".join(words[i:i + n]) for i in range(len(words) - n + 1)]


async def track_bot_reply(chat_id: int, text: str) -> None:
    """Регистрирует n-grams бот-ответа в bot_phrase_freq.
    Вызывается после каждой отправки сообщения от бота."""
    grams = _ngrams(text)
    if not grams:
        return
    grams = list(set(grams))[:MAX_NGRAMS_PER_REPLY]
    try:
        for g in grams:
            await repos.bump_phrase_freq(chat_id, g)
    except Exception:
        log.exception("anti_stale track_bot_reply failed")


async def is_stale(chat_id: int, candidate_text: str) -> list[str]:
    """Проверяет — есть ли в кандидатe-тексте n-grams, которые залипли
    в последние 24ч (использованы >= STALE_THRESHOLD раз).

    Возвращает список найденных stale-фраз. Пустой = текст свежий.
    """
    grams = set(_ngrams(candidate_text))
    if not grams:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    try:
        stale = await repos.find_stale_phrases(
            chat_id, list(grams), threshold=STALE_THRESHOLD, since=cutoff,
        )
    except Exception:
        log.exception("anti_stale.is_stale failed")
        return []
    return stale
