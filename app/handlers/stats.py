from __future__ import annotations

import logging
from html import escape

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.services import steam

router = Router(name="stats")
log = logging.getLogger(__name__)


@router.message(Command("stats"))
async def cmd_stats(msg: Message, command: CommandObject) -> None:
    arg = (command.args or "").strip()
    if not arg:
        await msg.reply("Как юзать: /stats &lt;steamid64 / vanity / profile_url&gt;")
        return
    sid = await steam.resolve_steamid64(arg)
    if sid is None:
        await msg.reply("Не нашёл такого Steam-профиля.")
        return

    summary = await steam.fetch_player_summary(sid)
    stats = await steam.fetch_cs2_stats(sid)

    lines = []
    if summary:
        name = summary.get("personaname") or "?"
        profile_url = summary.get("profileurl") or f"https://steamcommunity.com/profiles/{sid}"
        lines.append(f"<b>{escape(name)}</b> — <a href=\"{profile_url}\">profile</a>")
    lines.append(f"SteamID64: <code>{sid}</code>")
    if stats is None:
        lines.append("\n<i>CS2-стата недоступна (профиль приватный или у Valve лапки).</i>")
    else:
        lines.append("")
        lines.append(f"K/D: <b>{stats.kd:.2f}</b> ({stats.kills} / {stats.deaths})")
        lines.append(f"MVP: {stats.mvps}")
        lines.append(f"Раундов: {stats.rounds} • Побед: {stats.wins}")
        if stats.hours:
            lines.append(f"Наиграно: {stats.hours} ч")
    await msg.reply("\n".join(lines), disable_web_page_preview=True)
