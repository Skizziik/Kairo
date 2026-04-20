from __future__ import annotations

import logging
import random

from aiogram import F, Router
from aiogram.types import Message, ReactionTypeEmoji

from app.config import get_settings

router = Router(name="reactions")
log = logging.getLogger(__name__)

# Pool of emojis the bot can react with (must be from Telegram's allowed reactions list)
REACTION_POOL = ["🔥", "💩", "👍", "😁", "🤡", "💯", "😢", "🤔", "👀", "🤝", "🫡", "🤯", "🗿"]

# When someone uses these in their message, bot has high chance to react
TRIGGER_EMOJIS = {"😂", "🤣", "💀", "🔥", "🤡", "😭", "👀", "🤯", "💩", "🫠"}


def _has_trigger_emoji(text: str) -> bool:
    return any(e in text for e in TRIGGER_EMOJIS)


async def _try_react(msg: Message) -> None:
    emoji = random.choice(REACTION_POOL)
    try:
        await msg.bot.set_message_reaction(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
    except Exception as e:
        log.debug("reaction failed: %s", e)


@router.message(F.sticker)
async def on_sticker(msg: Message) -> None:
    if msg.chat.type == "private":
        return
    s = get_settings()
    if random.random() > s.emoji_react_probability * 2:  # stickers get higher chance
        return
    await _try_react(msg)


@router.message(F.text.func(_has_trigger_emoji))
async def on_trigger_emoji(msg: Message) -> None:
    if msg.chat.type == "private":
        return
    s = get_settings()
    if random.random() > s.emoji_react_probability:
        return
    await _try_react(msg)
