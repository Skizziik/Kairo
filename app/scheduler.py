from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot

from app.ai import llm, summary_formats
from app.ai.memory import compact_all_users
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
    """Daily summary 2.0 — каждый день один из 6 рандомных форматов
    (с anti-repeat 3 дня). См. app/ai/summary_formats.py.
    """
    msgs = await repos.messages_since_hours(chat_id, hours=24)
    if len(msgs) < 10:
        return None
    window = _format_msgs(msgs)

    # Получаем какие форматы использовали последние 3 дня (антирепит)
    recent_keys: list[str] = []
    try:
        async with repos.pool().acquire() as conn:
            row = await conn.fetchrow(
                "select extras from bot_chat_state where chat_id = $1", chat_id,
            )
            if row is not None:
                raw = row["extras"]
                if isinstance(raw, dict):
                    extras = raw
                else:
                    import json as _json
                    extras = _json.loads(raw or "{}")
                recent_keys = list(extras.get("recent_summary_keys", []))[-3:]
    except Exception:
        log.exception("could not load recent summary keys")

    fmt = summary_formats.pick_format(recent_keys)
    log.info("daily summary chat=%s using format=%s", chat_id, fmt.key)

    user_prompt = f"Сообщения за последние сутки:\n\n{window}\n\nСформируй итог дня по выбранному формату."
    try:
        text = await llm.chat(
            messages=[
                {"role": "system", "content": fmt.system},
                {"role": "user", "content": user_prompt},
            ],
            temperature=fmt.temperature,
            max_tokens=fmt.max_tokens,
        )
    except Exception:
        log.exception("daily summary LLM call failed")
        return None
    if not text.strip():
        return None

    # Запоминаем использованный формат для anti-repeat
    try:
        import json as _json
        async with repos.pool().acquire() as conn:
            row = await conn.fetchrow(
                "select extras from bot_chat_state where chat_id = $1 for update",
                chat_id,
            )
            extras = {}
            if row is not None:
                raw = row["extras"]
                extras = raw if isinstance(raw, dict) else _json.loads(raw or "{}")
            keys = list(extras.get("recent_summary_keys", []))
            keys.append(fmt.key)
            extras["recent_summary_keys"] = keys[-7:]   # храним 7 последних
            await conn.execute(
                """
                insert into bot_chat_state (chat_id, extras, updated_at)
                values ($1, $2::jsonb, now())
                on conflict (chat_id) do update set
                    extras = excluded.extras,
                    updated_at = now()
                """,
                chat_id, _json.dumps(extras),
            )
    except Exception:
        log.exception("could not save summary format key")

    header = f"<b>{fmt.name}</b>\n\n"
    return header + text


def _seconds_until_weekly(weekday: int, hour_utc: int) -> float:
    """Seconds until next occurrence of given weekday (0=Mon) at hour UTC."""
    now = datetime.now(timezone.utc)
    days_ahead = (weekday - now.weekday()) % 7
    target = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    if days_ahead == 0 and target <= now:
        days_ahead = 7
    target += timedelta(days=days_ahead)
    return (target - now).total_seconds()


async def weekly_memory_compact_loop() -> None:
    """Sunday 03:00 UTC: consolidate each user's memories through LLM."""
    while True:
        try:
            delay = _seconds_until_weekly(weekday=6, hour_utc=3)  # Sunday 3 AM UTC
            log.info("next memory compact in %.0fs", delay)
            await asyncio.sleep(delay)
            result = await compact_all_users()
            log.info("weekly memory compact result: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("weekly memory compact loop iteration failed")
            await asyncio.sleep(600)


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


# ════════════════════════════════════════════════════════════════
# Phase 6: Active Initiator — бот сам пишет в тихий чат раз в день
# ════════════════════════════════════════════════════════════════
_ACTIVE_INITIATOR_OPENERS = [
    "здарова дауны, кто живой?",
    "чё, тишина? я скучаю по вашему треску",
    "слышу как мухи летают. где все?",
    "так, проверка связи. кого нет — тот должен два",
    "залип в потолок, выйду на связь",
    "хэй, кто тут хочет рандомный факт?",
    "если что я ещё работаю",
    "вот сижу думаю — может пора что-то новое попробовать",
    "ну как там в реале, пацаны?",
    "молчите как сговорились. знал бы что секрет — присоединился",
]


async def active_initiator_loop(bot: Bot) -> None:
    """Раз в день в случайный час 10-22 MSK проверяет тихий ли чат.
    Если последнее сообщение было >2 часов назад — пишет рандомный opener.

    Помогает боту "присутствовать" даже когда чат спит. Лимит — один заход
    в сутки максимум.
    """
    import random
    s = get_settings()
    while True:
        try:
            # Каждый новый день — новый рандомный час 10-22 MSK
            now = datetime.now(timezone.utc)
            msk_hour = (now + timedelta(hours=3)).hour
            target_msk_hour = random.randint(10, 22)
            # Считаем сколько ждать до этого часа сегодня (или завтра)
            target_utc_hour = (target_msk_hour - 3) % 24
            target = now.replace(hour=target_utc_hour, minute=random.randint(0, 59),
                                 second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            delay = (target - now).total_seconds()
            log.info("next active-initiator check in %.0fs (MSK %02d:%02d)",
                     delay, target_msk_hour, target.minute)
            await asyncio.sleep(delay)

            if s.tg_allowed_chat_id is None:
                continue
            # Чат тихий? Ищем последнее сообщение
            recent = await repos.recent_messages(s.tg_allowed_chat_id, 1)
            if recent:
                last_msg_at = recent[-1].created_at
                # tz-aware compare
                if last_msg_at.tzinfo is None:
                    last_msg_at = last_msg_at.replace(tzinfo=timezone.utc)
                idle_hours = (datetime.now(timezone.utc) - last_msg_at).total_seconds() / 3600
                if idle_hours < 2:
                    log.info("active-initiator: chat активен (idle=%.1fh), skip", idle_hours)
                    continue

            # Чат тихий — пишем
            opener = random.choice(_ACTIVE_INITIATOR_OPENERS)
            try:
                await bot.send_message(s.tg_allowed_chat_id, opener)
                log.info("active-initiator: posted opener to chat=%s",
                         s.tg_allowed_chat_id)
            except Exception:
                log.exception("active-initiator send failed")

        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("active-initiator loop iteration failed")
            await asyncio.sleep(3600)
