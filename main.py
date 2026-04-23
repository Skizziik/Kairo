from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from aiogram.types import Update
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from app.api.economy_api import router as economy_router
from app.bot import get_bot, get_dispatcher
from app.config import get_settings
from app.db.client import close_pool, init_pool
from app.scheduler import daily_summary_loop, weekly_memory_compact_loop

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
        allowed_updates=["message", "callback_query", "chat_member", "message_reaction"],
    )
    log.info("webhook set to %s", s.webhook_url)

    scheduler_tasks: list[asyncio.Task] = []
    if s.daily_summary_enabled:
        scheduler_tasks.append(asyncio.create_task(daily_summary_loop(bot)))
        log.info("daily summary scheduler started (fires at %dh UTC)", s.daily_summary_hour_utc)
    scheduler_tasks.append(asyncio.create_task(weekly_memory_compact_loop()))
    log.info("weekly memory compact scheduler started (Sunday 03:00 UTC)")

    try:
        yield
    finally:
        log.info("shutting down")
        for t in scheduler_tasks:
            t.cancel()
        for t in scheduler_tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        # deliberately NOT calling delete_webhook — Telegram keeps the URL,
        # retries pending updates to the next live instance, survives redeploys
        await bot.session.close()
        await close_pool()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

# CORS for Mini App frontend (hosted separately).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Telegram Web Apps can come from any origin user configured
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(economy_router)


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
