from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.ai.embeddings import embed_one
from app.config import get_settings
from app.db import repos

router = Router(name="teach")
log = logging.getLogger(__name__)


@router.message(Command("teach"))
async def cmd_teach(msg: Message, command: CommandObject) -> None:
    if msg.from_user is None:
        return
    s = get_settings()
    if msg.from_user.id not in s.admin_id_set:
        await msg.reply("/teach — только для админов.")
        return

    target_id: int | None = None
    fact: str = ""

    if msg.reply_to_message and msg.reply_to_message.from_user:
        target_id = msg.reply_to_message.from_user.id
        fact = (command.args or "").strip()
    else:
        args = (command.args or "").strip()
        if " " not in args:
            await msg.reply(
                "Как юзать:\n"
                "  <code>/teach @ник факт</code>\n"
                "  или реплайни на сообщение того юзера и напиши <code>/teach факт</code>"
            )
            return
        first, fact = args.split(" ", 1)
        target_id = await repos.find_user_by_username(first)
        if target_id is None:
            await msg.reply(f"Не нашёл юзера {first}. Пусть сначала напишет что-нибудь в чат.")
            return

    fact = fact.strip()
    if not fact:
        await msg.reply("А факт-то где? Напиши что именно запомнить.")
        return

    # Store as semantic memory (with embedding if available)
    try:
        vec = await embed_one(fact)
        if vec is not None:
            await repos.add_memory(target_id, fact, vec, importance=5)
        else:
            log.warning("teach: no embedding available, skipping memory insert")
    except Exception:
        log.exception("teach: embedding/store failed")
        await msg.reply("Не смог сохранить в памяти. Гляну логи.")
        return

    await msg.reply(f"Запомнил про <code>{target_id}</code>: {fact}")
