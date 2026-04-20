from __future__ import annotations

import random

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

router = Router(name="map")

# CS2 active duty map pool (update when Valve rotates — as of Apr 2026)
ACTIVE_POOL = [
    "de_mirage",
    "de_inferno",
    "de_nuke",
    "de_ancient",
    "de_anubis",
    "de_dust2",
    "de_train",
]


@router.message(Command("map"))
async def cmd_map(msg: Message, command: CommandObject) -> None:
    arg = (command.args or "").strip().lower()
    if arg in ("pool", "list", "all"):
        lines = "\n".join(f"• {m}" for m in ACTIVE_POOL)
        await msg.reply(f"<b>Active Duty pool:</b>\n{lines}")
        return
    pick = random.choice(ACTIVE_POOL)
    await msg.reply(f"🎲 Катаем <b>{pick}</b>")
