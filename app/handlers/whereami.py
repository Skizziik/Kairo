from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router(name="whereami")


@router.message(Command("whereami"))
async def cmd_whereami(msg: Message) -> None:
    await msg.answer(
        f"<b>chat_id:</b> <code>{msg.chat.id}</code>\n"
        f"<b>type:</b> {msg.chat.type}\n"
        f"<b>your tg_id:</b> <code>{msg.from_user.id if msg.from_user else '—'}</code>\n\n"
        "Сохрани chat_id в Render как <code>TG_ALLOWED_CHAT_ID</code>, "
        "а свой tg_id положи в <code>TG_ADMIN_IDS</code>."
    )
