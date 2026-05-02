"""/villager — opens the Village Tycoon Mini App. Plus quick /vstatus & /vcollect."""
from __future__ import annotations

import logging
from decimal import Decimal

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from app.config import get_settings
from app.villager import game as vt

router = Router(name="villager")
log = logging.getLogger(__name__)


def _ku() -> InlineKeyboardMarkup | None:
    s = get_settings()
    raw = s.villager_url
    tme = s.villager_tme_url
    if not raw and not tme:
        return None
    target = tme or raw
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🏘️ Открыть деревню", url=target or ""),
    ]])


def _kb_webapp() -> InlineKeyboardMarkup | None:
    s = get_settings()
    if not s.villager_url:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🏘️ Открыть деревню",
            web_app=WebAppInfo(url=s.villager_url),
        ),
    ]])


async def _send(msg: Message, text: str) -> None:
    """Pick best button: web_app in private, url with t.me Direct Link in groups."""
    s = get_settings()
    raw = s.villager_url
    tme = s.villager_tme_url
    if not raw and not tme:
        await msg.reply(text + "\n\n<i>Mini App не настроен (VILLAGER_URL пустой).</i>")
        return
    is_private = msg.chat.type == "private"
    try:
        if is_private and raw:
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🏘️ Открыть деревню", web_app=WebAppInfo(url=raw)),
            ]])
            await msg.reply(text, reply_markup=kb)
            return
        target = tme or raw
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🏘️ Открыть деревню", url=target or ""),
        ]])
        await msg.reply(text, reply_markup=kb)
    except TelegramBadRequest as e:
        log.warning("villager button rejected: %s", e.message)
        await msg.reply(text + f"\n\n{raw or tme}")


@router.message(Command("villager"))
async def villager_open(msg: Message) -> None:
    text = (
        "🏘️ <b>Village Tycoon</b>\n"
        "Развивай свою деревню от хижины до мегаполиса.\n\n"
        "Открывай — твои постройки уже работают пока тебя нет."
    )
    await _send(msg, text)


def _fmt(n: Decimal | int | str) -> str:
    try:
        v = int(n)
    except Exception:
        return str(n)
    if v >= 1_000_000_000:
        return f"{v/1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"{v/1_000_000:.1f}M"
    if v >= 10_000:
        return f"{v/1_000:.1f}k"
    return str(v)


@router.message(Command("vstatus"))
async def villager_status(msg: Message) -> None:
    """Quick text summary without opening the app."""
    if not msg.from_user:
        return
    tg_id = msg.from_user.id
    try:
        await vt.ensure_user(
            tg_id,
            username=msg.from_user.username,
            first_name=msg.from_user.first_name,
            last_name=msg.from_user.last_name,
            language_code=msg.from_user.language_code,
            is_premium=bool(getattr(msg.from_user, "is_premium", False)),
        )
        state = await vt.get_state(tg_id)
    except Exception:
        log.exception("vstatus failed")
        await msg.reply("Что-то пошло не так. Попробуй позже.")
        return

    lines = []
    name = state["user"].get("first_name") or state["user"].get("username") or "Игрок"
    lines.append(f"🏘️ <b>{name}</b> · ур. {state['user']['player_level']} · эпоха {state['user']['era']}")
    res_lines = []
    icons = {"wood": "🪵", "stone": "🪨", "food": "🍞", "water": "💧", "gold": "🪙"}
    for r in state["resources"]:
        if r["type"] in icons:
            res_lines.append(f"{icons[r['type']]} {_fmt(r['amount'])}/{_fmt(r['cap'])}")
    lines.append(" · ".join(res_lines))

    pending = state.get("pending_total") or {}
    pending_total = sum(int(float(v)) for v in pending.values())
    if pending_total > 0:
        lines.append(f"\n📦 Накоплено: {_fmt(pending_total)} (используй /vcollect)")

    in_progress = sum(1 for b in state["buildings"] if b["status"] in ("building", "upgrading"))
    if in_progress:
        lines.append(f"🏗 В работе: {in_progress}")

    quests_active = [q for q in state["quests"] if q["status"] == "active"]
    quests_done = [q for q in state["quests"] if q["status"] == "completed"]
    if quests_done:
        lines.append(f"⭐ Готовы к получению: {len(quests_done)} квест(ов)")
    if quests_active and len(quests_active) <= 3:
        lines.append("\nТекущие квесты:")
        for q in quests_active[:3]:
            lines.append(f"• {q['name']}")

    await _send(msg, "\n".join(lines))


@router.message(Command("vcollect"))
async def villager_collect(msg: Message) -> None:
    """Quick collect from chat without opening the app."""
    if not msg.from_user:
        return
    tg_id = msg.from_user.id
    try:
        await vt.ensure_user(
            tg_id,
            username=msg.from_user.username,
            first_name=msg.from_user.first_name,
            last_name=msg.from_user.last_name,
        )
        result = await vt.collect_all(tg_id)
    except Exception:
        log.exception("vcollect failed")
        await msg.reply("Не получилось.")
        return

    if not result.get("ok"):
        await msg.reply("Не получилось.")
        return

    collected = result["data"]["collected"]
    icons = {"wood": "🪵", "stone": "🪨", "food": "🍞", "water": "💧", "gold": "🪙"}
    parts = []
    for k, v in collected.items():
        if int(float(v)) <= 0:
            continue
        icon = icons.get(k, "")
        parts.append(f"{icon} +{_fmt(v)}")
    if not parts:
        await msg.reply("Пока нечего собирать. Зайди позже.")
        return
    await _send(msg, "📦 Собрал!\n" + " · ".join(parts))
