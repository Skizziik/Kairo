from __future__ import annotations

import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

router = Router(name="lfg")
log = logging.getLogger(__name__)

# message_id -> {"initiator_id": int, "initiator_name": str, "note": str, "in": {uid: name}, "out": {uid: name}}
_sessions: dict[tuple[int, int], dict] = {}
MAX_SLOTS = 5


def _kb(chat_id: int, message_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Я в деле", callback_data=f"lfg:join:{chat_id}:{message_id}"),
        InlineKeyboardButton(text="❌ Пас", callback_data=f"lfg:skip:{chat_id}:{message_id}"),
    ]])


def _render(sess: dict) -> str:
    initiator = sess["initiator_name"]
    note = sess["note"]
    lines = [f"<b>LFG</b> от {escape(initiator)}"]
    if note:
        lines.append(escape(note))
    going = list(sess["in"].values())
    lines.append("")
    lines.append(f"В деле ({len(going)}/{MAX_SLOTS}):")
    if going:
        lines.extend(f"  • {escape(n)}" for n in going)
    else:
        lines.append("  <i>пока никого</i>")
    if sess["out"]:
        lines.append("")
        lines.append(f"Пас ({len(sess['out'])}): " + ", ".join(escape(n) for n in sess["out"].values()))
    if len(going) >= MAX_SLOTS:
        lines.append("")
        lines.append("🔥 <b>Пятёрка собрана, го</b>")
    return "\n".join(lines)


@router.message(Command("lfg"))
async def cmd_lfg(msg: Message, command: CommandObject) -> None:
    if msg.from_user is None:
        return
    initiator_name = f"@{msg.from_user.username}" if msg.from_user.username else (msg.from_user.first_name or "anon")
    note = (command.args or "").strip()
    sess = {
        "initiator_id": msg.from_user.id,
        "initiator_name": initiator_name,
        "note": note,
        "in": {msg.from_user.id: initiator_name},
        "out": {},
    }
    # Send placeholder then patch with real message_id so callback_data can reference it
    placeholder = await msg.answer("LFG...")
    key = (placeholder.chat.id, placeholder.message_id)
    _sessions[key] = sess
    await placeholder.edit_text(_render(sess), reply_markup=_kb(*key))


@router.callback_query(F.data.startswith("lfg:"))
async def on_lfg_click(q: CallbackQuery) -> None:
    if q.data is None or q.from_user is None or q.message is None:
        await q.answer()
        return
    try:
        _, action, chat_id_s, msg_id_s = q.data.split(":")
        key = (int(chat_id_s), int(msg_id_s))
    except ValueError:
        await q.answer("корявая кнопка")
        return
    sess = _sessions.get(key)
    if sess is None:
        await q.answer("Сессия протухла", show_alert=False)
        return

    uid = q.from_user.id
    name = f"@{q.from_user.username}" if q.from_user.username else (q.from_user.first_name or "anon")

    if action == "join":
        sess["out"].pop(uid, None)
        if uid not in sess["in"]:
            if len(sess["in"]) >= MAX_SLOTS:
                await q.answer("Пятёрка уже забита", show_alert=False)
                return
            sess["in"][uid] = name
            await q.answer("Записал")
        else:
            await q.answer("Уже в списке")
    elif action == "skip":
        sess["in"].pop(uid, None)
        sess["out"][uid] = name
        await q.answer("Окей, пропускаешь")
    else:
        await q.answer()
        return

    try:
        await q.message.edit_text(_render(sess), reply_markup=_kb(*key))
    except Exception:
        log.exception("lfg edit failed")
