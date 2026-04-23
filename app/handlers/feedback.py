from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import MessageReactionUpdated, ReactionTypeEmoji

from app.db import repos

router = Router(name="feedback")
log = logging.getLogger(__name__)


@router.message_reaction()
async def on_reaction(event: MessageReactionUpdated) -> None:
    if event.user is None:
        return
    new = event.new_reaction or []
    if not new:
        return
    first = new[0]
    if not isinstance(first, ReactionTypeEmoji):
        return
    emoji = first.emoji
    try:
        recorded = await repos.record_reaction(
            chat_id=event.chat.id,
            message_id=event.message_id,
            emoji=emoji,
            user_id=event.user.id,
        )
    except Exception:
        log.exception("record reaction failed")
        return
    if recorded:
        log.info("feedback captured: chat=%s msg=%s %s by %s",
                 event.chat.id, event.message_id, emoji, event.user.id)
