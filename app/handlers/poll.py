from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

router = Router(name="poll")
log = logging.getLogger(__name__)


@router.message(Command("poll"))
async def cmd_poll(msg: Message, command: CommandObject) -> None:
    arg = (command.args or "").strip()
    # Split by | or ;  — first is question, rest options
    raw_parts = [p.strip() for p in arg.replace(";", "|").split("|") if p.strip()]
    if len(raw_parts) < 3:
        await msg.reply(
            "Формат: <code>/poll вопрос | вариант1 | вариант2 | вариант3</code>\n"
            "Минимум 2 варианта ответа."
        )
        return
    question = raw_parts[0][:300]
    options = [o[:100] for o in raw_parts[1:11]]
    try:
        await msg.bot.send_poll(
            chat_id=msg.chat.id,
            question=question,
            options=options,
            is_anonymous=False,
            allows_multiple_answers=False,
        )
    except Exception as e:
        log.exception("poll send failed")
        await msg.reply(f"Не смог: {e}")
