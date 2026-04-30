"""TRYLLA EXCHANGE — главный модуль биржевой мини-игры.

Содержит:
- Симуляцию цен (random walk + sectoral drift + cyclic + active news + whale)
- Обработку новостей (spawn, expire, cascade, apply effects)
- Major events (rare global shocks)
- Whale activity (signal-driven)
- Торговля (buy/sell с commission и spread)
- Портфель / leaderboard / subscriptions

Цены хранятся в "копейках" (×100) для целочисленной арифметики.
Quantity хранится в milliunits (×1000) — позволяет торговать 0.001 BTC.
"""
from __future__ import annotations

import json
import logging
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.db.client import pool
from app.economy.market_assets import (
    all_assets, get_asset_by_key, CATEGORIES,
)
from app.economy.market_news import ALL_NEWS, MAJOR_EVENTS, get_news_by_key

log = logging.getLogger(__name__)


# ============================================================
# CONSTANTS
# ============================================================

# Trading params
COMMISSION_PCT = 0.01          # 1% — снижается перками до 0.1%
SPREAD_PCT     = 0.003         # buy/sell spread: 0.3%
STARTING_CASH  = 100_000_00    # 100K TRYLLA (×100 cents)

# Tick params
PRICE_TICK_SECONDS = 5         # симуляция цен каждые 5 сек
NEWS_SPAWN_MIN_SEC = 60        # новости каждые 1-3 мин
NEWS_SPAWN_MAX_SEC = 180
SNAPSHOT_KEEP      = 1000      # legacy (count-based fallback)
SNAPSHOT_RETENTION_SEC = 24 * 3600   # храним 24 часа сырых тиков (для tf=24h)

# Market mood — global multiplier on volatility
MARKET_MOOD_BASE = 1.0

# Subscription pricing
SUB_PRICE_24H = 1000_00        # 1000 TRYLLA (×100)


# ============================================================
# IN-MEMORY STATE (volatile across server restarts — that's OK)
# ============================================================
#
# Per-asset state for the price simulation:
#   - last_delta:  used for MOMENTUM (rolling tendency to continue trending)
#   - regime:      'trending_up' | 'trending_down' | 'consolidating' | 'volatile'
#                  changes ~once per N ticks; affects formula weights
#   - regime_ticks_left: countdown
#
# Correlation hub state:
#   - btc_delta:   used to pull all crypto in BTC's direction (β coefficient)
#   - gold_delta:  used as safe-haven signal (negative correlation with stocks/crypto)

_asset_state: dict[str, dict] = {}
_btc_recent_delta = 0.0
_gold_recent_delta = 0.0
_market_mood = 1.0   # global multiplier; drifts each tick


def _asset_st(key: str) -> dict:
    s = _asset_state.get(key)
    if s is None:
        s = {"last_delta": 0.0, "regime": "consolidating",
             "regime_ticks_left": random.randint(60, 240)}
        _asset_state[key] = s
    return s


def _maybe_change_regime(st: dict) -> None:
    """Each asset can be in a regime that biases its formula. Regimes shift
    every few minutes randomly — this creates "trends" that opportunistic
    traders can spot but never predict precisely."""
    st["regime_ticks_left"] -= 1
    if st["regime_ticks_left"] <= 0:
        st["regime"] = random.choices(
            ["trending_up", "trending_down", "consolidating", "volatile"],
            weights=[1.0, 1.0, 1.5, 0.7],
        )[0]
        # Trending regimes last longer than volatile ones
        if st["regime"] in ("trending_up", "trending_down"):
            st["regime_ticks_left"] = random.randint(120, 360)   # 10-30 min
        elif st["regime"] == "volatile":
            st["regime_ticks_left"] = random.randint(40, 100)
        else:
            st["regime_ticks_left"] = random.randint(80, 240)


# ============================================================
# DB / SCHEMA
# ============================================================

async def ensure_schema() -> None:
    sql_path = Path(__file__).parent.parent / "db" / "migration_market.sql"
    if not sql_path.exists():
        log.warning("market migration SQL missing")
        return
    sql = sql_path.read_text(encoding="utf-8")
    async with pool().acquire() as conn:
        await conn.execute(sql)
    log.info("market schema ensured")
    # Seed assets if empty
    await _seed_assets_if_empty()


async def _seed_assets_if_empty() -> None:
    """Insert all 294 asset configs on first run. Idempotent — only if empty."""
    async with pool().acquire() as conn:
        cnt = await conn.fetchval("select count(*) from market_assets")
        if cnt and int(cnt) > 0:
            return
        log.info("market: seeding %d assets", len(all_assets()))
        for entry in all_assets():
            (cat, key, name, symbol, sub, rarity, base, vol, liq, tags, cyc_h, amp) = entry
            cycle_period_sec = int(cyc_h * 3600)
            cycle_phase = random.uniform(0, 2 * math.pi)
            image_path = f"img/market/{cat}/{key}.png"
            await conn.execute(
                """
                insert into market_assets
                  (key, category, subcategory, name, symbol, rarity, base_price, current_price,
                   volatility, liquidity, tags, cycle_period_sec, cycle_amplitude, cycle_phase,
                   high_24h, low_24h, open_24h, image_path, last_tick_at)
                values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12,$13,$14,$15,$16,$17,$18,now())
                on conflict (key) do nothing
                """,
                key, cat, sub, name, symbol, rarity, int(base), int(base),
                float(vol), float(liq), json.dumps(tags),
                cycle_period_sec, float(amp), cycle_phase,
                int(base), int(base), int(base), image_path,
            )


async def ensure_user(tg_id: int) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "insert into market_users (tg_id) values ($1) on conflict do nothing",
            tg_id,
        )


def _parse_jsonb(val) -> Any:
    if val is None: return None
    if isinstance(val, (dict, list)): return val
    if isinstance(val, str):
        try: return json.loads(val)
        except Exception: return None
    return None


# ============================================================
# PRICE SIMULATION
# ============================================================

