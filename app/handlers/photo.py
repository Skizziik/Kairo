from __future__ import annotations

import logging
from io import BytesIO

from aiogram import F, Router
from aiogram.types import Message

from app.config import get_settings
from app.db import repos
from app.services.vision import describe_image

router = Router(name="photo")
log = logging.getLogger(__name__)


async def _download(msg: Message, file_id: str) -> bytes | None:
    try:
        f = await msg.bot.get_file(file_id)
        buf = BytesIO()
        await msg.bot.download_file(f.file_path, destination=buf)
        return buf.getvalue()
    except Exception:
        log.exception("photo download failed")
        return None


@router.message(F.photo)
async def on_photo(msg: Message) -> None:
    s = get_settings()
    if s.tg_allowed_chat_id is not None and msg.chat.type != "private" and msg.chat.id != s.tg_allowed_chat_id:
        return
    if msg.from_user is None or msg.from_user.is_bot:
        return
    if not s.mistral_api_key:
        return
    if not msg.photo:
        return

    # Pick largest thumbnail
    photo = msg.photo[-1]
    image = await _download(msg, photo.file_id)
    if image is None:
        return
    # Cap payload at ~2 MB to avoid slow LLM calls
    if len(image) > 2_000_000:
        log.info("photo too big (%d bytes), skipping vision", len(image))
        return

    caption = (msg.caption or "").strip() or None
    description = await describe_image(image, hint=caption)
    if not description:
        return

    # Log as chat message so bot can react to it naturally later
    text = f"[картинка] {description}"
    if caption:
        text = f"[картинка с подписью «{caption}»] {description}"
    try:
        await repos.log_message(
            chat_id=msg.chat.id,
            tg_user_id=msg.from_user.id,
            text=text,
            reply_to=msg.reply_to_message.message_id if msg.reply_to_message else None,
            is_bot=False,
        )
    except Exception:
        log.exception("photo log_message failed")
    log.info("photo described chat=%s (%d chars)", msg.chat.id, len(description))
