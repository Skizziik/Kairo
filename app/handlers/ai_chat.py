from __future__ import annotations

import logging
import random
import re
import time

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.ai.memory import answer_as_rip
from app.bot import get_bot
from app.config import get_settings

router = Router(name="ai_chat")
log = logging.getLogger(__name__)

_last_chime_by_chat: dict[int, float] = {}

# Match "нагибатор", "рип нагибатор", "kairo", "кайро" as standalone words
NAME_TRIGGER = re.compile(
    r"(?:^|[\s,.!?;:])(?:рип\s*)?нагибатор(?:е|ом|ов|а|у|ы)?(?:[\s,.!?;:]|$)|"
    r"(?:^|[\s,.!?;:])(?:kairo|кайро)(?:[\s,.!?;:]|$)",
    re.IGNORECASE,
)

# Direct-address phrases — when someone clearly wants the bot's opinion
ADDRESS_HOOKS = re.compile(
    r"(?:как\s+думаеш[ьл]|что\s+скажеш[ьл]|тво[её]\s+мнени[ея]|согласен\??|"
    r"а\s+ты\s*\??|бот[,!]?\s+скажи|что\s+думаеш[ьл]|\bтипа\s+мнени[ея]\b|"
    r"прокомментируй|а\s+что\s+думаеш[ьл])",
    re.IGNORECASE,
)


def _display(msg: Message) -> str:
    u = msg.from_user
    if u is None:
        return "anon"
    if u.username:
        return f"@{u.username}"
    return u.first_name or f"user{u.id}"


async def _ask(msg: Message, question: str) -> None:
    if not question:
        await msg.reply("Ну ты это, вопрос-то напиши. Или реплайни на меня с вопросом.")
        return
    try:
        await msg.bot.send_chat_action(msg.chat.id, "typing")
        answer = await answer_as_rip(
            chat_id=msg.chat.id,
            asker_id=msg.from_user.id,
            asker_display=_display(msg),
            question=question,
        )
    except Exception:
        log.exception("ai answer failed")
        await msg.reply("Щас не могу, мозги лагают. Попробуй через минуту.")
        return
    await msg.reply(answer or "...")


@router.message(Command("ai"))
async def cmd_ai(msg: Message, command: CommandObject) -> None:
    question = (command.args or "").strip()
    if not question and msg.reply_to_message:
        question = msg.reply_to_message.text or msg.reply_to_message.caption or ""
    await _ask(msg, question)


@router.message(F.reply_to_message.from_user.is_bot == True, F.text)
async def reply_to_bot(msg: Message) -> None:
    # Only engage if the reply is actually to US
    bot = get_bot()
    if msg.reply_to_message is None or msg.reply_to_message.from_user is None:
        return
    if msg.reply_to_message.from_user.id != bot.id:
        return
    text = (msg.text or "").strip()
    if not text:
        return
    await _ask(msg, text)


async def _extract_mention_question(msg: Message) -> str | None:
    """Return question text if message addresses the bot via @mention or name
    trigger, otherwise None."""
    text = msg.text or msg.caption
    if not text:
        return None
    bot = get_bot()
    me = await bot.me()
    bot_username = (me.username or "").lower()

    # Strip @mention of the bot if present
    cleaned = text
    if bot_username and msg.entities:
        for entity in msg.entities:
            if entity.type == "mention":
                m_text = text[entity.offset : entity.offset + entity.length]
                if m_text.lower() == f"@{bot_username}":
                    cleaned = (text[: entity.offset] + text[entity.offset + entity.length :]).strip()
                    return cleaned or "чё как"

    # Name trigger — bot called by name somewhere in the sentence
    if NAME_TRIGGER.search(text):
        return text.strip()

    # Direct-address hook phrases — "как думаешь", "что скажешь", "а ты?" etc.
    if ADDRESS_HOOKS.search(text):
        return text.strip()

    return None


def _can_chime_in(chat_id: int, cooldown_seconds: int) -> bool:
    now = time.time()
    last = _last_chime_by_chat.get(chat_id, 0.0)
    return now - last >= cooldown_seconds


def _mark_chimed_in(chat_id: int) -> None:
    _last_chime_by_chat[chat_id] = time.time()


@router.message(F.text | F.caption)
async def on_text(msg: Message) -> None:
    if msg.from_user is None or msg.from_user.is_bot:
        return
    text = msg.text or msg.caption or ""
    if text.startswith("/"):
        return
    # Ignore replies to the bot — handled above
    if (
        msg.reply_to_message is not None
        and msg.reply_to_message.from_user is not None
        and msg.reply_to_message.from_user.is_bot
    ):
        return

    # 1) Direct address via @mention or name trigger — always respond
    question = await _extract_mention_question(msg)
    if question is not None:
        await _ask(msg, question)
        return

    # 2) Random chime-in (group only, not in private DMs)
    if msg.chat.type == "private":
        return

    s = get_settings()
    if len(text.split()) < s.chime_in_min_words:
        return
    if not _can_chime_in(msg.chat.id, s.chime_in_cooldown_seconds):
        return
    # Questions get 2× chime probability — people asking stuff wants more response
    probability = s.chime_in_probability
    if "?" in text:
        probability = min(1.0, probability * 2)
    if random.random() > probability:
        return

    _mark_chimed_in(msg.chat.id)
    log.info("chime-in triggered in chat=%s len=%d", msg.chat.id, len(text))
    await _ask(msg, text)
