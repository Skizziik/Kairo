from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.ai.memory import summarize_recent

router = Router(name="tldr")
log = logging.getLogger(__name__)


@router.message(Command("tldr"))
async def cmd_tldr(msg: Message, command: CommandObject) -> None:
    try:
        limit = int((command.args or "80").strip())
    except ValueError:
        limit = 80
    limit = max(20, min(limit, 200))
    try:
        await msg.bot.send_chat_action(msg.chat.id, "typing")
        text = await summarize_recent(msg.chat.id, limit=limit)
    except Exception:
        log.exception("tldr failed")
        await msg.reply("Не шмог. Попробуй ещё раз.")
        return
    await msg.reply(text)
