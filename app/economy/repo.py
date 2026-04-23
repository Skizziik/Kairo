"""Economy DB access — balances, inventory, cases, transactions."""
from __future__ import annotations

import json
import logging
import random
from datetime import date, datetime, timedelta, timezone

from app.db.client import pool
from app.economy.pricing import compute_price, roll_float, wear_from_float

log = logging.getLogger(__name__)

STREAK_RESET_HOURS = 36
BASE_DAILY = 150
STREAK_BONUS_PER_DAY = 20
STREAK_BONUS_CAP = 800


async def ensure_user(tg_id: int) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "insert into economy_users (tg_id) values ($1) on conflict do nothing",
            tg_id,
        )


async def get_user(tg_id: int) -> dict | None:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "select * from economy_users where tg_id = $1",
            tg_id,
        )
    return dict(row) if row else None


async def credit(tg_id: int, amount: int, kind: str, reason: str | None = None, ref_id: int | None = None) -> int:
    """Add coins to user balance. Returns new balance."""
    if amount == 0:
        user = await get_user(tg_id)
        return user["balance"] if user else 0
    await ensure_user(tg_id)
    async with pool().acquire() as conn:
        async with conn.transaction():
            if amount > 0:
                row = await conn.fetchrow(
                    "update economy_users set balance = balance + $2, total_earned = total_earned + $2 "
                    "where tg_id = $1 returning balance",
                    tg_id, amount,
                )
            else:
                row = await conn.fetchrow(
                    "update economy_users set balance = balance + $2, total_spent = total_spent - $2 "
                    "where tg_id = $1 returning balance",
                    tg_id, amount,
                )
            new_bal = int(row["balance"])
            await conn.execute(
                "insert into economy_transactions (user_id, amount, kind, reason, ref_id, balance_after) "
                "values ($1, $2, $3, $4, $5, $6)",
                tg_id, amount, kind, reason, ref_id, new_bal,
            )
    return new_bal


async def try_claim_daily(tg_id: int) -> dict:
    """Atomic daily claim. Returns {'ok': bool, 'amount': int, 'streak': int, 'next_in_seconds': int}."""
    await ensure_user(tg_id)
    now = datetime.now(timezone.utc)
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select last_daily_at, current_streak, best_streak from economy_users "
                "where tg_id = $1 for update",
                tg_id,
            )
            if row is None:
                return {"ok": False, "amount": 0, "streak": 0, "next_in_seconds": 0}

            last = row["last_daily_at"]
            streak = int(row["current_streak"] or 0)
            best = int(row["best_streak"] or 0)

            if last is not None:
                since = (now - last).total_seconds()
                if since < 23 * 3600:
                    remaining = int(23 * 3600 - since)
                    return {"ok": False, "amount": 0, "streak": streak, "next_in_seconds": remaining}
                if since > STREAK_RESET_HOURS * 3600:
                    streak = 0

            streak += 1
            best = max(best, streak)
            bonus = min(STREAK_BONUS_CAP, STREAK_BONUS_PER_DAY * (streak - 1))
            payout = BASE_DAILY + bonus

            new_bal_row = await conn.fetchrow(
                "update economy_users set "
                "  balance = balance + $2, "
                "  total_earned = total_earned + $2, "
                "  current_streak = $3, "
                "  best_streak = $4, "
                "  last_daily_at = $5 "
                "where tg_id = $1 returning balance",
                tg_id, payout, streak, best, now,
            )
            new_bal = int(new_bal_row["balance"])
            await conn.execute(
                "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                "values ($1, $2, 'daily', $3, $4)",
                tg_id, payout, f"streak={streak}, bonus={bonus}", new_bal,
            )
    return {"ok": True, "amount": payout, "streak": streak, "next_in_seconds": 0, "new_balance": new_bal}


async def grant_activity_coin(tg_id: int, per_day_cap: int = 30) -> bool:
    """Grant 1 coin if user hasn't hit daily activity cap. Called roughly every N messages."""
    await ensure_user(tg_id)
    today = date.today()
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select activity_earned_today, activity_day from economy_users where tg_id = $1 for update",
                tg_id,
            )
            if row is None:
                return False
            earned = int(row["activity_earned_today"] or 0)
            day = row["activity_day"]
            if day != today:
                earned = 0
            if earned >= per_day_cap:
                return False
            new_bal_row = await conn.fetchrow(
                "update economy_users set "
                "  balance = balance + 1, total_earned = total_earned + 1, "
                "  activity_earned_today = $2, activity_day = $3 "
                "where tg_id = $1 returning balance",
                tg_id, earned + 1, today,
            )
            new_bal = int(new_bal_row["balance"])
            await conn.execute(
                "insert into economy_transactions (user_id, amount, kind, balance_after) "
                "values ($1, 1, 'activity', $2)",
                tg_id, new_bal,
            )
    return True


async def list_cases() -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "select id, key, name, description, price, image_url from economy_cases "
            "where active order by price asc"
        )
    return [dict(r) for r in rows]


async def get_case(case_id: int) -> dict | None:
    async with pool().acquire() as conn:
        row = await conn.fetchrow("select * from economy_cases where id = $1", case_id)
    return dict(row) if row else None


