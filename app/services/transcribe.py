from __future__ import annotations

import logging

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3-turbo"


async def transcribe_audio(audio_bytes: bytes, filename: str = "voice.ogg") -> str | None:
    """Transcribe audio via Groq Whisper. Returns plain text or None if unavailable/failed."""
    s = get_settings()
    if not s.groq_api_key:
        log.info("GROQ_API_KEY not set; voice transcription disabled")
        return None
    files = {
        "file": (filename, audio_bytes, "audio/ogg"),
        "model": (None, GROQ_MODEL),
        "response_format": (None, "text"),
        "language": (None, "ru"),
    }
    async with httpx.AsyncClient(timeout=60.0) as c:
        try:
            r = await c.post(
                GROQ_TRANSCRIBE_URL,
                headers={"Authorization": f"Bearer {s.groq_api_key}"},
                files=files,
            )
        except httpx.HTTPError:
            log.exception("groq transcribe network error")
            return None
    if r.status_code >= 400:
        log.warning("groq transcribe http %s: %s", r.status_code, r.text[:200])
        return None
    text = (r.text or "").strip()
    if not text:
        return None
    return text
