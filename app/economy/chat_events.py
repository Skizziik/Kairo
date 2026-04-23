"""Chat events: Happy Hour announcements + Mystery drop giveaways."""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import get_settings
from app.db.client import pool
from app.economy import repo as eco
from app.economy.pricing import compute_price, roll_float, wear_from_float

log = logging.getLogger(__name__)


# ========= HAPPY HOUR announcements =========

HAPPY_HOUR_TYPES = [
    {"code": "slots_2x", "text": "🔥 HAPPY HOUR! Слоты платят x2 следующие 30 минут.", "duration_min": 30},
    {"code": "cases_discount", "text": "🎁 HAPPY HOUR! Все кейсы минус 20% цены на 30 минут.", "duration_min": 30},
    {"code": "coinflip_boost", "text": "🪙 HAPPY HOUR! Coinflip платит 2.0x (вместо 1.95x) 30 минут.", "duration_min": 30},
    {"code": "wheel_extra", "text": "🎡 HAPPY HOUR! Колесо фортуны можно крутить ещё раз!", "duration_min": 60},
]


async def announce_happy_hour(bot: Bot) -> None:
    s = get_settings()
    if s.tg_allowed_chat_id is None:
        return
    evt = random.choice(HAPPY_HOUR_TYPES)
    now = datetime.now(timezone.utc)
    ends_at = now + timedelta(minutes=evt["duration_min"])
    try:
        sent = await bot.send_message(
            s.tg_allowed_chat_id,
            f"{evt['text']}\n\nТы знаешь что делать — заходи в /casino.",
        )
        async with pool().acquire() as conn:
            await conn.execute(
                "insert into economy_events (chat_id, kind, payload, starts_at, ends_at, message_id) "
                "values ($1, $2, '{}'::jsonb, $3, $4, $5)",
                s.tg_allowed_chat_id, evt["code"], now, ends_at, sent.message_id,
            )
    except Exception:
        log.exception("happy hour announce failed")


async def happy_hour_loop(bot: Bot) -> None:
    """Fire a random happy hour every 3-6 hours."""
    while True:
        try:
            wait_min = random.randint(180, 360)
            log.info("next happy hour in %d min", wait_min)
            await asyncio.sleep(wait_min * 60)
            # Skip if night (11pm-8am UTC = ~2am-11am MSK — not ideal for activity)
            hour = datetime.now(timezone.utc).hour
            if hour < 7 or hour >= 22:
                log.info("happy hour skipped — night time")
                continue
            await announce_happy_hour(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("happy hour loop iteration failed")
            await asyncio.sleep(600)


# ========= MYSTERY DROPS =========

async def fire_mystery_drop(bot: Bot) -> None:
    """Drop a random good skin into the chat. First to tap /claim wins."""
    s = get_settings()
    if s.tg_allowed_chat_id is None:
        return
    # Pick a random skin from restricted/classified tier (decent value, not too rare)
    async with pool().acquire() as conn:
        skin = await conn.fetchrow(
            "select id, full_name, image_url, rarity, rarity_color, min_float, max_float, "
            "base_price, stat_trak_available, category "
            "from economy_skins_catalog "
            "where rarity in ('restricted', 'classified') and category = 'weapon' and active "
            "order by random() limit 1"
        )
    if skin is None:
        return
    fl = roll_float(float(skin["min_float"]), float(skin["max_float"]))
    wear_name, _ = wear_from_float(fl)
    st = random.random() < 0.1  # 10% stattrak chance on drops
    price = compute_price(int(skin["base_price"]), fl, wear_name, st)
    now = datetime.now(timezone.utc)
    ends_at = now + timedelta(minutes=10)

    async with pool().acquire() as conn:
        evt = await conn.fetchrow(
            "insert into economy_events (chat_id, kind, payload, starts_at, ends_at, status) "
            "values ($1, 'mystery_drop', $2::jsonb, $3, $4, 'active') returning id",
            s.tg_allowed_chat_id,
            __import__('json').dumps({
                "skin_id": int(skin["id"]),
                "float": fl,
                "wear": wear_name,
                "stat_trak": st,
                "price": price,
            }),
            now, ends_at,
        )
    st_tag = " ST™" if st else ""
    caption = (
        f"🎁 <b>MYSTERY DROP</b>\n\n"
        f"В чат упал: <b>{skin['full_name']}</b>{st_tag}\n"
        f"Wear: {wear_name.replace('_',' ')} · float {fl:.3f}\n"
        f"Цена: ~{price:,} 🪙\n\n"
        f"Первый, кто тапнет кнопку — забирает. Время: 10 минут."
    ).replace(",", " ")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🫳 Забрать!", callback_data=f"claim_drop:{evt['id']}"),
    ]])
    try:
        sent = await bot.send_photo(
            chat_id=s.tg_allowed_chat_id,
            photo=skin["image_url"],
            caption=caption,
            reply_markup=kb,
        )
        async with pool().acquire() as conn:
            await conn.execute(
                "update economy_events set message_id = $2 where id = $1",
                int(evt["id"]), sent.message_id,
            )
    except Exception:
        log.exception("mystery drop send failed")


async def mystery_drop_loop(bot: Bot) -> None:
    """Fire a mystery drop every 2-4 hours (not at night)."""
    while True:
        try:
            wait_min = random.randint(120, 240)
            log.info("next mystery drop in %d min", wait_min)
            await asyncio.sleep(wait_min * 60)
            hour = datetime.now(timezone.utc).hour
            if hour < 7 or hour >= 22:
                log.info("mystery drop skipped — night time")
                continue
            await fire_mystery_drop(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("mystery drop loop iteration failed")
            await asyncio.sleep(600)


async def claim_mystery_drop(user_id: int, event_id: int) -> dict:
    """Atomic first-come-first-serve claim of a mystery drop event."""
    import json as _json
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select payload, claimed_by, ends_at, status from economy_events "
                "where id = $1 and kind = 'mystery_drop' for update",
                event_id,
            )
            if row is None:
                return {"ok": False, "error": "Event not found"}
            if row["claimed_by"] is not None:
                return {"ok": False, "error": "Уже забрали"}
            if datetime.now(timezone.utc) > row["ends_at"]:
                return {"ok": False, "error": "Время вышло"}

            payload = row["payload"] if isinstance(row["payload"], dict) else _json.loads(row["payload"])
            # Insert inventory item
            await conn.execute(
                "insert into economy_inventory "
                "(user_id, skin_id, float_value, wear, stat_trak, price, source, source_ref) "
                "values ($1, $2, $3, $4, $5, $6, 'drop', $7)",
                user_id, int(payload["skin_id"]), float(payload["float"]),
                payload["wear"], bool(payload["stat_trak"]), int(payload["price"]),
                str(event_id),
            )
            await conn.execute(
                "update economy_events set claimed_by = $2, claimed_at = now(), status = 'claimed' "
                "where id = $1",
                event_id, user_id,
            )
    return {"ok": True, "payload": payload}
