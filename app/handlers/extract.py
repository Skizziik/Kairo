from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.ai.memory import compact_all_users, extract_and_store
from app.config import get_settings

router = Router(name="extract")
log = logging.getLogger(__name__)


@router.message(Command("extract"))
async def cmd_extract(msg: Message) -> None:
    if msg.from_user is None:
        return
    s = get_settings()
    if msg.from_user.id not in s.admin_id_set:
        await msg.reply("Эта команда только для админов.")
        return
    if msg.chat.type == "private":
        await msg.reply("В приватке нечего экстрактить — зови в группе.")
        return

    await msg.bot.send_chat_action(msg.chat.id, "typing")
    try:
        updated = await extract_and_store(msg.chat.id, window_size=80)
    except Exception:
        log.exception("manual extract failed")
        await msg.reply("Экстрактор упал. Смотри логи Render.")
        return
    await msg.reply(f"Готово. Обновлено профилей: <b>{updated}</b>. /me → глянь свой.")


@router.message(Command("compact"))
async def cmd_compact(msg: Message) -> None:
    if msg.from_user is None:
        return
    s = get_settings()
    if msg.from_user.id not in s.admin_id_set:
        await msg.reply("Только для админов.")
        return
    await msg.bot.send_chat_action(msg.chat.id, "typing")
    try:
        result = await compact_all_users()
    except Exception:
        log.exception("compact_all_users failed")
        await msg.reply("Упал. Логи Render.")
        return
    await msg.reply(
        f"Memory compact: прошёл по <b>{result['processed']}</b> из "
        f"<b>{result['total']}</b> юзеров."
    )
