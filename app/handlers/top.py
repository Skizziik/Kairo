from __future__ import annotations

from html import escape

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.db import repos

router = Router(name="top")


@router.message(Command("top"))
async def cmd_top(msg: Message, command: CommandObject) -> None:
    try:
        days = int((command.args or "7").strip())
    except ValueError:
        days = 7
    days = max(1, min(days, 30))
    rows = await repos.top_active(msg.chat.id, days=days, limit=10)
    if not rows:
        await msg.reply("Пока тишина, пишите больше.")
        return
    lines = [f"<b>Топ активных за {days} дн.</b>"]
    medals = ["🥇", "🥈", "🥉"] + ["  "] * 7
    for (name, n), medal in zip(rows, medals):
        lines.append(f"{medal} {escape(str(name))} — {n}")
    await msg.reply("\n".join(lines))
