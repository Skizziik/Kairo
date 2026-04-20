from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.db import repos

router = Router(name="forget")
log = logging.getLogger(__name__)


@router.message(Command("forget"))
async def cmd_forget(msg: Message) -> None:
    if msg.from_user is None:
        return
    result = await repos.wipe_user_data(msg.from_user.id)
    await msg.reply(
        f"Профиль обнулён, стёрто воспоминаний: <b>{result['memories_deleted']}</b>.\n"
        f"<i>Логи сообщений остаются (они нужны для /tldr и контекста чата), но "
        f"персональных фактов о тебе у меня больше нет.</i>"
    )