async def price_tick() -> None:
    """One tick of price simulation across all assets. Called every 5s.

    The formula has 9 components (in declining importance):
        1. random_walk          — gaussian noise (σ scales with volatility)
        2. momentum             — 30% of previous delta carries forward (trends!)
        3. regime_bias          — current "regime" tints the drift
        4. sector_drift         — whole category leans together
        5. cyclic               — per-asset sine wave (long-term tide)
        6. news_effects         — sum of active news impacts (cosine decay)
        7. correlation_pull     — BTC pulls crypto, gold tugs safe-haven
        8. breakout_accel       — pierce 24h H/L → momentum accelerates
        9. fat_tail_shock       — 0.5% chance ×3 chaos move
        10. liquidity_amp       — thin assets swing wider than thick ones

    Multiple multipliers stack so the chart never looks like clean noise —
    you'll see trending periods, sudden gaps, mean reversions, and bursts.
    """
    global _btc_recent_delta, _gold_recent_delta, _market_mood
    now = datetime.now(timezone.utc)

    # ─── Pull data ───
    async with pool().acquire() as conn:
        assets = await conn.fetch(
            "select key, category, subcategory, base_price, current_price, "
            "volatility, liquidity, tags, cycle_period_sec, cycle_amplitude, "
            "cycle_phase, high_24h, low_24h, open_24h "
            "from market_assets"
        )
        news_rows = await conn.fetch(
            "select id, type, severity, affected, duration_sec, spawned_at, expires_at "
            "from market_news where expires_at > now()"
        )

    # ─── 6. News effect map ───
    asset_effects: dict[str, float] = {}
    for n in news_rows:
        affected = _parse_jsonb(n["affected"]) or {}
        elapsed = (now - n["spawned_at"]).total_seconds()
        total = (n["expires_at"] - n["spawned_at"]).total_seconds() or 1
        progress = min(1.0, elapsed / total)
        intensity = 0.5 * (1 + math.cos(progress * math.pi))
        # Heavy/extreme news kick harder at start
        sev = n["severity"]
        if sev == "heavy":   intensity *= 1.5
        if sev == "extreme": intensity *= 2.5

        def _apply(k: str, pct: float):
            asset_effects[k] = asset_effects.get(k, 0) + pct * intensity / 100.0

        if "asset" in affected:
            _apply(affected["asset"], float(affected.get("pct", 0)))
        if "assets" in affected:
            for a in affected["assets"]:
                if a.get("key"):
                    _apply(a["key"], float(a.get("pct", 0)))
        if "category" in affected:
            cat = affected["category"]; pct = float(affected.get("pct", 0))
            for a in assets:
                if a["category"] == cat: _apply(a["key"], pct)
        if "subcategory" in affected:
            sub = affected["subcategory"]; pct = float(affected.get("pct", 0))
            for a in assets:
                if a["subcategory"] == sub: _apply(a["key"], pct)
        if "tag" in affected:
            tag = affected["tag"]; pct = float(affected.get("pct", 0))
            for a in assets:
                tags = _parse_jsonb(a["tags"]) or []
                if tag in tags: _apply(a["key"], pct)

    # ─── 4. Sector drift — each category gets its own mood ───
    sector_drift = {cat: random.gauss(0, 0.0020) for cat in CATEGORIES.keys()}

    # ─── Market mood — drifts slowly between bullish/bearish ───
    _market_mood += random.gauss(0, 0.005)
    _market_mood = max(0.7, min(1.3, _market_mood))   # clamp

    # Will be set after BTC tick — used by all other crypto for correlation
    new_btc_delta_pct = None
    new_gold_delta_pct = None

    # ─── For each asset: compose full delta ───
    updates = []
    snapshots = []

    # Process BTC and gold FIRST so we have their deltas for correlation
    asset_order = list(assets)
    asset_order.sort(key=lambda a: 0 if a["key"] in ("btc", "gold") else 1)

    # Абсолютный пол на цену: 1 цент = 0.01 TRYLLA. Иначе мемкоины
    # с base_price=1 уезжают в int(0.02)=0 и multiplicative модель
    # больше не может оттуда вытянуть.
    PRICE_FLOOR = 1.0

    for a in asset_order:
        key = a["key"]
        cat = a["category"]
        sub = a["subcategory"] or ""
        base = float(a["base_price"])
        cur  = max(PRICE_FLOOR, float(a["current_price"]))   # heal stuck-at-zero
        vol  = float(a["volatility"])
        liq  = max(0.1, float(a["liquidity"]))
        tags = _parse_jsonb(a["tags"]) or []

        st = _asset_st(key)
        _maybe_change_regime(st)

        # ── 1. Random walk (much more lively than before)
        sigma = 0.0025 * (1 + 0.5 * vol)   # higher vol asset → wider gaussian
        drift = random.gauss(0, sigma)

        # ── 2. Momentum — 30-40% of previous delta carries forward
        momentum = st["last_delta"] * 0.35
        drift += momentum

        # ── 3. Regime bias
        if st["regime"] == "trending_up":     drift += 0.0010 * (1 + vol)
        elif st["regime"] == "trending_down": drift -= 0.0010 * (1 + vol)
        elif st["regime"] == "volatile":      drift += random.gauss(0, 0.003 * (1 + vol))

        # ── 4. Sector drift
        drift += sector_drift.get(cat, 0.0)

        # ── 5. Cyclic — visible sine wave (×1.0 strength now)
        period_sec = max(60, int(a["cycle_period_sec"]))
        amp = float(a["cycle_amplitude"])
        phase = float(a["cycle_phase"])
        t = now.timestamp()
        cyclic = amp * math.cos(2 * math.pi * t / period_sec + phase) * (2 * math.pi / period_sec) * PRICE_TICK_SECONDS
        drift += cyclic * 1.0   # was 0.2

        # ── 6. News effects (per-minute pct → per-tick fraction)
        news_pct = asset_effects.get(key, 0.0)
        drift += news_pct * (PRICE_TICK_SECONDS / 60.0)

        # ── 7. Correlation pull
        if cat == "crypto" and key not in ("btc",) and "stablecoin" not in tags:
            # crypto follows BTC at β=0.5..0.9 depending on subcategory
            if _btc_recent_delta:
                beta = 0.7 if sub == "mainstream" else 0.55 if sub == "altcoin" else 0.85
                drift += _btc_recent_delta * beta * 0.6
        if cat in ("stocks", "tech") and "ai" not in tags:
            # Risk-on: positive correlation with BTC
            if _btc_recent_delta:
                drift += _btc_recent_delta * 0.15
        if "safe_haven" in tags and key != "gold":
            # Other safe havens follow gold
            if _gold_recent_delta:
                drift += _gold_recent_delta * 0.5
        if (cat in ("stocks", "tech") or "stablecoin" in tags) and _gold_recent_delta:
            # Risk-off (gold up) → stocks slight down
            drift -= _gold_recent_delta * 0.10

        # ── 8. Breakout acceleration — pierce 24h H/L → trend extends
        h24 = int(a["high_24h"])
        l24 = int(a["low_24h"])
        if h24 > 0 and cur > h24 * 1.001:
            drift += 0.0015 * (1 + vol)   # breakout up
        if l24 > 0 and cur < l24 * 0.999:
            drift -= 0.0015 * (1 + vol)   # breakdown

        # ── 9. Fat tail — rare chaos move (0.5% per tick per asset)
        if random.random() < 0.005:
            shock = random.choice([-1, 1]) * random.uniform(0.02, 0.06) * (1 + vol)
            drift += shock

        # ── 10. Liquidity amplification — thin assets feel wilder
        liquidity_amp = 1.0 + (1.0 - liq) * 0.5

        # ── Mean reversion (kicks in only if very far from base)
        if cur > base * 8:    drift -= 0.008
        elif cur > base * 4:  drift -= 0.003
        elif cur < base * 0.15: drift += 0.008
        elif cur < base * 0.4:  drift += 0.003

        # ── Final delta
        delta = drift * (1 + vol) * liquidity_amp * _market_mood

        # Record for momentum
        st["last_delta"] = delta * 0.5 + st["last_delta"] * 0.5   # smoothed

        # ── New price with hard floor/ceiling
        new_price = cur * (1.0 + delta)
        new_price = max(PRICE_FLOOR, base * 0.02, min(base * 100, new_price))

        # Capture BTC/gold deltas for correlation cascade
        if key == "btc":
            new_btc_delta_pct = delta
            _btc_recent_delta = delta
        if key == "gold":
            new_gold_delta_pct = delta
            _gold_recent_delta = delta

        updates.append((key, int(new_price)))
        snapshots.append((key, int(new_price)))

    # 5. Bulk update + snapshot insert
    async with pool().acquire() as conn:
        async with conn.transaction():
            for key, new_price in updates:
                await conn.execute(
                    "update market_assets set current_price = $2, last_tick_at = $3, "
                    "high_24h = greatest(high_24h, $2), "
                    "low_24h  = case when low_24h = 0 then $2 else least(low_24h, $2) end "
                    "where key = $1",
                    key, new_price, now,
                )
            # Snapshots — bulk insert
            if snapshots:
                vals = []
                args = []
                for i, (k, p) in enumerate(snapshots):
                    vals.append(f"(${i*2+1}, ${i*2+2}, now())")
                    args.extend([k, p])
                # Postgres has param limit ~32K, batch by 200
                batch_size = 200
                for i in range(0, len(snapshots), batch_size):
                    batch = snapshots[i:i+batch_size]
                    placeholders = []
                    bargs = []
                    for j, (k, p) in enumerate(batch):
                        placeholders.append(f"(${j*2+1}, ${j*2+2}, now())")
                        bargs.extend([k, p])
                    await conn.execute(
                        f"insert into market_price_snapshots (asset_key, price, ts) values "
                        + ",".join(placeholders),
                        *bargs,
                    )

            # Trim old snapshots by time — храним последние SNAPSHOT_RETENTION_SEC
            # секунд (по умолчанию 24ч), хватает для tf=24h. Index (asset_key, ts)
            # позволяет удалению быть дешёвым.
            await conn.execute(
                "delete from market_price_snapshots where ts < now() - "
                f"interval '{SNAPSHOT_RETENTION_SEC} seconds'"
            )


