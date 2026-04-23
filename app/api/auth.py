"""Telegram Mini App initData authentication.

Telegram sends a query string with user data + hash signed by bot token.
We validate it matches, then trust the user_id.
See https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException

from app.config import get_settings

log = logging.getLogger(__name__)

MAX_AGE_SECONDS = 24 * 3600  # initData valid for 24h


def _validate(init_data: str, bot_token: str) -> dict | None:
    if not init_data:
        return None
    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except Exception:
        return None
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc_hash, received_hash):
        return None

    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        return None
    if auth_date and (time.time() - auth_date) > MAX_AGE_SECONDS:
        return None

    user_raw = pairs.get("user")
    if not user_raw:
        return None
    try:
        user = json.loads(user_raw)
    except Exception:
        return None
    if not isinstance(user, dict) or "id" not in user:
        return None
    return user


async def require_user(x_telegram_init_data: str = Header(default="")) -> dict:
    s = get_settings()
    user = _validate(x_telegram_init_data, s.tg_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid initData")
    return user
