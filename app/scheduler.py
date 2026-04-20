from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot

from app.ai import llm
from app.config import get_settings
from app.db import repos

log = logging.getLogger(__name__)


def _seconds_until_hour_utc(hour: int) -> float:
    now = datetime.now(timezone.utc)
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _format_msgs(msgs: list[repos.MessageRow]) -> str:
    lines = []
    for m in msgs:
        if m.is_bot:
            continue
        who = m.username or m.first_name or f"user{m.tg_user_id}"
        lines.append(f"@{who}: {m.text}")
    return "\n".join(lines)


async def _build_daily_summary(chat_id: int) -> str | None:
    msgs = await repos.messages_since_hours(chat_id, hours=24)
    if len(msgs) < 10:
        return None
    window = _format_msgs(msgs)
    system = (
        "Ты — летописец чата RIP CS2. Тебе дают сообщения за сутки. "
        "Собери короткий итог дня: 5–8 пунктов буллетами, на русском. "
        "Каждый пункт — конкретное событие/шутка/договорённость/инсайт с @никами. "
        "Стиль: слегка иронично, как пацан рассказывает что было. "
        "Без воды, без морали, без оценок. Только факты и юмор."
    )
    user_prompt = f"Сообщения за последние сутки:\n\n{window}\n\nИтог дня:"
    try:
        text = await llm.chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=700,
        )
    except Exception:
        log.exception("daily summary LLM call failed")
        return None
    if not text.strip():
        return None
    header = "<b>📒 Итоги дня</b>\n\n"
    return header + text


async def daily_summary_loop(bot: Bot) -> None:
    s = get_settings()
    while True:
        try:
            delay = _seconds_until_hour_utc(s.daily_summary_hour_utc)
            log.info("next daily summary in %.0fs", delay)
            await asyncio.sleep(delay)
            if s.tg_allowed_chat_id is None:
                log.info("daily summary: TG_ALLOWED_CHAT_ID not set, skipping")
                continue
            summary = await _build_daily_summary(s.tg_allowed_chat_id)
            if summary is None:
                log.info("daily summary: nothing to summarize")
                continue
            await bot.send_message(s.tg_allowed_chat_id, summary)
            log.info("daily summary posted to chat=%s", s.tg_allowed_chat_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("daily summary loop iteration failed")
            # prevent hot-loop on repeated errors
            await asyncio.sleep(300)
