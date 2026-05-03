"""/clicker — opens the CS:Clicker Mini App. Plus quick /cstatus for chat overview."""
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
from app.clicker import game as gm

router = Router(name="clicker")
log = logging.getLogger(__name__)


def _fmt(v) -> str:
    try:
        n = int(Decimal(str(v)))
    except Exception:
        return str(v)
    if n >= 1_000_000_000_000:
        return f"{n/1_000_000_000_000:.1f}T"
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n/1_000:.1f}k"
    return f"{n:,}".replace(",", " ")


async def _send_app_button(msg: Message, text: str) -> None:
    """Mirrors casino's strategy:
    - Private chat: web_app button with raw Mini App URL (opens in-place).
    - Group / channel: url button to t.me Direct-Link Mini App (registered via
      BotFather /newapp). Telegram opens it inline as a Mini App, not browser.
    - On any TelegramBadRequest, fall back to plain text with the URL.
    """
    s = get_settings()
    raw = s.clicker_url
    tme = s.clicker_tme_url
    if not raw and not tme:
        await msg.reply(text + "\n\n<i>Mini App не настроен (CLICKER_URL пустой).</i>")
        return

    is_private = msg.chat.type == "private"
    try:
        if is_private and raw:
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🎯 Запустить CS:CLICKER", web_app=WebAppInfo(url=raw)),
            ]])
            await msg.reply(text, reply_markup=kb)
            return
        # Group / channel — Direct Link Mini App via t.me URL.
        target = tme or raw
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🎯 Запустить CS:CLICKER", url=target),
        ]])
        await msg.reply(text, reply_markup=kb)
        return
    except TelegramBadRequest as e:
        log.warning("clicker button rejected (%s), fall back to text URL", e.message)

    await msg.reply(f"{text}\n\n{tme or raw}")


@router.message(Command("clicker"))
async def cmd_clicker(msg: Message) -> None:
    text = (
        "🎯 <b>CS:CLICKER</b>\n"
        "Тапай террористов, фарми скины, выноси боссов от Бабы Зины до Гейба.\n\n"
        "Жми кнопку — откроется приложение."
    )
    await _send_app_button(msg, text)


@router.message(Command("cstatus"))
async def cmd_cstatus(msg: Message) -> None:
    if not msg.from_user:
        return
    tg_id = msg.from_user.id
    try:
        await gm.ensure_user(
            tg_id,
            username=msg.from_user.username,
            first_name=msg.from_user.first_name,
            last_name=msg.from_user.last_name,
            is_premium=bool(getattr(msg.from_user, "is_premium", False)),
        )
        result = await gm.get_state(tg_id)
        if not result.get("ok"):
            await msg.reply("Пока не получилось загрузить.")
            return
        s = result["data"]["state"]
    except Exception:
        log.exception("cstatus failed")
        await msg.reply("Что-то пошло не так.")
        return

    user = s["user"]
    combat = s.get("combat") or {}
    meta = s.get("level_meta") or {}
    name = user.get("first_name") or user.get("username") or "Игрок"

    enemy_label = meta.get("enemy_name") or "враг"
    hp_now = _fmt(combat.get("enemy_hp", "0"))
    hp_max = _fmt(combat.get("enemy_max_hp", "0"))

    lines = [
        f"🎯 <b>{name}</b> · ур. {user['level']} (макс {user['max_level']}, чекпоинт {user['checkpoint']})",
        f"💵 ${_fmt(user['cash'])} · ⌬ {_fmt(user['casecoins'])} · ★ {_fmt(user['glory'])}",
        f"⚔️ Click {_fmt(user['click_damage'])} · ⏱ Auto {_fmt(user['auto_dps'])}/s · 🎯 Crit {user['crit_chance']}%",
        f"🩸 {enemy_label}: {hp_now} / {hp_max}",
    ]
    if user["prestige_count"] > 0:
        lines.append(f"⭐ Престижей: {user['prestige_count']} · слотов {user['artifact_slots']}/6")
    if user["bosses_killed"] > 0:
        lines.append(f"💀 Убито боссов: {user['bosses_killed']}")

    await _send_app_button(msg, "\n".join(lines))
