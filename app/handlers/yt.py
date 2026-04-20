from __future__ import annotations

import logging
import os
from html import escape

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import FSInputFile, Message

from app.services import youtube

router = Router(name="yt")
log = logging.getLogger(__name__)


@router.message(Command("yt"))
async def cmd_yt(msg: Message, command: CommandObject) -> None:
    url = (command.args or "").strip()
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        await msg.reply("Дай ссылку: /yt &lt;url&gt;")
        return
    await msg.reply("Качаю, погодь...")
    await msg.bot.send_chat_action(msg.chat.id, "upload_document")
    track = await youtube.download_audio(url)
    if track is None:
        await msg.reply("Не смог скачать. Проверь ссылку или трек слишком длинный/большой.")
        return
    caption = f"<b>{escape(track.title)}</b>"
    if track.uploader:
        caption += f"\n<i>{escape(track.uploader)}</i>"
    try:
        await msg.reply_audio(
            FSInputFile(track.path),
            caption=caption,
            title=track.title,
            performer=track.uploader or None,
            duration=track.duration or None,
        )
    finally:
        try:
            os.remove(track.path)
            os.rmdir(os.path.dirname(track.path))
        except OSError:
            pass