# ============================================================
# NEWS ENGINE
# ============================================================

async def news_spawn_tick() -> None:
    """Spawn 1-2 news items at a randomized interval. Some news cascade."""
    async with pool().acquire() as conn:
        # Don't spawn if too many active already
        active = await conn.fetchval(
            "select count(*) from market_news where expires_at > now()"
        )
        if active and int(active) > 8:
            return

    # Pick a random news entry
    entry = random.choice(ALL_NEWS)
    await _spawn_news(entry)


async def _spawn_news(entry: tuple) -> None:
    """entry = (key, headline, body, type, severity, affected, duration_min,
               cascade_key, cascade_delay_min)"""
    (key, headline, body, ntype, severity, affected, duration_min,
     cascade_key, cascade_delay_min) = entry
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=int(duration_min))
    async with pool().acquire() as conn:
        await conn.execute(
            """
            insert into market_news
              (headline, body, type, severity, affected, duration_sec,
               cascade_news_key, cascade_delay_sec, spawned_at, expires_at)
            values ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10)
            """,
            headline, body, ntype, severity, json.dumps(affected),
            int(duration_min) * 60,
            cascade_key, int((cascade_delay_min or 0) * 60),
            now, expires_at,
        )
    log.info("market news spawned: %s [%s]", headline, ntype)


