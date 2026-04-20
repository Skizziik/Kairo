from __future__ import annotations

import logging
from html import escape

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.db import repos

router = Router(name="profile")
log = logging.getLogger(__name__)


def _format_profile(p: dict, tg_id: int) -> str:
    name = p.get("username") or p.get("first_name") or f"user{tg_id}"
    display = f"@{p['username']}" if p.get("username") else name
    lines = [f"<b>Что бот помнит про {escape(display)}</b>"]
    if p.get("updated_at"):
        lines.append(f"<i>обновлено: {p['updated_at'].strftime('%Y-%m-%d %H:%M UTC')}</i>")
    lines.append("")

    summary = (p.get("summary") or "").strip()
    if summary:
        lines.append("<b>Описание:</b>")
        lines.append(escape(summary))
    else:
        lines.append("<i>Описания пока нет.</i>")

    traits = p.get("traits") or {}
    if traits:
        lines.append("")
        lines.append("<b>Черты:</b>")
        for k, v in traits.items():
            if isinstance(v, list):
                v = ", ".join(str(x) for x in v)
            lines.append(f"• <b>{escape(str(k))}:</b> {escape(str(v))}")
    return "\n".join(lines)


async def _show_profile(msg: Message, tg_id: int) -> None:
    p = await repos.get_profile_full(tg_id)
    if p is None:
        await msg.reply("Такого юзера нет в базе — пусть сначала что-нибудь напишет.")
        return
    text = _format_profile(p, tg_id)

    memories = await repos.recent_memories(tg_id, limit=8)
    if memories:
        text += "\n\n<b>Последние воспоминания:</b>"
        for content, _imp, ts in memories:
            text += f"\n• {escape(content)}"

    await msg.reply(text)


@router.message(Command("profile"))
async def cmd_profile(msg: Message, command: CommandObject) -> None:
    if msg.reply_to_message and msg.reply_to_message.from_user:
        await _show_profile(msg, msg.reply_to_message.from_user.id)
        return
    arg = (command.args or "").strip()
    if not arg:
        if msg.from_user:
            await _show_profile(msg, msg.from_user.id)
        return
    # Try @username lookup
    uid = await repos.find_user_by_username(arg)
    if uid is None:
        await msg.reply("Не нашёл такого. Формат: /profile @ник  или  реплайни на сообщение.")
        return
    await _show_profile(msg, uid)
