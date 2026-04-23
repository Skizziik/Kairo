from __future__ import annotations

import base64
import logging

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

MISTRAL_CHAT_URL = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_VISION_MODEL = "pixtral-12b-2409"


async def describe_image(image_bytes: bytes, hint: str | None = None) -> str | None:
    """Ask Mistral pixtral what's in the image. Return short description in Russian."""
    s = get_settings()
    if not s.mistral_api_key:
        return None
    b64 = base64.b64encode(image_bytes).decode("ascii")
    user_text = (
        "Опиши что изображено на картинке в 1-2 коротких предложениях, на русском. "
        "Если это скрин из CS2 / игры — назови карту, позицию, ситуацию если узнаёшь. "
        "Если мем — объясни шутку коротко. Если селфи/фото — опиши кратко. "
        "Только описание, без преамбулы."
    )
    if hint:
        user_text += f"\nПодпись пользователя: {hint}"
    payload = {
        "model": MISTRAL_VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": f"data:image/jpeg;base64,{b64}"},
                ],
            }
        ],
        "temperature": 0.3,
        "max_tokens": 200,
    }
    async with httpx.AsyncClient(timeout=30.0) as c:
        try:
            r = await c.post(
                MISTRAL_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {s.mistral_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except httpx.HTTPError:
            log.exception("pixtral network error")
            return None
    if r.status_code >= 400:
        log.warning("pixtral http %s: %s", r.status_code, r.text[:200])
        return None
    data = r.json()
    choices = data.get("choices") or []
    if not choices:
        return None
    return (choices[0].get("message", {}).get("content") or "").strip()