async def cascade_check_tick() -> None:
    """Check for news with cascade_news_key whose cascade_delay has elapsed.
    If the parent news has been alive past delay, spawn the cascade child."""
    now = datetime.now(timezone.utc)
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            select id, cascade_news_key, cascade_delay_sec, spawned_at
            from market_news
            where cascade_news_key is not null
              and spawned_at + (cascade_delay_sec || ' seconds')::interval <= $1
              and spawned_at + (cascade_delay_sec || ' seconds')::interval > $1 - interval '15 seconds'
            """, now,
        )
    for r in rows:
        child_key = r["cascade_news_key"]
        entry = get_news_by_key(child_key)
        if entry:
            await _spawn_news(entry)


# ============================================================
# MAJOR EVENTS
# ============================================================

async def major_event_tick() -> None:
    """Roll for a major event each tick. Very low chance."""
    for event in MAJOR_EVENTS:
        chance = float(event.get("frequency_chance", 0))
        if random.random() < chance:
            await _trigger_major_event(event)
            return  # only one per tick


async def _trigger_major_event(event: dict) -> None:
    """Apply event effects via spawning multiple news items."""
    log.info("MARKET MAJOR EVENT: %s", event["name"])
    duration_min = int(event.get("duration_min", 60))
    severity = event.get("severity", "extreme")
    name = event["name"]
    desc = event.get("description", "")

    # Build a synthetic affected dict from event effects
    affected_total = []
    for selector, pct in event.get("effects", {}).items():
        sel_type, sel_value = selector.split(":", 1)
        if sel_type == "asset":
            affected_total.append({"asset": sel_value, "pct": pct})
        elif sel_type == "category":
            affected_total.append({"category": sel_value, "pct": pct})
        elif sel_type == "subcategory":
            affected_total.append({"subcategory": sel_value, "pct": pct})
        elif sel_type == "tag":
            affected_total.append({"tag": sel_value, "pct": pct})

    # Spawn one mega-news per effect
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=duration_min)
    async with pool().acquire() as conn:
        for eff in affected_total:
            await conn.execute(
                """
                insert into market_news
                  (headline, body, type, severity, affected, duration_sec, spawned_at, expires_at)
                values ($1,$2,$3,$4,$5::jsonb,$6,$7,$8)
                """,
                f"⚡ {name}", desc, "major_event", severity,
                json.dumps(eff), duration_min * 60, now, expires_at,
            )


# ============================================================
# WHALE ACTIVITY
# ============================================================

async def whale_tick() -> None:
    """Random chance of whale movement. Larger volatility = more whales."""
    if random.random() > 0.05:   # 5% chance per tick
        return
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "select key, volatility, current_price from market_assets "
            "where rarity != 'mythic' "
            "order by random() limit 1"
        )
    if not rows:
        return
    a = rows[0]
    action = random.choice(["accumulate", "distribute"])
    magnitude = (3 + 12 * float(a["volatility"])) * (1 if action == "accumulate" else -1)
    now = datetime.now(timezone.utc)
    visible_at = now
    insider_at = now - timedelta(seconds=30)  # skilled players see early
    executes_at = now + timedelta(seconds=random.randint(30, 180))
    async with pool().acquire() as conn:
        await conn.execute(
            """
            insert into market_whale_actions
              (asset_key, action, magnitude, visible_at, insider_at, executes_at)
            values ($1,$2,$3,$4,$5,$6)
            """,
            a["key"], action, float(magnitude), visible_at, insider_at, executes_at,
        )


# ============================================================
# TRADING
# ============================================================

async def buy_asset(
    tg_id: int,
    asset_key: str,
    cash_amount: int | None = None,
    quantity_micro: int | None = None,
) -> dict:
    """Купить `asset_key` либо на сумму `cash_amount` (в центах TRYLLA),
    либо на конкретное кол-во `quantity_micro` (микро-юниты ×1e6).
    Один из двух обязателен."""
    if (cash_amount is None or cash_amount <= 0) and \
       (quantity_micro is None or quantity_micro <= 0):
        return {"ok": False, "error": "Укажи сумму или количество"}
    await ensure_user(tg_id)
    async with pool().acquire() as conn:
        async with conn.transaction():
            asset = await conn.fetchrow(
                "select * from market_assets where key = $1", asset_key,
            )
            if asset is None:
                return {"ok": False, "error": "Актив не найден"}
            user = await conn.fetchrow(
                "select trylla, skills from market_users where tg_id = $1 for update", tg_id,
            )
            if user is None:
                return {"ok": False, "error": "Нет состояния"}

            # Effective commission (skill discount)
            skills = _parse_jsonb(user["skills"]) or {}
            cm = COMMISSION_PCT * (1 - 0.05 * int(skills.get("lower_fees", 0)))  # 5% off per level
            cm = max(0.001, cm)

            # Buy at spread-adjusted price
            base_price = int(asset["current_price"])
            fill_price = int(base_price * (1.0 + SPREAD_PCT / 2))

            # Если задано quantity_micro — пересчитываем нужный cash_amount.
            # cost = qty_micro * fill_price / 1e6 ; cash * (1-cm) >= cost
            if quantity_micro is not None and quantity_micro > 0:
                cost_no_comm = (quantity_micro * fill_price + 999_999) // 1_000_000  # ceil
                cash_amount = int(math.ceil(cost_no_comm / max(0.0001, 1.0 - cm)))

            if int(user["trylla"]) < cash_amount:
                return {"ok": False, "error": "Не хватает TRYLLA",
                        "have": int(user["trylla"]), "need": cash_amount}

            commission = int(cash_amount * cm)
            available = cash_amount - commission
            if available <= 0:
                return {"ok": False, "error": "После комиссии ничего не остаётся"}

            # Quantity in milliunits: (available / fill_price) × 1000
            # Quantity in microunits (×1,000,000) for sub-cent precision.
            quantity = int((available * 1_000_000) // fill_price)
            if quantity <= 0:
                return {"ok": False, "error": "Слишком мало для покупки"}
            actual_cost = (quantity * fill_price) // 1_000_000   # back to cents

            # Apply
            await conn.execute(
                "update market_users set trylla = trylla - $2, total_trades = total_trades + 1, "
                "total_invested = total_invested + $2, last_active_at = now() where tg_id = $1",
                tg_id, actual_cost + commission,
            )
            # Upsert holding (weighted avg buy price)
            existing = await conn.fetchrow(
                "select quantity, avg_buy_price from market_holdings "
                "where user_id = $1 and asset_key = $2 for update",
                tg_id, asset_key,
            )
            if existing:
                old_qty = int(existing["quantity"])
                old_avg = int(existing["avg_buy_price"])
                new_qty = old_qty + quantity
                new_avg = (old_qty * old_avg + quantity * fill_price) // new_qty
                await conn.execute(
                    "update market_holdings set quantity = $3, avg_buy_price = $4, "
                    "last_traded_at = now() where user_id = $1 and asset_key = $2",
                    tg_id, asset_key, new_qty, new_avg,
                )
            else:
                await conn.execute(
                    "insert into market_holdings (user_id, asset_key, quantity, avg_buy_price) "
                    "values ($1,$2,$3,$4)",
                    tg_id, asset_key, quantity, fill_price,
                )
            # Trade record
            await conn.execute(
                """
                insert into market_trades
                  (user_id, asset_key, side, quantity, price, total_value, commission, realized_pl)
                values ($1,$2,'buy',$3,$4,$5,$6,0)
                """,
                tg_id, asset_key, quantity, fill_price, actual_cost, commission,
            )
            # Bump XP — 5 per trade + 1 per 1K cash
            xp_gain = 5 + cash_amount // 100_000
            await conn.execute(
                "update market_users set xp = xp + $2 where tg_id = $1",
                tg_id, xp_gain,
            )

    return {
        "ok": True,
        "quantity": quantity,
        "fill_price": fill_price,
        "actual_cost": actual_cost,
        "commission": commission,
    }


async def sell_asset(tg_id: int, asset_key: str, quantity_pct: int = 100) -> dict:
    """Sell `quantity_pct` of holding (1-100). Spread-adjusted. Applies commission
    and computes realized P/L. XP for profitable sells."""
    quantity_pct = max(1, min(100, int(quantity_pct)))
    await ensure_user(tg_id)
    async with pool().acquire() as conn:
        async with conn.transaction():
            asset = await conn.fetchrow(
                "select current_price from market_assets where key = $1", asset_key,
            )
            if asset is None:
                return {"ok": False, "error": "Актив не найден"}
            holding = await conn.fetchrow(
                "select quantity, avg_buy_price from market_holdings "
                "where user_id = $1 and asset_key = $2 for update",
                tg_id, asset_key,
            )
            if not holding or int(holding["quantity"]) <= 0:
                return {"ok": False, "error": "Нет позиции"}

            user = await conn.fetchrow(
                "select trylla, skills from market_users where tg_id = $1 for update", tg_id,
            )
            skills = _parse_jsonb(user["skills"]) or {}
            cm = COMMISSION_PCT * (1 - 0.05 * int(skills.get("lower_fees", 0)))
            cm = max(0.001, cm)

            full_qty = int(holding["quantity"])
            sell_qty = (full_qty * quantity_pct) // 100
            if sell_qty <= 0:
                return {"ok": False, "error": "Слишком мало для продажи"}

            base_price = int(asset["current_price"])
            fill_price = int(base_price * (1.0 - SPREAD_PCT / 2))
            gross = (sell_qty * fill_price) // 1_000_000  # cents (microunits → cents)
            commission = int(gross * cm)
            net = gross - commission

            avg_buy = int(holding["avg_buy_price"])
            cost_basis = (sell_qty * avg_buy) // 1_000_000
            realized_pl = net - cost_basis

            # Apply
            new_qty = full_qty - sell_qty
            if new_qty == 0:
                await conn.execute(
                    "delete from market_holdings where user_id = $1 and asset_key = $2",
                    tg_id, asset_key,
                )
            else:
                await conn.execute(
                    "update market_holdings set quantity = $3, last_traded_at = now() "
                    "where user_id = $1 and asset_key = $2",
                    tg_id, asset_key, new_qty,
                )

            await conn.execute(
                """
                update market_users set
                  trylla = trylla + $2,
                  total_trades = total_trades + 1,
                  total_realized_pl = total_realized_pl + $3,
                  best_trade_pl = greatest(best_trade_pl, $3),
                  worst_trade_pl = least(worst_trade_pl, $3),
                  win_count = win_count + $4,
                  loss_count = loss_count + $5,
                  last_active_at = now()
                where tg_id = $1
                """,
                tg_id, net, realized_pl,
                1 if realized_pl > 0 else 0,
                1 if realized_pl < 0 else 0,
            )

            await conn.execute(
                """
                insert into market_trades
                  (user_id, asset_key, side, quantity, price, total_value, commission, realized_pl)
                values ($1,$2,'sell',$3,$4,$5,$6,$7)
                """,
                tg_id, asset_key, sell_qty, fill_price, gross, commission, realized_pl,
            )
            # XP — больше за прибыльные сделки
            xp_gain = 5 + max(0, realized_pl // 1_000_00)
            await conn.execute(
                "update market_users set xp = xp + $2 where tg_id = $1",
                tg_id, int(xp_gain),
            )

    return {
        "ok": True, "sold_quantity": sell_qty, "fill_price": fill_price,
        "gross": gross, "commission": commission, "net": net,
        "realized_pl": realized_pl,
    }


# ============================================================
# CONVERSION TRYLLA → main coins (taxable)
# ============================================================

async def convert_to_coins(tg_id: int, amount_trylla: int) -> dict:
    """Конвертация TRYLLA → основные коины 1:1. Облагается налогом."""
    if amount_trylla <= 0:
        return {"ok": False, "error": "Сумма должна быть > 0"}
    await ensure_user(tg_id)
    async with pool().acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow(
                "select trylla from market_users where tg_id = $1 for update", tg_id,
            )
            if user is None or int(user["trylla"]) < amount_trylla:
                return {"ok": False, "error": "Недостаточно TRYLLA"}
            await conn.execute(
                "update market_users set trylla = trylla - $2 where tg_id = $1",
                tg_id, amount_trylla,
            )
            # Crediт основной баланс (TRYLLA cents → coins: divide by 100)
            credited_coins = amount_trylla // 100
            new_bal_row = await conn.fetchrow(
                "update economy_users set balance = balance + $2, "
                "total_earned = total_earned + $2 where tg_id = $1 returning balance",
                tg_id, credited_coins,
            )
            new_bal = int(new_bal_row["balance"]) if new_bal_row else 0
            try:
                await conn.execute(
                    "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                    "values ($1, $2, 'market_convert', $3, $4)",
                    tg_id, credited_coins, f"convert_{amount_trylla}_trylla", new_bal,
                )
            except Exception: pass

    # Tax accrual on the converted amount (taxable income)
    try:
        from app.economy import tax as _tax
        await _tax.accrue_tax(tg_id, credited_coins, "market_convert")
    except Exception: pass

    return {"ok": True, "converted_trylla": amount_trylla,
            "credited_coins": credited_coins, "new_balance": new_bal}


# ============================================================
# READ STATE / PORTFOLIO
# ============================================================

async def get_state(tg_id: int) -> dict:
    """Полное состояние игрока на бирже."""
    await ensure_user(tg_id)
    async with pool().acquire() as conn:
        user = await conn.fetchrow(
            "select * from market_users where tg_id = $1", tg_id,
        )
        holdings = await conn.fetch(
            """
            select h.*, a.name, a.symbol, a.current_price, a.image_path,
                   a.category, a.rarity
            from market_holdings h
            join market_assets a on a.key = h.asset_key
            where h.user_id = $1
            order by h.last_traded_at desc
            """, tg_id,
        )

    # Compute portfolio value
    portfolio_value = 0
    holdings_payload = []
    for h in holdings:
        qty = int(h["quantity"])
        avg = int(h["avg_buy_price"])
        cur = int(h["current_price"])
        value = (qty * cur) // 1_000_000
        cost = (qty * avg) // 1_000_000
        pl = value - cost
        pct = (pl / cost * 100.0) if cost > 0 else 0
        portfolio_value += value
        holdings_payload.append({
            "asset_key": h["asset_key"],
            "name": h["name"],
            "symbol": h["symbol"],
            "category": h["category"],
            "rarity": h["rarity"],
            "image": h["image_path"],
            "quantity": qty,
            "avg_buy_price": avg,
            "current_price": cur,
            "value": value,
            "cost": cost,
            "pl": pl,
            "pl_pct": round(pct, 2),
        })

    # Level
    cur_xp = int(user["xp"] or 0)
    level = level_for_xp(cur_xp)
    xp_to_next = xp_needed_for(level) - cur_xp

    return {
        "tg_id": int(user["tg_id"]),
        "trylla": int(user["trylla"]),
        "level": level,
        "xp": cur_xp,
        "xp_to_next": xp_to_next,
        "next_level_xp": xp_needed_for(level),
        "skills": _parse_jsonb(user["skills"]) or {},
        "total_trades": int(user["total_trades"]),
        "total_invested": int(user["total_invested"]),
        "total_realized_pl": int(user["total_realized_pl"]),
        "best_trade_pl": int(user["best_trade_pl"]),
        "worst_trade_pl": int(user["worst_trade_pl"]),
        "win_count": int(user["win_count"]),
        "loss_count": int(user["loss_count"]),
        "win_rate": round(int(user["win_count"]) / max(1, int(user["win_count"]) + int(user["loss_count"])) * 100, 1),
        "subscriber_count": int(user["subscriber_count"]),
        "portfolio_privacy": user["portfolio_privacy"],
        "portfolio_value": portfolio_value,
        "total_value": int(user["trylla"]) + portfolio_value,
        "holdings": holdings_payload,
    }


async def get_assets() -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "select key, category, subcategory, name, symbol, rarity, "
            "current_price, base_price, volatility, liquidity, tags, "
            "high_24h, low_24h, open_24h, image_path "
            "from market_assets order by category, key"
        )
    return [
        {
            "key": r["key"], "category": r["category"], "subcategory": r["subcategory"],
            "name": r["name"], "symbol": r["symbol"], "rarity": r["rarity"],
            "current_price": int(r["current_price"]),
            "base_price": int(r["base_price"]),
            "volatility": float(r["volatility"]),
            "liquidity": float(r["liquidity"]),
            "tags": _parse_jsonb(r["tags"]) or [],
            "high_24h": int(r["high_24h"]),
            "low_24h": int(r["low_24h"]),
            "open_24h": int(r["open_24h"]),
            "change_24h_pct": round((int(r["current_price"]) - int(r["open_24h"])) / max(1, int(r["open_24h"])) * 100, 2) if r["open_24h"] else 0,
            "image": r["image_path"],
        }
        for r in rows
    ]


# Таймфреймы графика: окно (сек) → сколько точек отдавать в UI.
# Бакетим в ~120 точек чтобы canvas рисовал плавно.
_CHART_TF: dict[str, dict] = {
    "10m": {"window_sec": 600,    "buckets": 120},   # native 5s
    "1h":  {"window_sec": 3600,   "buckets": 120},   # ~30s
    "12h": {"window_sec": 43200,  "buckets": 120},   # ~6m
    "24h": {"window_sec": 86400,  "buckets": 120},   # ~12m
}


def _downsample_snaps(snaps: list, n_buckets: int) -> list[dict]:
    """Equal-time bucketing: avg price per bucket. snaps must be ts-asc."""
    if not snaps:
        return []
    if len(snaps) <= n_buckets:
        return [{"price": int(s["price"]), "ts": s["ts"].isoformat()} for s in snaps]
    t0 = snaps[0]["ts"].timestamp()
    t1 = snaps[-1]["ts"].timestamp()
    if t1 <= t0:
        return [{"price": int(snaps[-1]["price"]), "ts": snaps[-1]["ts"].isoformat()}]
    bsz = (t1 - t0) / n_buckets
    buckets: list[list] = [[] for _ in range(n_buckets)]
    for s in snaps:
        idx = int((s["ts"].timestamp() - t0) / bsz)
        if idx >= n_buckets: idx = n_buckets - 1
        buckets[idx].append(s)
    out: list[dict] = []
    for b in buckets:
        if not b:
            continue
        avg = sum(int(s["price"]) for s in b) // len(b)
        out.append({"price": avg, "ts": b[-1]["ts"].isoformat()})
    return out


async def get_chart(asset_key: str, tf: str = "10m", points: int = 0) -> dict:
    """Снимки цен в окне tf, бакетные в ~120 точек."""
    cfg = _CHART_TF.get(tf, _CHART_TF["10m"])
    window = cfg["window_sec"]
    n_buckets = cfg["buckets"]
    async with pool().acquire() as conn:
        asset = await conn.fetchrow(
            "select * from market_assets where key = $1", asset_key,
        )
        if asset is None:
            return {"ok": False, "error": "Не найдено"}
        snaps = await conn.fetch(
            "select price, ts from market_price_snapshots "
            "where asset_key = $1 and ts >= now() - "
            f"interval '{window} seconds' "
            "order by ts asc",
            asset_key,
        )
    pts = _downsample_snaps(list(snaps), n_buckets)
    # Canvas нужно >=2 точки. Если истории за окно нет (фрешный деплой,
    # длинный tf) — рисуем плоскую линию из open_24h → current_price.
    if len(pts) < 2:
        now_ts = datetime.now(timezone.utc)
        open_p = int(asset["open_24h"]) or int(asset["current_price"])
        pts = [
            {"price": open_p, "ts": (now_ts - timedelta(seconds=window)).isoformat()},
            {"price": int(asset["current_price"]),
             "ts": (asset["last_tick_at"] or now_ts).isoformat()},
        ]
    return {
        "ok": True,
        "tf": tf,
        "asset": {
            "key": asset["key"], "name": asset["name"], "symbol": asset["symbol"],
            "current_price": int(asset["current_price"]),
            "high_24h": int(asset["high_24h"]),
            "low_24h": int(asset["low_24h"]),
            "open_24h": int(asset["open_24h"]),
            "volatility": float(asset["volatility"]),
            "image": asset["image_path"],
            "category": asset["category"], "rarity": asset["rarity"],
            "subcategory": asset["subcategory"],
        },
        "points": pts,
    }


async def get_news(limit: int = 30) -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "select id, headline, body, type, severity, affected, "
            "spawned_at, expires_at "
            "from market_news order by spawned_at desc limit $1",
            limit,
        )
    return [
        {
            "id": int(r["id"]),
            "headline": r["headline"],
            "body": r["body"],
            "type": r["type"],
            "severity": r["severity"],
            "affected": _parse_jsonb(r["affected"]) or {},
            "spawned_at": r["spawned_at"].isoformat() if r["spawned_at"] else None,
            "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
            "active": r["expires_at"] > datetime.now(timezone.utc) if r["expires_at"] else False,
        }
        for r in rows
    ]


async def leaderboard(sort_by: str = "total_value", limit: int = 30) -> list[dict]:
    """Sort modes: total_value (cash + portfolio), realized_pl, win_rate."""
    if sort_by not in ("total_value", "realized_pl", "win_rate", "trades"):
        sort_by = "total_value"

    async with pool().acquire() as conn:
        # Compute portfolio value as a subquery
        rows = await conn.fetch(
            """
            select
              m.tg_id, m.trylla, m.level, m.xp, m.total_trades,
              m.total_realized_pl, m.win_count, m.loss_count,
              m.subscriber_count, m.portfolio_privacy,
              u.username, u.first_name, u.photo_url,
              coalesce((
                select sum((h.quantity * a.current_price) / 1000000)
                from market_holdings h
                join market_assets a on a.key = h.asset_key
                where h.user_id = m.tg_id
              ), 0) as portfolio_value
            from market_users m
            left join users u on u.tg_id = m.tg_id
            order by
              case when $1 = 'total_value'  then m.trylla + coalesce((
                  select sum((h.quantity * a.current_price) / 1000000)
                  from market_holdings h
                  join market_assets a on a.key = h.asset_key
                  where h.user_id = m.tg_id
              ), 0) else 0 end desc,
              case when $1 = 'realized_pl'  then m.total_realized_pl else 0 end desc,
              case when $1 = 'win_rate' and (m.win_count + m.loss_count) > 0
                   then m.win_count::float / (m.win_count + m.loss_count) else 0 end desc,
              case when $1 = 'trades'       then m.total_trades else 0 end desc
            limit $2
            """, sort_by, limit,
        )
    return [
        {
            "tg_id": int(r["tg_id"]),
            "username": r["username"],
            "first_name": r["first_name"],
            "photo_url": r["photo_url"],
            "trylla": int(r["trylla"]),
            "portfolio_value": int(r["portfolio_value"] or 0),
            "total_value": int(r["trylla"]) + int(r["portfolio_value"] or 0),
            "level": int(r["level"]),
            "total_trades": int(r["total_trades"]),
            "total_realized_pl": int(r["total_realized_pl"]),
            "win_count": int(r["win_count"]),
            "loss_count": int(r["loss_count"]),
            "win_rate": round(int(r["win_count"]) / max(1, int(r["win_count"]) + int(r["loss_count"])) * 100, 1),
            "subscriber_count": int(r["subscriber_count"]),
            "portfolio_privacy": r["portfolio_privacy"],
        }
        for r in rows
    ]


async def get_other_portfolio(viewer_id: int, target_id: int) -> dict:
    """Show another player's portfolio if:
    - target's portfolio_privacy = 'public', OR
    - viewer has active subscription to target."""
    async with pool().acquire() as conn:
        target = await conn.fetchrow(
            "select * from market_users where tg_id = $1", target_id,
        )
        if target is None:
            return {"ok": False, "error": "Игрок не найден"}
        privacy = target["portfolio_privacy"]
        # Viewer's own profile is always visible
        if viewer_id == target_id:
            return await get_state(target_id)
        if privacy != "public":
            # Check subscription
            sub = await conn.fetchrow(
                "select expires_at from market_subscriptions "
                "where subscriber_id = $1 and target_id = $2 and expires_at > now() "
                "order by expires_at desc limit 1",
                viewer_id, target_id,
            )
            if sub is None:
                return {"ok": False, "error": "Профиль приватный — нужна подписка"}
        # Allowed — return state
        return await get_state(target_id)


async def buy_subscription(viewer_id: int, target_id: int) -> dict:
    """Pay SUB_PRICE_24H TRYLLA to view target's portfolio for 24h."""
    if viewer_id == target_id:
        return {"ok": False, "error": "Нельзя подписаться на себя"}
    await ensure_user(viewer_id)
    await ensure_user(target_id)
    async with pool().acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow(
                "select trylla from market_users where tg_id = $1 for update", viewer_id,
            )
            if user is None or int(user["trylla"]) < SUB_PRICE_24H:
                return {"ok": False, "error": "Недостаточно TRYLLA",
                        "need": SUB_PRICE_24H}
            now = datetime.now(timezone.utc)
            expires = now + timedelta(hours=24)
            await conn.execute(
                "update market_users set trylla = trylla - $2 where tg_id = $1",
                viewer_id, SUB_PRICE_24H,
            )
            # Target gets 80% as kickback
            kickback = int(SUB_PRICE_24H * 0.8)
            await conn.execute(
                "update market_users set trylla = trylla + $2, "
                "subscriber_count = subscriber_count + 1 where tg_id = $1",
                target_id, kickback,
            )
            await conn.execute(
                "insert into market_subscriptions "
                "(subscriber_id, target_id, paid, starts_at, expires_at) "
                "values ($1,$2,$3,$4,$5)",
                viewer_id, target_id, SUB_PRICE_24H, now, expires,
            )
    return {"ok": True, "expires_at": expires.isoformat(),
            "paid": SUB_PRICE_24H, "kickback_to_target": kickback}


