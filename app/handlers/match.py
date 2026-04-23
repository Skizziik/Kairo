from __future__ import annotations

import random
from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router(name="match")

OUTCOMES = [
    "{winner} читает {loser} как раскрытую книгу — {winner} в атаке с 5:0",
    "пистольник: {winner} 3 headshot-а, {loser} на респе с 300 евро",
    "1v1 на миде: {winner} crosshair placement enabled, {loser} превращается в пиксельную пыль",
    "{winner} берёт AWP, {loser} с дегла — {loser} живёт 0.3 секунды",
    "{loser} попытался agressive peek, {winner} preaim-нул на слух — goodnight",
    "{winner} 4:0, {loser} в чате: «lag, ping, не мой день»",
    "обменялись по 1, но {winner} economy management сильнее — {loser} на сейве сломался",
    "{winner} eco раунд клатчит пятёркой, {loser} срёт со страха в спирсе",
    "{winner} ставит smoke on CT, {loser} до сих пор ищет где она кончается",
    "{winner} tap-tap-tap, {loser} спрей на 10 метров. итог очевиден",
    "{loser} нашёл angle, {winner} нашёл {loser}. 1-0",
]


@router.message(Command("match", "1v1"))
async def cmd_match(msg: Message) -> None:
    if msg.reply_to_message is None or msg.reply_to_message.from_user is None:
        await msg.reply(
            "Реплайни на сообщение второго участника: <code>/match</code>\n"
            "или <code>/1v1</code> в ответ на любое сообщение."
        )
        return
    if msg.from_user is None:
        return
    a = msg.from_user
    b = msg.reply_to_message.from_user
    if a.id == b.id:
        await msg.reply("Сам с собой 1v1? Садись за deathmatch лучше.")
        return
    if b.is_bot:
        await msg.reply("С ботами я не сражаюсь, братан.")
        return

    names = {
        a.id: f"@{a.username}" if a.username else (a.first_name or f"user{a.id}"),
        b.id: f"@{b.username}" if b.username else (b.first_name or f"user{b.id}"),
    }
    winner_id = random.choice([a.id, b.id])
    loser_id = b.id if winner_id == a.id else a.id
    scenario = random.choice(OUTCOMES).format(
        winner=escape(names[winner_id]),
        loser=escape(names[loser_id]),
    )
    await msg.reply(
        f"⚔️ <b>1v1</b>: {escape(names[a.id])} vs {escape(names[b.id])}\n\n{scenario}\n\n"
        f"🏆 <b>Победил {escape(names[winner_id])}</b>"
    )
