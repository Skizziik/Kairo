from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from html import escape

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import ChatPermissions, Message

from app.config import get_settings
from app.db import repos
from app.db.client import pool

router = Router(name="moderation")
log = logging.getLogger(__name__)

DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhdw])?", re.IGNORECASE)
UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
AUTOMUTE_WARN_THRESHOLD = 3


def _parse_duration(arg: str) -> tuple[int | None, str]:
    """Return (seconds, remainder). Defaults: 1h for mute/ban if omitted."""
    m = DURATION_RE.match(arg or "")
    if not m:
        return None, (arg or "").strip()
    num = int(m.group(1))
    unit = (m.group(2) or "m").lower()
    seconds = num * UNIT_SECONDS.get(unit, 60)
    remainder = (arg[m.end():] or "").strip()
    return seconds, remainder


def _is_admin(user_id: int) -> bool:
    s = get_settings()
    return user_id in s.admin_id_set


async def _target_from_reply(msg: Message) -> tuple[int, str] | None:
    if msg.reply_to_message is None or msg.reply_to_message.from_user is None:
        return None
    u = msg.reply_to_message.from_user
    if u.is_bot:
        return None
    display = f"@{u.username}" if u.username else (u.first_name or f"user{u.id}")
    return u.id, display


def _admin_only(f):
    async def wrapper(msg: Message, *args, **kwargs):
        if msg.from_user is None or not _is_admin(msg.from_user.id):
            await msg.reply("Эта команда только для админов.")
            return
        if msg.chat.type == "private":
            await msg.reply("Модерация работает только в группе.")
            return
        return await f(msg, *args, **kwargs)
    return wrapper


@router.message(Command("warn"))
@_admin_only
async def cmd_warn(msg: Message, command: CommandObject) -> None:
    target = await _target_from_reply(msg)
    if target is None:
        await msg.reply("Реплайни на сообщение того кого варнишь: <code>/warn причина</code>")
        return
    tg_id, display = target
    reason = (command.args or "").strip() or "без причины"
    count = await repos.add_warn(tg_id, msg.chat.id, reason, issued_by=msg.from_user.id)
    text = f"⚠️ {escape(display)} получил варн ({count}/{AUTOMUTE_WARN_THRESHOLD}): {escape(reason)}"

    if count >= AUTOMUTE_WARN_THRESHOLD:
        until = datetime.now(timezone.utc) + timedelta(hours=1)
        try:
            await msg.bot.restrict_chat_member(
                chat_id=msg.chat.id,
                user_id=tg_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            text += f"\n🔇 Авто-мут на 1 час (лимит варнов)."
        except Exception as e:
            log.exception("automute failed")
            text += f"\n(авто-мут не сработал: {e})"
    await msg.reply(text)


@router.message(Command("mute"))
@_admin_only
async def cmd_mute(msg: Message, command: CommandObject) -> None:
    target = await _target_from_reply(msg)
    if target is None:
        await msg.reply("Реплайни на сообщение: <code>/mute 30m причина</code>")
        return
    tg_id, display = target
    seconds, reason = _parse_duration(command.args or "")
    if seconds is None:
        seconds = 3600
    until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    try:
        await msg.bot.restrict_chat_member(
            chat_id=msg.chat.id,
            user_id=tg_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until,
        )
    except Exception as e:
        log.exception("mute failed")
        await msg.reply(f"Не смог замутить: {e}")
        return
    human = _human_duration(seconds)
    tail = f" — {escape(reason)}" if reason else ""
    await msg.reply(f"🔇 {escape(display)} замучен на {human}{tail}")


@router.message(Command("unmute"))
@_admin_only
async def cmd_unmute(msg: Message) -> None:
    target = await _target_from_reply(msg)
    if target is None:
        await msg.reply("Реплайни на сообщение того кого размутить.")
        return
    tg_id, display = target
    try:
        await msg.bot.restrict_chat_member(
            chat_id=msg.chat.id,
            user_id=tg_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )
    except Exception as e:
        log.exception("unmute failed")
        await msg.reply(f"Не смог размутить: {e}")
        return
    await msg.reply(f"🔊 {escape(display)} размучен.")


@router.message(Command("ban"))
@_admin_only
async def cmd_ban(msg: Message, command: CommandObject) -> None:
    target = await _target_from_reply(msg)
    if target is None:
        await msg.reply("Реплайни на сообщение того кого банишь: <code>/ban причина</code>")
        return
    tg_id, display = target
    reason = (command.args or "").strip() or "без причины"
    try:
        await msg.bot.ban_chat_member(chat_id=msg.chat.id, user_id=tg_id)
    except Exception as e:
        log.exception("ban failed")
        await msg.reply(f"Не смог забанить: {e}")
        return
    await msg.reply(f"🚫 {escape(display)} забанен. Причина: {escape(reason)}")


@router.message(Command("unban"))
@_admin_only
async def cmd_unban(msg: Message, command: CommandObject) -> None:
    tg_id: int | None = None
    display = ""
    target = await _target_from_reply(msg)
    if target is not None:
        tg_id, display = target
    else:
        arg = (command.args or "").strip()
        if arg.isdigit():
            tg_id = int(arg)
            display = f"user{tg_id}"
        elif arg.startswith("@"):
            found = await repos.find_user_by_username(arg)
            if found:
                tg_id = found
                display = arg
    if tg_id is None:
        await msg.reply("Как юзать: реплайни или <code>/unban @ник</code> или <code>/unban 12345</code>")
        return
    try:
        await msg.bot.unban_chat_member(chat_id=msg.chat.id, user_id=tg_id, only_if_banned=True)
    except Exception as e:
        log.exception("unban failed")
        await msg.reply(f"Не смог разбанить: {e}")
        return
    await msg.reply(f"✅ {escape(display)} разбанен.")


@router.message(Command("warns"))
@_admin_only
async def cmd_warns(msg: Message) -> None:
    target = await _target_from_reply(msg)
    if target is None:
        await msg.reply("Реплайни на сообщение чтобы глянуть варны.")
        return
    tg_id, display = target
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "select reason, issued_at from warns where tg_user_id = $1 and chat_id = $2 "
            "order by issued_at desc limit 20",
            tg_id, msg.chat.id,
        )
    if not rows:
        await msg.reply(f"{escape(display)} чистый, варнов нет.")
        return
    lines = [f"<b>Варны {escape(display)} ({len(rows)}):</b>"]
    for r in rows:
        lines.append(f"• {r['issued_at'].strftime('%Y-%m-%d')} — {escape(r['reason'] or '—')}")
    await msg.reply("\n".join(lines))


def _human_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} сек"
    if seconds < 3600:
        return f"{seconds // 60} мин"
    if seconds < 86400:
        return f"{seconds // 3600} ч"
    return f"{seconds // 86400} дн"
