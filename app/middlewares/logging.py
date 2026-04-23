from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message

from app.ai.memory import extract_and_store
from app.config import get_settings
from app.db import repos
from app.economy import repo as eco

log = logging.getLogger(__name__)


class MessageLogMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        s = get_settings()
        if event.from_user is None:
            return await handler(event, data)

        # Hard-scope bot to one chat if configured (dm still always allowed)
        if (
            s.tg_allowed_chat_id is not None
            and event.chat.type != "private"
            and event.chat.id != s.tg_allowed_chat_id
        ):
            return  # silently ignore foreign groups

        try:
            await repos.upsert_user(
                tg_id=event.from_user.id,
                username=event.from_user.username,
                first_name=event.from_user.first_name,
                last_name=event.from_user.last_name,
            )
            text = event.text or event.caption or ""
            if text and not event.from_user.is_bot:
                await repos.log_message(
                    chat_id=event.chat.id,
                    tg_user_id=event.from_user.id,
                    text=text,
                    reply_to=event.reply_to_message.message_id if event.reply_to_message else None,
                    is_bot=False,
                )
                if event.chat.type != "private":
                    count = await repos.bump_extract_counter(1)
                    if count >= s.memory_extract_every:
                        await repos.reset_extract_counter()
                        asyncio.create_task(_run_extract_safe(event.chat.id))
                    # Activity reward: give 1 coin every ~10 messages, capped per day.
                    if count % 10 == 0:
                        try:
                            await eco.grant_activity_coin(event.from_user.id)
                        except Exception:
                            log.exception("activity coin grant failed")
        except Exception:
            log.exception("message log middleware failed")

        return await handler(event, data)


async def _run_extract_safe(chat_id: int) -> None:
    try:
        await extract_and_store(chat_id)
    except Exception:
        log.exception("extract_and_store failed")
