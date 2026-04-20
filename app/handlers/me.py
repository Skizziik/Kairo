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
    p = await repos.get_profile_full(uid)
    async with pool().acquire() as conn:
        mem_count = await conn.fetchval("select count(*) from memories where user_id = $1", uid)
        msg_count = await conn.fetchval("select count(*) from messages where tg_user_id = $1", uid)

    lines = ["<b>Твой профиль у RIP нагибатора</b>"]
    lines.append(f"Сообщений в базе: <b>{msg_count}</b>")
    lines.append(f"Воспоминаний: <b>{mem_count}</b>")

    if p and p.get("updated_at"):
        lines.append(f"<i>Профиль обновлён: {p['updated_at'].strftime('%Y-%m-%d %H:%M UTC')}</i>")

    summary = (p or {}).get("summary", "").strip() if p else ""
    traits = (p or {}).get("traits", {}) if p else {}

    if summary:
        lines.append("")
        lines.append("<b>Что я про тебя знаю:</b>")
        lines.append(escape(summary))

    if traits:
        lines.append("")
        lines.append("<b>Черты:</b>")
        for k, v in traits.items():
            if isinstance(v, list):
                v = ", ".join(str(x) for x in v)
            lines.append(f"• <b>{escape(str(k))}:</b> {escape(str(v))}")

    if not summary and not traits:
        lines.append("")
        lines.append("<i>Профиль пока пустой — поболтай в чате, экстрактор подхватит.</i>")

    memories = await repos.recent_memories(uid, limit=5)
    if memories:
        lines.append("")
        lines.append("<b>Последние воспоминания:</b>")
        for content, _imp, _ts in memories:
            lines.append(f"• {escape(content)}")

    await msg.reply("\n".join(lines))