# ============================================================
# BANK / LOANS — спасательный круг для разорившихся
# ============================================================
#
# Mechanics:
# - Игрок может взять кредит до LOAN_MAX_BY_LEVEL[level] TRYLLA
# - Daily interest: 5% (компаундится)
# - Срок: 7 дней по умолчанию
# - После просрочки: +10%/день штраф (на текущий долг)
# - Можно погашать частично или полностью в любой момент
# - Несколько одновременных кредитов разрешены (но с общим cap)
# - Если total долг > 10× стартового капитала — нельзя брать новый

LOAN_DAILY_RATE = 0.05            # 5% в день
LOAN_OVERDUE_RATE = 0.10          # +10%/день штраф за просрочку
LOAN_DEFAULT_TERM_DAYS = 7
# Максимум суммарного долга по уровню игрока
LOAN_MAX_BY_LEVEL = {
    1: 5_000_00,        # 5K TRYLLA на старте
    5: 25_000_00,       # 25K на 5 уровне
    10: 100_000_00,     # 100K на 10
    20: 500_000_00,     # 500K
    30: 2_000_000_00,   # 2M
    50: 10_000_000_00,  # 10M endgame
}


def loan_max_for_level(level: int) -> int:
    """Linear interpolation between level breakpoints."""
    keys = sorted(LOAN_MAX_BY_LEVEL.keys())
    if level <= keys[0]: return LOAN_MAX_BY_LEVEL[keys[0]]
    if level >= keys[-1]: return LOAN_MAX_BY_LEVEL[keys[-1]]
    # Find bracket
    for i in range(len(keys) - 1):
        a, b = keys[i], keys[i+1]
        if a <= level <= b:
            va, vb = LOAN_MAX_BY_LEVEL[a], LOAN_MAX_BY_LEVEL[b]
            t = (level - a) / (b - a)
            return int(va + (vb - va) * t)
    return LOAN_MAX_BY_LEVEL[keys[-1]]


