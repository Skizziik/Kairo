from __future__ import annotations

from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.db import repos
from app.db.client import pool

router = Router(name="me")


@router.message(Command("me"))
async def cmd_me(msg: Message) -> None:
    if msg.from_user is None:
        return
    uid = msg.from_user.id
    summary, traits = await repos.get_profile(uid)
    async with pool().acquire() as conn:
        mem_count = await conn.fetchval("select count(*) from memories where user_id = $1", uid)
        msg_count = await conn.fetchval("select count(*) from messages where tg_user_id = $1", uid)

    lines = [f"<b>Твой профиль у RIP нагибатора</b>"]
    lines.append(f"Сообщений в базе: {msg_count}")
    lines.append(f"Воспоминаний: {mem_count}")
    if summary:
        lines.append("")
        lines.append("<b>Что я про тебя знаю:</b>")
        lines.append(escape(summary))
    if traits:
        lines.append("")
        lines.append("<b>Черты:</b>")
        for k, v in traits.items():
            lines.append(f"• {escape(str(k))}: {escape(str(v))}")
    if not summary and not traits:
        lines.append("")
        lines.append("<i>Пока ничего — пообщаемся, разберусь.</i>")
    await msg.reply("\n".join(lines))
