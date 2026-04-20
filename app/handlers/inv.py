from __future__ import annotations

import logging
from html import escape

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.services import steam

router = Router(name="inv")
log = logging.getLogger(__name__)


@router.message(Command("inv"))
async def cmd_inv(msg: Message, command: CommandObject) -> None:
    arg = (command.args or "").strip()
    if not arg:
        await msg.reply("Как юзать: /inv &lt;steamid64 / vanity / profile_url&gt;")
        return
    sid = await steam.resolve_steamid64(arg)
    if sid is None:
        await msg.reply("Не нашёл такого Steam-профиля.")
        return
    inv = await steam.fetch_cs2_inventory_summary(sid)
    if inv is None:
        await msg.reply("Инвентарь закрыт либо Steam не отдал. Открой в настройках видимость инвентаря.")
        return
    lines = [f"<b>Инвентарь CS2</b> (<code>{sid}</code>)"]
    lines.append(f"Предметов: <b>{inv['count']}</b>")
    if inv["preview"]:
        lines.append("")
        lines.append("Первые предметы:")
        for name in inv["preview"]:
            lines.append(f"  • {escape(name)}")
    lines.append("")
    lines.append("<i>Точные цены не считаю — платные API не подключал. Если надо — потом прикручу.</i>")
    await msg.reply("\n".join(lines))
