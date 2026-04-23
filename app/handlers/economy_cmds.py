"""Text-based economy commands: /balance /daily /casino /seed_economy (admin)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from aiogram.exceptions import TelegramBadRequest

from app.config import get_settings
from app.economy import repo as eco
from app.economy.pricing import rarity_emoji, wear_short
from app.scripts.seed_economy import run_seed

router = Router(name="economy_cmds")
log = logging.getLogger(__name__)


def _webapp_kb() -> InlineKeyboardMarkup | None:
    """WebApp button — works only in private chats without BotFather /setdomain."""
    s = get_settings()
    url = s.miniapp_url
    if not url:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎰 Открыть казино", web_app=WebAppInfo(url=url)),
    ]])


def _url_kb() -> InlineKeyboardMarkup | None:
    """URL button — works everywhere. Opens in Telegram WebView if domain is
    registered via BotFather /newapp, otherwise in browser."""
    s = get_settings()
    url = s.miniapp_url
    if not url:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎰 Открыть казино", url=url),
    ]])


async def _send_with_miniapp(msg: Message, text: str, prefer_reply: bool = True) -> None:
    """Strategy:
    - Private chat: use web_app button with the raw Mini App URL (inline).
    - Group chat: use url button with t.me Direct Link Mini App URL (inline).
      If not set, fall back to raw URL (opens browser).
    - Any TelegramBadRequest falls through to pure text with URL appended.
    """
    s = get_settings()
    is_private = msg.chat.type == "private"
    send = msg.reply if prefer_reply else msg.answer

    raw_url = s.miniapp_url
    tme_url = s.miniapp_tme_url

    if not raw_url and not tme_url:
        await send(text)
        return

    # Pick best button type per context.
    try:
        if is_private and raw_url:
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🎰 Открыть казино", web_app=WebAppInfo(url=raw_url)),
            ]])
            await send(text, reply_markup=kb)
            return
        # Group or channel — must use url button.
        # Prefer t.me Direct Link (opens inline), fall back to raw URL (opens browser).
        target = tme_url or raw_url
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🎰 Открыть казино", url=target),
        ]])
        await send(text, reply_markup=kb)
        return
    except TelegramBadRequest as e:
        log.warning("miniapp button rejected (%s), fall back to text URL", e.message)

    # Last resort — plain text with URL
    await send(f"{text}\n\n{tme_url or raw_url}")


def _fmt_coins(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _fmt_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} сек"
    if seconds < 3600:
        return f"{seconds // 60} мин"
    return f"{seconds // 3600} ч {(seconds % 3600) // 60} мин"


@router.message(Command("balance", "bal", "баланс"))
async def cmd_balance(msg: Message) -> None:
    if msg.from_user is None:
        return
    await eco.ensure_user(msg.from_user.id)
    user = await eco.get_user(msg.from_user.id)
    if user is None:
        await msg.reply("Не шмог загрузить твой профиль.")
        return
    nick = f"@{msg.from_user.username}" if msg.from_user.username else (msg.from_user.first_name or "пацан")
    text = (
        f"💰 <b>Баланс {escape(nick)}</b>\n\n"
        f"Коинов: <b>{_fmt_coins(int(user['balance']))}</b>\n"
        f"Заработано всего: {_fmt_coins(int(user['total_earned']))}\n"
        f"Потрачено всего: {_fmt_coins(int(user['total_spent']))}\n"
        f"Кейсов открыто: <b>{user['cases_opened']}</b>\n"
        f"Текущий стрик: <b>{user['current_streak']}</b> 🔥 (лучший: {user['best_streak']})"
    )
    await _send_with_miniapp(msg, text)


@router.message(Command("daily", "дейли"))
async def cmd_daily(msg: Message) -> None:
    if msg.from_user is None:
        return
    result = await eco.try_claim_daily(msg.from_user.id)
    if not result["ok"]:
        wait = result.get("next_in_seconds", 0)
        await msg.reply(f"Сегодня уже забирал. Ещё через <b>{_fmt_duration(wait)}</b> можно.")
        return
    new_bal = result.get("new_balance", 0)
    text = (
        f"✅ +{_fmt_coins(result['amount'])} коинов на счёт\n"
        f"Стрик: <b>{result['streak']}</b> 🔥\n"
        f"Баланс: <b>{_fmt_coins(new_bal)}</b>"
    )
    await _send_with_miniapp(msg, text)


@router.message(Command("casino", "казино"))
async def cmd_casino(msg: Message) -> None:
    s = get_settings()
    if not s.miniapp_url:
        await msg.reply(
            "Mini App ещё не подключён (нет переменной MINIAPP_URL). "
            "Админ должен задеплоить фронт и прописать URL в Render env."
        )
        return
    text = "🎰 <b>RIP Казино</b>\n\nЖми кнопку снизу — откроется полноценное приложение."
    await _send_with_miniapp(msg, text, prefer_reply=False)


@router.message(Command("inv_text", "inv_t"))
async def cmd_inv_text(msg: Message) -> None:
    """Text-mode fallback inventory (for when Mini App is unavailable)."""
    if msg.from_user is None:
        return
    items = await eco.inventory_of(msg.from_user.id, limit=20)
    if not items:
        await msg.reply("Инвентарь пуст. Забери /daily и открой кейс в /casino.")
        return
    lines = [f"<b>Твой инвентарь</b> (топ 20):"]
    for it in items:
        st = "ST™ " if it["stat_trak"] else ""
        lines.append(
            f"{rarity_emoji(it['rarity'])} {st}{escape(it['full_name'])} "
            f"({wear_short(it['wear'])} {it['float_value']:.3f}) — "
            f"<b>{_fmt_coins(int(it['price']))}</b>"
        )
    await msg.reply("\n".join(lines))


@router.message(Command("top_rich", "top_bal"))
async def cmd_top_rich(msg: Message) -> None:
    top = await eco.leaderboard_rich(limit=10)
    if not top:
        await msg.reply("Пока пусто.")
        return
    medals = ["🥇", "🥈", "🥉"] + ["  "] * 7
    lines = ["<b>💎 Топ богатых</b>"]
    for r, medal in zip(top, medals):
        name = r["username"] or r["first_name"] or f"user{r['tg_id']}"
        lines.append(f"{medal} {escape(str(name))} — {_fmt_coins(int(r['balance']))} коинов")
    await msg.reply("\n".join(lines))


@router.message(Command("seed_economy"))
async def cmd_seed_economy(msg: Message) -> None:
    if msg.from_user is None:
        return
    s = get_settings()
    if msg.from_user.id not in s.admin_id_set:
        await msg.reply("Только для админов.")
        return
    await msg.reply("Загружаю каталог скинов и кейсы из CSGO-API... (это займёт ~30-60 сек)")
    try:
        result = await run_seed(force=False)
    except Exception as e:
        log.exception("seed failed")
        await msg.reply(f"Сид упал: <code>{escape(str(e))}</code>")
        return
    await msg.reply(
        f"Результат: <code>{result['status']}</code>\n"
        f"Скинов в каталоге: <b>{result.get('catalog_size', '?')}</b>\n"
        f"Кейсов: <b>{result.get('cases', '?')}</b>"
    )
