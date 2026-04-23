from __future__ import annotations

import random

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

router = Router(name="roll")


@router.message(Command("roll"))
async def cmd_roll(msg: Message, command: CommandObject) -> None:
    arg = (command.args or "").strip()
    low, high = 1, 100
    if arg:
        parts = arg.split()
        try:
            if len(parts) == 1:
                high = max(2, int(parts[0]))
            elif len(parts) >= 2:
                low = int(parts[0])
                high = int(parts[1])
                if high < low:
                    low, high = high, low
        except ValueError:
            await msg.reply("Формат: /roll  или  /roll 100  или  /roll 1 6")
            return
    result = random.randint(low, high)
    await msg.reply(f"🎲 <b>{result}</b>   <i>({low}-{high})</i>")