def _current_debt(loan_row) -> int:
    """Total amount due on this loan = principal + accrued_interest - repaid."""
    return int(loan_row["principal"]) + int(loan_row["accrued_interest"]) - int(loan_row["repaid"])


async def get_bank_state(tg_id: int) -> dict:
    """Состояние банка: текущие кредиты, доступный лимит, всё что нужно UI."""
    await ensure_user(tg_id)
    async with pool().acquire() as conn:
        user = await conn.fetchrow(
            "select level, trylla from market_users where tg_id = $1", tg_id,
        )
        loans = await conn.fetch(
            "select * from market_loans where user_id = $1 and status = 'active' "
            "order by taken_at desc",
            tg_id,
        )
        total_active_debt = await conn.fetchval(
            "select coalesce(sum(principal + accrued_interest - repaid), 0) "
            "from market_loans where user_id = $1 and status = 'active'",
            tg_id,
        )
    level = int(user["level"]) if user else 1
    max_debt = loan_max_for_level(level)
    available_credit = max(0, max_debt - int(total_active_debt or 0))

    return {
        "trylla": int(user["trylla"]) if user else 0,
        "level": level,
        "max_total_debt": max_debt,
        "current_total_debt": int(total_active_debt or 0),
        "available_credit": available_credit,
        "daily_rate": LOAN_DAILY_RATE,
        "overdue_rate": LOAN_OVERDUE_RATE,
        "default_term_days": LOAN_DEFAULT_TERM_DAYS,
        "active_loans": [
            {
                "id": int(l["id"]),
                "principal": int(l["principal"]),
                "daily_rate": float(l["daily_rate"]),
                "days_accrued": int(l["days_accrued"]),
                "accrued_interest": int(l["accrued_interest"]),
                "repaid": int(l["repaid"]),
                "current_debt": _current_debt(l),
                "taken_at": l["taken_at"].isoformat(),
                "due_at": l["due_at"].isoformat(),
                "overdue_days": int(l["overdue_days"]),
                "is_overdue": int(l["overdue_days"]) > 0,
            }
            for l in loans
        ],
    }


