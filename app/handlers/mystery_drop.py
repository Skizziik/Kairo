from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.db import repos as base_repos
from app.economy import chat_events
from app.economy import repo as eco

router = Router(name="mystery_drop")
log = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("claim_drop:"))
async def on_claim(q: CallbackQuery) -> None:
    if q.data is None or q.from_user is None:
        await q.answer()
        return
    try:
        event_id = int(q.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await q.answer("Битая кнопка", show_alert=False)
        return
    await base_repos.upsert_user(
        tg_id=q.from_user.id,
        username=q.from_user.username,
        first_name=q.from_user.first_name,
        last_name=q.from_user.last_name,
    )
    await eco.ensure_user(q.from_user.id)
    result = await chat_events.claim_mystery_drop(q.from_user.id, event_id)
    if not result["ok"]:
        await q.answer(result.get("error", "Нельзя"), show_alert=False)
        return
    nick = f"@{q.from_user.username}" if q.from_user.username else (q.from_user.first_name or "кто-то")
    await q.answer("Забрал! 🎉", show_alert=False)
    try:
        if q.message is not None:
            await q.message.edit_caption(
                caption=f"{q.message.caption}\n\n✅ <b>Забрал {nick}</b>",
                reply_markup=None,
            )
    except Exception:
        log.exception("edit caption failed")
