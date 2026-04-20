from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from aiogram.types import Update
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.bot import get_bot, get_dispatcher
from app.config import get_settings
from app.db.client import close_pool, init_pool

s = get_settings()

logging.basicConfig(
    level=s.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("kairo")


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("starting Kairo")
    await init_pool()
    bot = get_bot()
    dp = get_dispatcher()
    _ = dp  # touch to register routers
    await bot.set_webhook(
        url=s.webhook_url,
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query", "chat_member"],
    )
    log.info("webhook set to %s", s.webhook_url)
    try:
        yield
    finally:
        log.info("shutting down")
        await bot.delete_webhook(drop_pending_updates=False)
        await bot.session.close()
        await close_pool()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/", response_class=PlainTextResponse)
async def root() -> str:
    return "RIP нагибатор. Uptime is love."


@app.get("/health", response_class=PlainTextResponse)
async def health() -> str:
    return "ok"


@app.post("/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request) -> dict:
    if secret != s.tg_webhook_secret:
        raise HTTPException(status_code=403, detail="forbidden")
    data = await request.json()
    bot = get_bot()
    dp = get_dispatcher()
    update = Update.model_validate(data, context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}
