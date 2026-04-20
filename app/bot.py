from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.config import get_settings

log = logging.getLogger(__name__)

_bot: Bot | None = None
_dp: Dispatcher | None = None


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        s = get_settings()
        _bot = Bot(
            token=s.tg_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
    return _bot


def get_dispatcher() -> Dispatcher:
    global _dp
    if _dp is None:
        _dp = Dispatcher()
        _register(_dp)
    return _dp


def _register(dp: Dispatcher) -> None:
    from app.handlers import (
        ai_chat,
        help as help_h,
        inv,
        lfg,
        map as map_h,
        me,
        start,
        stats,
        tldr,
        top,
        whereami,
        yt,
    )
    from app.middlewares.antispam import AntispamMiddleware
    from app.middlewares.logging import MessageLogMiddleware

    dp.message.middleware(MessageLogMiddleware())
    dp.message.middleware(AntispamMiddleware())

    dp.include_routers(
        start.router,
        help_h.router,
        whereami.router,
        ai_chat.router,
        tldr.router,
        lfg.router,
        stats.router,
        inv.router,
        map_h.router,
        yt.router,
        me.router,
        top.router,
    )
