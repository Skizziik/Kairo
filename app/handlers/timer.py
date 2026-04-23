from __future__ import annotations

import asyncio
import logging
import re
from html import escape

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

router = Router(name="timer")
log = logging.getLogger(__name__)

DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd]?)", re.IGNORECASE)
UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "": 60}
MAX_SECONDS = 24 * 3600  # 24h cap


@router.message(Command("timer", "напомни"))
async def cmd_timer(msg: Message, command: CommandObject) -> None:
    arg = (command.args or "").strip()
    if not arg:
        await msg.reply("Формат: <code>/timer 5m купить хлеб</code>  или  <code>/timer 30s</code>")
        return
    m = DURATION_RE.match(arg)
    if not m:
        await msg.reply("Не понял длительность. Пример: <code>/timer 10m созвон</code>")
        return
    num = int(m.group(1))
    unit = (m.group(2) or "m").lower()
    seconds = num * UNIT_SECONDS.get(unit, 60)
    if seconds <= 0 or seconds > MAX_SECONDS:
        await msg.reply("Таймер от 1 секунды до 24 часов.")
        return
    note = arg[m.end():].strip() or "пинг"
    if msg.from_user is None:
        return
    nick = f"@{msg.from_user.username}" if msg.from_user.username else (msg.from_user.first_name or "пацан")
    await msg.reply(f"⏱ Засёк {_fmt(seconds)}. Напомню про «{escape(note)}».")

    asyncio.create_task(_fire(msg, nick, note, seconds))


async def _fire(origin_msg: Message, nick: str, note: str, seconds: int) -> None:
    try:
        await asyncio.sleep(seconds)
        await origin_msg.bot.send_message(
            origin_msg.chat.id,
            f"🔔 {escape(nick)}, напоминаю: <b>{escape(note)}</b>",
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("timer fire failed")


def _fmt(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} сек"
    if seconds < 3600:
        return f"{seconds // 60} мин"
    if seconds < 86400:
        return f"{seconds // 3600} ч"
    return f"{seconds // 86400} дн"
