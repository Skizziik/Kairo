from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(msg: Message) -> None:
    await msg.answer(
        "Ало, я <b>RIP нагибатор</b> — бот чата RIP CS2.\n"
        "Кину статы, соберу 5-стак, отвечу на вопрос.\n\n"
        "Тыкни /help чтобы глянуть чё я умею."
    )
