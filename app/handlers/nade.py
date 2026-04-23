from __future__ import annotations

import json
import logging
import os
import random
import re
from html import escape

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

router = Router(name="nade")
log = logging.getLogger(__name__)

_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "raskidki.json")

MAP_ALIASES: dict[str, str] = {
    "mirage": "mirage", "мираж": "mirage", "de_mirage": "mirage",
    "inferno": "inferno", "инферно": "inferno", "infer": "inferno", "de_inferno": "inferno",
    "dust2": "dust2", "даст": "dust2", "dust": "dust2", "d2": "dust2", "de_dust2": "dust2",
    "nuke": "nuke", "нюк": "nuke", "de_nuke": "nuke",
    "anubis": "anubis", "анубис": "anubis", "de_anubis": "anubis",
    "ancient": "ancient", "древний": "ancient", "de_ancient": "ancient",
    "train": "train", "трейн": "train", "de_train": "train",
}

TYPE_EMOJI = {"smoke": "💨", "flash": "⚡", "molo": "🔥", "HE": "💥"}


def _load() -> dict:
    try:
        with open(_DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        log.exception("failed to load raskidki.json")
        return {}


def _normalize_map(arg: str) -> str | None:
    arg = (arg or "").strip().lower()
    return MAP_ALIASES.get(arg)


def _format_lineup(m: str, lineup: dict) -> str:
    emoji = TYPE_EMOJI.get(lineup.get("type", ""), "🎯")
    side = lineup.get("side", "")
    name = escape(lineup.get("name", "—"))
    desc = escape(lineup.get("desc", ""))
    ref = lineup.get("ref")
    header = f"{emoji} <b>{name}</b> <i>({side}-side, {m})</i>"
    body = desc
    tail = f"\n🔗 <a href=\"{escape(ref, quote=True)}\">подробнее и видео</a>" if ref else ""
    return f"{header}\n\n{body}{tail}"


@router.message(Command("nade", "raskidka", "раскидка"))
async def cmd_nade(msg: Message, command: CommandObject) -> None:
    db = _load()
    arg = (command.args or "").strip()
    parts = arg.split(maxsplit=1)

    if not parts:
        maps = ", ".join(db.keys())
        await msg.reply(
            "Формат: <code>/nade mirage</code> (случайная раскидка)\n"
            "Или: <code>/nade mirage jungle</code> (поиск по названию)\n\n"
            f"Доступные карты: {maps}"
        )
        return

    map_name = _normalize_map(parts[0])
    if map_name is None or map_name not in db:
        await msg.reply(f"Не знаю карту «{escape(parts[0])}». Попробуй: mirage, inferno, dust2, nuke, anubis, ancient, train.")
        return

    map_data = db[map_name]
    lineups = map_data.get("lineups", [])
    if not lineups:
        await msg.reply("Для этой карты пока пусто.")
        return

    # Optional name filter (2nd word)
    if len(parts) > 1:
        query = parts[1].lower()
        match = [l for l in lineups if query in l.get("name", "").lower() or query in l.get("desc", "").lower()]
        if not match:
            await msg.reply(f"По запросу «{escape(parts[1])}» ничего на {map_data['title']}.")
            return
        pick = match[0]
    else:
        pick = random.choice(lineups)

    await msg.reply(_format_lineup(map_data["title"], pick), disable_web_page_preview=True)