async def open_case(user_id: int, case_id: int) -> dict:
    """Deduct price, roll loot, add to inventory. Returns full dropped item dict."""
    await ensure_user(user_id)
    case = await get_case(case_id)
    if case is None:
        return {"ok": False, "error": "Кейс не найден"}

    async with pool().acquire() as conn:
        async with conn.transaction():
            user_row = await conn.fetchrow(
                "select balance from economy_users where tg_id = $1 for update",
                user_id,
            )
            if user_row is None or int(user_row["balance"]) < case["price"]:
                return {"ok": False, "error": "Не хватает коинов"}

            # roll rarity
            pool_data = case["loot_pool"]
            if isinstance(pool_data, str):
                pool_data = json.loads(pool_data)
            rarity_weights: dict[str, float] = pool_data.get("rarity_weights", {})
            by_rarity: dict[str, list[int]] = pool_data.get("by_rarity", {})
            roll = random.random()
            cum = 0.0
            chosen_rarity = None
            # rarities must be iterated in fixed order (lowest to highest) for predictability
            rarity_order = ["mil-spec", "restricted", "classified", "covert", "exceedingly_rare", "consumer", "industrial"]
            # Use only rarities that have weight AND items
            available = [r for r in rarity_order if rarity_weights.get(r) and by_rarity.get(r)]
            if not available:
                return {"ok": False, "error": "Лут-пул пуст"}
            total_weight = sum(rarity_weights[r] for r in available)
            if total_weight <= 0:
                return {"ok": False, "error": "Кривой лут-пул"}
            for r in available:
                cum += rarity_weights[r] / total_weight
                if roll <= cum:
                    chosen_rarity = r
                    break
            if chosen_rarity is None:
                chosen_rarity = available[-1]

            # pick skin id from that rarity bucket
            candidates = by_rarity[chosen_rarity]
            skin_id = random.choice(candidates)

            # load skin details
            skin = await conn.fetchrow(
                "select id, full_name, weapon, skin_name, rarity, rarity_color, min_float, max_float, "
                "image_url, base_price, stat_trak_available, category "
                "from economy_skins_catalog where id = $1",
                skin_id,
            )
            # roll float within skin's range
            fl = roll_float(float(skin["min_float"]), float(skin["max_float"]))
            wear_name, _ = wear_from_float(fl)
            # stat-trak chance (0 if item doesn't support it)
            st_chance = float(case["stat_trak_chance"] or 0.0)
            st = skin["stat_trak_available"] and random.random() < st_chance

            price = compute_price(int(skin["base_price"]), fl, wear_name, st)

            # deduct balance
            new_bal_row = await conn.fetchrow(
                "update economy_users set "
                "  balance = balance - $2, total_spent = total_spent + $2, "
                "  cases_opened = cases_opened + 1 "
                "where tg_id = $1 returning balance",
                user_id, case["price"],
            )
            new_bal = int(new_bal_row["balance"])

            # record transaction (spend)
            await conn.execute(
                "insert into economy_transactions (user_id, amount, kind, reason, ref_id, balance_after) "
                "values ($1, $2, 'case', $3, $4, $5)",
                user_id, -int(case["price"]), case["name"], case["id"], new_bal,
            )

            # insert inventory item
            inv = await conn.fetchrow(
                "insert into economy_inventory "
                "  (user_id, skin_id, float_value, wear, stat_trak, price, source, source_ref) "
                "values ($1, $2, $3, $4, $5, $6, 'case', $7) "
                "returning id",
                user_id, int(skin["id"]), fl, wear_name, st, price, str(case["id"]),
            )

    return {
        "ok": True,
        "inventory_id": int(inv["id"]),
        "skin": {
            "id": int(skin["id"]),
            "full_name": skin["full_name"],
            "weapon": skin["weapon"],
            "skin_name": skin["skin_name"],
            "rarity": skin["rarity"],
            "rarity_color": skin["rarity_color"],
            "image_url": skin["image_url"],
            "category": skin["category"],
        },
        "float": fl,
        "wear": wear_name,
        "stat_trak": st,
        "price": price,
        "new_balance": new_bal,
        "case_name": case["name"],
    }


async def inventory_of(user_id: int, limit: int = 200) -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            select i.id, i.skin_id, i.float_value, i.wear, i.stat_trak, i.price, i.acquired_at, i.locked,
                   s.full_name, s.weapon, s.skin_name, s.rarity, s.rarity_color, s.image_url, s.category
            from economy_inventory i
            join economy_skins_catalog s on s.id = i.skin_id
            where i.user_id = $1
            order by s.base_price desc, i.acquired_at desc
            limit $2
            """,
            user_id, limit,
        )
    return [dict(r) for r in rows]


async def leaderboard_rich(limit: int = 10) -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            select e.tg_id, e.balance, e.cases_opened, e.current_streak,
                   u.username, u.first_name
            from economy_users e
            left join users u on u.tg_id = e.tg_id
            order by e.balance desc
            limit $1
            """,
            limit,
        )
    return [dict(r) for r in rows]