async def take_loan(tg_id: int, amount: int) -> dict:
    """Взять кредит. Деньги зачисляются на TRYLLA баланс мгновенно."""
    if amount <= 0:
        return {"ok": False, "error": "Сумма должна быть > 0"}
    await ensure_user(tg_id)
    async with pool().acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow(
                "select level, trylla from market_users where tg_id = $1 for update", tg_id,
            )
            if user is None:
                return {"ok": False, "error": "Нет состояния"}
            level = int(user["level"])
            max_debt = loan_max_for_level(level)
            cur_debt = await conn.fetchval(
                "select coalesce(sum(principal + accrued_interest - repaid), 0) "
                "from market_loans where user_id = $1 and status = 'active'",
                tg_id,
            )
            cur_debt = int(cur_debt or 0)
            if cur_debt + amount > max_debt:
                return {"ok": False, "error": f"Превышен лимит. Доступно: {max(0, max_debt - cur_debt)}",
                        "available": max(0, max_debt - cur_debt)}

            now = datetime.now(timezone.utc)
            due = now + timedelta(days=LOAN_DEFAULT_TERM_DAYS)
            await conn.execute(
                "insert into market_loans (user_id, principal, daily_rate, taken_at, due_at) "
                "values ($1, $2, $3, $4, $5)",
                tg_id, amount, LOAN_DAILY_RATE, now, due,
            )
            await conn.execute(
                "update market_users set trylla = trylla + $2 where tg_id = $1",
                tg_id, amount,
            )
    return {"ok": True, "amount": amount, "due_at": due.isoformat(),
            "daily_rate": LOAN_DAILY_RATE}


