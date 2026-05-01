"""Inside Jokes Engine — отслеживает повторяющиеся фразы чата как мемы.

Каждое сообщение проверяется на N-grams (3-5 слов). Если фраза повторяется
3+ раз от разных юзеров — становится «мемом чата» в таблице chat_inside_jokes.

Бот в системный промпт инжектит топ-N актуальных мемов чата, чтобы
поддерживать общую культуру.
"""
from __future__ import annotations

import logging
import re

from app.db import repos

log = logging.getLogger(__name__)


# Минимум 3 повтора для статуса "мем"
MIN_REPEATS = 3

# Длина n-gram (3-5 слов фраза)
NGRAM_MIN = 3
NGRAM_MAX = 5

# Сколько мемов инжектить в промпт
INJECT_TOP_N = 5


_WORD = re.compile(r"\b\w{2,}\b", re.UNICODE)


def _extract_ngrams(text: str) -> list[str]:
    """Достаёт 3-5-grams (по словам) из текста, lowercased."""
    if not text:
        return []
    words = _WORD.findall(text.lower())
    if len(words) < NGRAM_MIN:
        return []
    out: list[str] = []
    for n in range(NGRAM_MIN, min(NGRAM_MAX, len(words)) + 1):
        for i in range(len(words) - n + 1):
            out.append(" ".join(words[i:i + n]))
    return out


# Чёрный список — слишком общие фразы которые не "мем"
_GENERIC = {
    "ну ты вообще", "что ты делаешь", "как у тебя", "не знаю что",
    "что-то не так", "у меня всё", "и так далее",
}


async def track_message(chat_id: int, user_id: int, text: str) -> None:
    """Регистрирует n-grams сообщения в таблице. Вызывается на каждое
    сообщение в чате. Idempotent (incrementit use_count если уже есть)."""
    if not text or len(text) < 10:
        return
    grams = _extract_ngrams(text)
    if not grams:
        return
    # Фильтр generic
    grams = [g for g in grams if g not in _GENERIC]

    try:
        for g in set(grams):    # dedup внутри одного сообщения
            await repos.upsert_inside_joke(chat_id, g, user_id)
    except Exception:
        log.exception("inside_jokes track failed")


async def get_top_jokes(chat_id: int, n: int = INJECT_TOP_N) -> list[str]:
    """Возвращает топ-N мемов чата с count >= MIN_REPEATS."""
    try:
        return await repos.top_inside_jokes(chat_id, min_count=MIN_REPEATS, limit=n)
    except Exception:
        log.exception("inside_jokes top fetch failed")
        return []
