from __future__ import annotations

import logging
from html import escape

from aiogram import F, Router
from aiogram.types import Message

router = Router(name="welcome")
log = logging.getLogger(__name__)


@router.message(F.new_chat_members)
async def on_new_members(msg: Message) -> None:
    if not msg.new_chat_members:
        return
    me = await msg.bot.me()
    for member in msg.new_chat_members:
        # Bot itself was added — don't welcome yourself
        if member.id == me.id:
            continue
        if member.is_bot:
            continue
        display = f"@{member.username}" if member.username else escape(member.first_name or "братан")
        text = (
            f"Хэй, {display} — залетаешь в <b>RIP CS2</b>. Я <b>RIP нагибатор</b>, "
            f"тут за токсик-тиммейта и память чата.\n\n"
            f"Чё умею — жми /help.\n"
            f"Хочешь чтоб я тебя запомнил как играешь — просто болтай, я подхвачу.\n"
            f"Обращайся когда угодно: /ai, реплаем на меня или назови по имени (нагибатор/кайро).\n\n"
            f"Ну, раскачиваем."
        )
        try:
            await msg.answer(text)
        except Exception:
            log.exception("welcome failed")