async def repay_loan(tg_id: int, loan_id: int, amount: int) -> dict:
    """Погасить часть/весь кредит. Если погашен полностью → status='paid'."""
    if amount <= 0:
        return {"ok": False, "error": "Сумма должна быть > 0"}
    await ensure_user(tg_id)
    async with pool().acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow(
                "select trylla from market_users where tg_id = $1 for update", tg_id,
            )
            if user is None or int(user["trylla"]) < amount:
                return {"ok": False, "error": "Не хватает TRYLLA"}

            loan = await conn.fetchrow(
                "select * from market_loans where id = $1 and user_id = $2 and status = 'active' "
                "for update", loan_id, tg_id,
            )
            if loan is None:
                return {"ok": False, "error": "Кредит не найден"}

            current_debt = _current_debt(loan)
            pay = min(amount, current_debt)
            new_repaid = int(loan["repaid"]) + pay
            new_status = "paid" if pay >= current_debt else "active"

            await conn.execute(
                "update market_users set trylla = trylla - $2 where tg_id = $1",
                tg_id, pay,
            )
            await conn.execute(
                "update market_loans set repaid = $2, status = $3 where id = $1",
                loan_id, new_repaid, new_status,
            )
    return {"ok": True, "paid": pay, "loan_status": new_status,
            "remaining_debt": max(0, current_debt - pay)}


async def daily_loan_accrual() -> None:
    """Запускается раз в день. Начисляет проценты на все активные кредиты,
    помечает просрочки. Если игрок не платит — долг компаундится."""
    log.info("market: daily loan accrual starting")
    async with pool().acquire() as conn:
        loans = await conn.fetch(
            "select id, principal, daily_rate, days_accrued, accrued_interest, "
            "repaid, due_at, overdue_days from market_loans where status = 'active'"
        )
        now = datetime.now(timezone.utc)
        for l in loans:
            cur_total = int(l["principal"]) + int(l["accrued_interest"]) - int(l["repaid"])
            if cur_total <= 0:
                # Auto-paid (rare but handle)
                await conn.execute(
                    "update market_loans set status = 'paid' where id = $1", int(l["id"]),
                )
                continue

            # Daily interest на текущий долг
            interest = int(cur_total * float(l["daily_rate"]))
            new_days = int(l["days_accrued"]) + 1
            new_overdue = int(l["overdue_days"])

            # Просрочка
            if l["due_at"] and now > l["due_at"]:
                new_overdue += 1
                # Дополнительный штраф за просрочку
                penalty = int(cur_total * LOAN_OVERDUE_RATE)
                interest += penalty

            await conn.execute(
                "update market_loans set "
                "  accrued_interest = accrued_interest + $2, "
                "  days_accrued = $3, "
                "  overdue_days = $4 "
                "where id = $1",
                int(l["id"]), interest, new_days, new_overdue,
            )
    log.info("market: daily loan accrual done — %d loans processed", len(loans))


# ============================================================
# LEVEL / XP
# ============================================================

def xp_needed_for(level: int) -> int:
    """Slow XP curve — earning level 100 takes a long time."""
    if level < 1: return 0
    return int(150 * (level ** 1.85))   # bigger exponent = slower


def level_for_xp(xp: int) -> int:
    if xp < 0: return 1
    lvl = 1
    while xp >= xp_needed_for(lvl):
        lvl += 1
        if lvl > 200: break
    return lvl


# ============================================================
# BACKGROUND LOOPS
# ============================================================

async def price_loop() -> None:
    """Tick prices every PRICE_TICK_SECONDS seconds. Cheap."""
    import asyncio
    log.info("market price loop started (tick %ss)", PRICE_TICK_SECONDS)
    while True:
        try:
            await asyncio.sleep(PRICE_TICK_SECONDS)
            await price_tick()
        except Exception:
            log.exception("market price tick failed")


async def news_loop() -> None:
    """Spawn news at irregular intervals + check cascades + roll major events."""
    import asyncio
    log.info("market news loop started")
    while True:
        try:
            wait = random.randint(NEWS_SPAWN_MIN_SEC, NEWS_SPAWN_MAX_SEC)
            await asyncio.sleep(wait)
            await news_spawn_tick()
            await cascade_check_tick()
            await major_event_tick()
        except Exception:
            log.exception("market news tick failed")


async def whale_loop() -> None:
    """Whale activity ticker — every 30 sec."""
    import asyncio
    log.info("market whale loop started")
    while True:
        try:
            await asyncio.sleep(30)
            await whale_tick()
        except Exception:
            log.exception("market whale tick failed")


async def daily_reset_loop() -> None:
    """Reset 24h high/low/open at midnight UTC + accrue daily loan interest."""
    import asyncio
    while True:
        try:
            now = datetime.now(timezone.utc)
            tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=10, microsecond=0)
            wait = (tomorrow - now).total_seconds()
            await asyncio.sleep(max(60, wait))
            async with pool().acquire() as conn:
                await conn.execute(
                    "update market_assets set "
                    "open_24h = current_price, high_24h = current_price, low_24h = current_price"
                )
            log.info("market: 24h stats reset")
            try:
                await daily_loan_accrual()
            except Exception:
                log.exception("market loan accrual failed")
        except Exception:
            log.exception("market daily reset failed")
