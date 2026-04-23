from __future__ import annotations

import logging

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

# Mistral Voxtral — OpenAI-compatible transcription endpoint.
# Uses the same MISTRAL_API_KEY as embeddings/vision.
MISTRAL_TRANSCRIBE_URL = "https://api.mistral.ai/v1/audio/transcriptions"
MISTRAL_STT_MODEL = "voxtral-mini-latest"


async def transcribe_audio(audio_bytes: bytes, filename: str = "voice.ogg") -> str | None:
    """Transcribe audio via Mistral Voxtral. Returns plain text or None if unavailable/failed."""
    s = get_settings()
    if not s.mistral_api_key:
        log.info("MISTRAL_API_KEY not set; voice transcription disabled")
        return None
    content_type = _guess_mime(filename)
    files = {
        "file": (filename, audio_bytes, content_type),
        "model": (None, MISTRAL_STT_MODEL),
        "language": (None, "ru"),
        "response_format": (None, "text"),
    }
    async with httpx.AsyncClient(timeout=60.0) as c:
        try:
            r = await c.post(
                MISTRAL_TRANSCRIBE_URL,
                headers={"Authorization": f"Bearer {s.mistral_api_key}"},
                files=files,
            )
        except httpx.HTTPError:
            log.exception("mistral transcribe network error")
            return None
    if r.status_code >= 400:
        log.warning("mistral transcribe http %s: %s", r.status_code, r.text[:300])
        return None

    # Voxtral returns JSON with a text field, or plain text depending on response_format.
    body = r.text.strip()
    if not body:
        return None
    if body.startswith("{"):
        try:
            data = r.json()
            return (data.get("text") or "").strip() or None
        except Exception:
            pass
    return body


def _guess_mime(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "ogg": "audio/ogg",
        "oga": "audio/ogg",
        "opus": "audio/ogg",
        "mp3": "audio/mpeg",
        "m4a": "audio/mp4",
        "mp4": "audio/mp4",
        "wav": "audio/wav",
        "webm": "audio/webm",
    }.get(ext, "audio/ogg")
