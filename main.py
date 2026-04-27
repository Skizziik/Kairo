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
from app.economy import audit as _audit
from app.economy import boss as _boss
from app.economy import case_rebalance as _case_rebalance
from app.economy import coinflip_pvp as _cfpvp
from app.economy import snake as _snake
from app.economy import tiers as _tiers
from app.economy import gear as _gear
from app.economy import mines as _mines
from app.economy import prestige as _prestige
from app.economy import tycoon as _tycoon
from app.economy.chat_events import happy_hour_loop, mystery_drop_loop
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
    # Apply forge schema migrations idempotently
    try:
        await _prestige.ensure_schema()
    except Exception as e:
        log.warning("prestige schema migration failed: %s", e)
    try:
        await _gear.ensure_schema()
    except Exception as e:
        log.warning("gear schema migration failed: %s", e)
    try:
        await _boss.ensure_schema()
    except Exception as e:
        log.warning("boss schema migration failed: %s", e)
    try:
        await _mines.ensure_schema()
    except Exception as e:
        log.warning("mines schema migration failed: %s", e)
    try:
        await _cfpvp.ensure_schema()
    except Exception as e:
        log.warning("coinflip-pvp schema migration failed: %s", e)
    try:
        await _tycoon.ensure_schema()
    except Exception as e:
        log.warning("tycoon schema migration failed: %s", e)
    try:
        await _audit.ensure_schema()
    except Exception as e:
        log.warning("audit schema migration failed: %s", e)
    try:
        await _tiers.ensure_schema()
    except Exception as e:
        log.warning("tiers schema migration failed: %s", e)
    try:
        await _snake.ensure_schema()
    except Exception as e:
        log.warning("snake schema migration failed: %s", e)
    # Trim oversized case pools (idempotent — only changes if current count > cap)
    try:
        await _case_rebalance.rebalance_all()
    except Exception as e:
        log.warning("case rebalance failed: %s", e)
    bot = get_bot()
    dp = get_dispatcher()
    _ = dp  # touch to register routers
    await bot.set_webhook(
        url=s.webhook_url,
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query", "chat_member", "message_reaction", "poll_answer"],
    )
    log.info("webhook set to %s", s.webhook_url)

    scheduler_tasks: list[asyncio.Task] = []
    if s.daily_summary_enabled:
        scheduler_tasks.append(asyncio.create_task(daily_summary_loop(bot)))
        log.info("daily summary scheduler started (fires at %dh UTC)", s.daily_summary_hour_utc)
    scheduler_tasks.append(asyncio.create_task(weekly_memory_compact_loop()))
    log.info("weekly memory compact scheduler started (Sunday 03:00 UTC)")
    if s.casino_chat_events_enabled:
        scheduler_tasks.append(asyncio.create_task(happy_hour_loop(bot)))
        scheduler_tasks.append(asyncio.create_task(mystery_drop_loop(bot)))
        log.info("happy hour + mystery drop schedulers started")
    else:
        log.info("chat events disabled (CASINO_CHAT_EVENTS_ENABLED=false)")

    # Casino-bot coinflip — keeps ~24 active lobbies, +1 every hour
    scheduler_tasks.append(asyncio.create_task(_cfpvp.bot_coinflip_loop()))
    log.info("coinflip bot loop started (target %d active lobbies)", _cfpvp.BOT_LOBBY_TARGET)

    # Bet audit cleanup — drops rows older than RETENTION_DAYS, hourly
    scheduler_tasks.append(asyncio.create_task(_audit.cleanup_loop()))
    log.info("audit cleanup loop started (retention %dd)", _audit.RETENTION_DAYS)

    # Snake AFK farm tick — accumulates passive coins for users who own AFK snakes
    scheduler_tasks.append(asyncio.create_task(_snake.afk_loop()))
    log.info("snake AFK loop started")

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


@app.api_route("/health", methods=["GET", "HEAD"], response_class=PlainTextResponse)
async def health() -> str:
    # UptimeRobot (and most uptime monitors) hit this with HEAD by default to
    # save bandwidth. Without explicit HEAD support FastAPI returns 405, which
    # uptime tools log as "down" — that's why our monitor sat in 405-hell for
    # a week. GET + HEAD covers all reasonable pingers.
    return "ok"


@app.post("/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request) -> dict:
    if secret != s.tg_webhook_secret:
        raise HTTPException(status_code=403, detail="forbidden")
    data = await request.json()
    bot = get_bot()
    dp = get_dispatcher()
    try:
        update = Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, update)
    except Exception:
        # Log but always return 200 to stop Telegram from retrying failed updates
        log.exception("webhook dispatch failed (update_id=%s)", data.get("update_id"))
    return {"ok": True}
