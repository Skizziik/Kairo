from __future__ import annotations

import logging
from io import BytesIO

from aiogram import F, Router
from aiogram.types import Message

from app.config import get_settings
from app.db import repos
from app.services.transcribe import transcribe_audio

router = Router(name="voice")
log = logging.getLogger(__name__)


async def _download(msg: Message, file_id: str) -> bytes | None:
    try:
        f = await msg.bot.get_file(file_id)
        buf = BytesIO()
        await msg.bot.download_file(f.file_path, destination=buf)
        return buf.getvalue()
    except Exception:
        log.exception("voice download failed")
        return None


@router.message(F.voice | F.video_note | F.audio)
async def on_voice(msg: Message) -> None:
    s = get_settings()
    if s.tg_allowed_chat_id is not None and msg.chat.type != "private" and msg.chat.id != s.tg_allowed_chat_id:
        return
    if msg.from_user is None or msg.from_user.is_bot:
        return

    file_id = None
    suffix = "ogg"
    if msg.voice:
        file_id = msg.voice.file_id
        duration = msg.voice.duration
    elif msg.video_note:
        file_id = msg.video_note.file_id
        duration = msg.video_note.duration
        suffix = "mp4"
    elif msg.audio:
        file_id = msg.audio.file_id
        duration = msg.audio.duration or 0
        suffix = "mp3"
    if file_id is None:
        return

    # Cap transcription at 3 min to avoid token overshoot
    if duration and duration > 180:
        log.info("voice too long (%ss), skipping transcription", duration)
        return
    if not s.groq_api_key:
        return

    audio = await _download(msg, file_id)
    if audio is None or len(audio) < 500:
        return

    text = await transcribe_audio(audio, filename=f"voice.{suffix}")
    if not text:
        return

    # Log as if it was a text message so chime-in / extractor can react to it
    try:
        await repos.log_message(
            chat_id=msg.chat.id,
            tg_user_id=msg.from_user.id,
            text=f"[войс] {text}",
            reply_to=msg.reply_to_message.message_id if msg.reply_to_message else None,
            is_bot=False,
        )
    except Exception:
        log.exception("voice log_message failed")
    log.info("voice transcribed in chat=%s by %s (%d chars)", msg.chat.id, msg.from_user.id, len(text))
