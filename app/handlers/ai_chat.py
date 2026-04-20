from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.ai.memory import answer_as_rip
from app.bot import get_bot

router = Router(name="ai_chat")
log = logging.getLogger(__name__)


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
