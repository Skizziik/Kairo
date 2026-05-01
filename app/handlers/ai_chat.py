from __future__ import annotations

import logging
import random
import re

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.ai import inside_jokes
from app.ai.memory import answer_as_rip
from app.bot import get_bot
from app.config import get_settings
from app.db import repos

router = Router(name="ai_chat")
log = logging.getLogger(__name__)

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
    # answer == None means active persona (e.g. ОБИДЕЛСЯ) chose silence.
    # Скипаем reply вообще — это часть UX динамической личности.
    if answer is None:
        log.info("bot chose silence in chat=%s", msg.chat.id)
        return
    sent = await msg.reply(answer or "...")
    # Log bot's own message for self-learning feedback tracking
    try:
        await repos.log_bot_message(sent.chat.id, sent.message_id, answer or "")
    except Exception:
        log.exception("failed to log bot message")


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


@router.message(F.text | F.caption)
async def on_text(msg: Message) -> None:
    if msg.from_user is None or msg.from_user.is_bot:
        return
    text = msg.text or msg.caption or ""
    if text.startswith("/"):
        return

    # Track inside-jokes / repeat phrases — fires on каждое обычное сообщение
    # (не команда). Помогает боту запомнить мемы чата для будущего инжекта.
    try:
        await inside_jokes.track_message(msg.chat.id, msg.from_user.id, text)
    except Exception:
        log.exception("inside_jokes.track_message failed")
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
        # Phase 4: per-user cooldown — 3 trigger'а за 5 мин max от одного юзера
        try:
            allow = await repos.can_user_trigger_and_mark(
                msg.chat.id, msg.from_user.id, cooldown_seconds=300, max_in_window=3,
            )
            if not allow:
                log.info("per-user rate-limit hit chat=%s user=%s",
                         msg.chat.id, msg.from_user.id)
                return
        except Exception:
            log.exception("per-user cooldown check failed")
        await _ask(msg, question)
        return

    # 2) Random chime-in (group only, not in private DMs)
    if msg.chat.type == "private":
        return

    s = get_settings()
    if len(text.split()) < s.chime_in_min_words:
        return
    # Questions get 2× chime probability — people asking stuff wants more response
    probability = s.chime_in_probability
    if "?" in text:
        probability = min(1.0, probability * 2)
    # Phase 4: smart chime-in — учитываем sentiment чата.
    # Если чат в "серьёзном режиме" (длинные сообщения) — реже вклиниваемся.
    # Если в "режиме базара" (короткие) — чаще.
    try:
        recent = await repos.recent_messages(msg.chat.id, 5)
        non_bot = [m for m in recent if not m.is_bot and m.text]
        if non_bot:
            avg_len = sum(len(m.text) for m in non_bot) / len(non_bot)
            if avg_len > 150:
                probability *= 0.4   # серьёзный разговор — почти не лезем
            elif avg_len < 30:
                probability = min(1.0, probability * 1.5)   # базар — лезем чаще
    except Exception:
        log.exception("smart chime-in sentiment check failed")
    if random.random() > probability:
        return
    # Atomic cooldown check — marks last_chime_at if allowed. Persisted in DB.
    allowed = await repos.can_chime_and_mark(msg.chat.id, s.chime_in_cooldown_seconds)
    if not allowed:
        return

    log.info("chime-in triggered in chat=%s len=%d", msg.chat.id, len(text))
    await _ask(msg, text)
