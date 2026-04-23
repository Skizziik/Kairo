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
    from app.economy import retention as rt
    leveled = await rt.grant_xp(tg_id, "daily")
    await rt.track_mission_progress(tg_id, "dailies", 1)
    achievements = await rt.check_achievements_after_action(tg_id, "daily")
    if leveled.get("leveled_up"):
        achievements += await rt.check_achievements_after_action(tg_id, "level_up")
    return {"ok": True, "amount": payout, "streak": streak, "next_in_seconds": 0,
            "new_balance": new_bal, "level": leveled, "achievements": achievements}


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

    # Retention hooks (outside transaction to avoid blocking)
    from app.economy import retention as rt
    leveled = await rt.grant_xp(user_id, "case_open")
    await rt.track_mission_progress(user_id, "cases", 1)
    await rt.pvp_track(user_id, "cases_opened", 1)
    achievements = await rt.check_achievements_after_action(
        user_id, "case_open",
        {"rarity": skin["rarity"], "category": skin["category"]},
    )
    if leveled.get("leveled_up"):
        achievements += await rt.check_achievements_after_action(user_id, "level_up")

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
        "achievements": achievements,
        "level": leveled,
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


# ============ sell to dealer ============

DEALER_PRICE_FRACTION = 0.7  # sell = 70% of listed price


async def sell_bulk_to_dealer(user_id: int, inventory_ids: list[int]) -> dict:
    """Sell many items in one atomic transaction. Returns total payout + count."""
    if not inventory_ids:
        return {"ok": False, "error": "Empty list"}
    unique_ids = list({int(x) for x in inventory_ids})
    total_payout = 0
    sold_count = 0
    async with pool().acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                "select id, price, locked from economy_inventory "
                "where id = any($1::bigint[]) and user_id = $2 for update",
                unique_ids, user_id,
            )
            to_delete = []
            for r in rows:
                if r["locked"]:
                    continue
                to_delete.append(int(r["id"]))
                total_payout += int(round(int(r["price"]) * DEALER_PRICE_FRACTION))
                sold_count += 1
            if not to_delete:
                return {"ok": False, "error": "Nothing to sell"}
            await conn.execute(
                "delete from economy_inventory where id = any($1::bigint[])",
                to_delete,
            )
            new_bal_row = await conn.fetchrow(
                "update economy_users set balance = balance + $2, total_earned = total_earned + $2 "
                "where tg_id = $1 returning balance",
                user_id, total_payout,
            )
            new_bal = int(new_bal_row["balance"])
            await conn.execute(
                "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                "values ($1, $2, 'sell_bulk', $3, $4)",
                user_id, total_payout, f"{sold_count} items", new_bal,
            )
    return {"ok": True, "payout": total_payout, "sold_count": sold_count, "new_balance": new_bal}


async def sell_to_dealer(user_id: int, inventory_id: int) -> dict:
    """Sell item to bot dealer. Returns payout."""
    async with pool().acquire() as conn:
        async with conn.transaction():
            item = await conn.fetchrow(
                "select id, user_id, price, locked from economy_inventory "
                "where id = $1 for update",
                inventory_id,
            )
            if item is None:
                return {"ok": False, "error": "Item not found"}
            if int(item["user_id"]) != user_id:
                return {"ok": False, "error": "Not your item"}
            if item["locked"]:
                return {"ok": False, "error": "Item is locked (on market or in trade)"}
            payout = max(1, int(round(int(item["price"]) * DEALER_PRICE_FRACTION)))
            await conn.execute("delete from economy_inventory where id = $1", inventory_id)
            new_bal_row = await conn.fetchrow(
                "update economy_users set balance = balance + $2, total_earned = total_earned + $2 "
                "where tg_id = $1 returning balance",
                user_id, payout,
            )
            new_bal = int(new_bal_row["balance"])
            await conn.execute(
                "insert into economy_transactions (user_id, amount, kind, reason, ref_id, balance_after) "
                "values ($1, $2, 'sell', 'dealer', $3, $4)",
                user_id, payout, inventory_id, new_bal,
            )
    from app.economy import retention as rt
    await rt.track_mission_progress(user_id, "sells", 1)
    achievements = await rt.check_achievements_after_action(user_id, "sell")
    if int(new_bal) >= 1_000_000:
        achievements += await rt.check_achievements_after_action(user_id, "balance")
    return {"ok": True, "payout": payout, "new_balance": new_bal, "achievements": achievements}


# ============ upgrade minigame ============

async def upgrade_item(user_id: int, inventory_id: int, target_skin_id: int, extra_coins: int = 0) -> dict:
    """Gamble an item + extra coins to upgrade into a target skin."""
    import random
    from app.economy.pricing import compute_price, wear_from_float, roll_float

    if extra_coins < 0:
        return {"ok": False, "error": "Invalid coins"}
    async with pool().acquire() as conn:
        async with conn.transaction():
            bal_row = await conn.fetchrow(
                "select balance from economy_users where tg_id = $1 for update",
                user_id,
            )
            if bal_row is None or int(bal_row["balance"]) < extra_coins:
                return {"ok": False, "error": "Not enough coins"}

            item = await conn.fetchrow(
                "select id, user_id, price, locked, skin_id from economy_inventory "
                "where id = $1 for update",
                inventory_id,
            )
            if item is None or int(item["user_id"]) != user_id:
                return {"ok": False, "error": "Item not yours"}
            if item["locked"]:
                return {"ok": False, "error": "Item locked"}

            target = await conn.fetchrow(
                "select id, full_name, weapon, skin_name, rarity, rarity_color, image_url, "
                "base_price, min_float, max_float, category, stat_trak_available "
                "from economy_skins_catalog where id = $1 and active",
                target_skin_id,
            )
            if target is None:
                return {"ok": False, "error": "Target skin not found"}

            stake_value = int(item["price"]) + int(extra_coins)
            target_median_price = int(target["base_price"])
            # Target is worth more (or equal) to be a real upgrade
            if target_median_price <= stake_value:
                return {"ok": False, "error": "Target must be more valuable than stake"}

            # Probability — always < 1, with 10% house edge
            probability = (stake_value / target_median_price) * 0.90
            probability = max(0.02, min(0.95, probability))
            success = random.random() < probability

            # Deduct extra coins upfront (either way)
            if extra_coins > 0:
                await conn.execute(
                    "update economy_users set balance = balance - $2, total_spent = total_spent + $2 "
                    "where tg_id = $1",
                    user_id, extra_coins,
                )
                await conn.execute(
                    "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                    "select $1, $2, 'upgrade', 'stake', balance from economy_users where tg_id = $1",
                    user_id, -extra_coins,
                )

            if success:
                await conn.execute("delete from economy_inventory where id = $1", inventory_id)
                fl = roll_float(float(target["min_float"]), float(target["max_float"]))
                wear_name, _ = wear_from_float(fl)
                new_price = compute_price(int(target["base_price"]), fl, wear_name, False)
                new_inv = await conn.fetchrow(
                    "insert into economy_inventory "
                    "  (user_id, skin_id, float_value, wear, stat_trak, price, source, source_ref) "
                    "values ($1, $2, $3, $4, false, $5, 'upgrade', $6) returning id",
                    user_id, int(target["id"]), fl, wear_name, new_price, str(inventory_id),
                )
            else:
                await conn.execute("delete from economy_inventory where id = $1", inventory_id)
            bal_row2 = await conn.fetchrow(
                "select balance from economy_users where tg_id = $1", user_id
            )
    # --- outside txn/pool context: retention hooks ---
    from app.economy import retention as rt
    await rt.track_mission_progress(user_id, "upgrade_attempts", 1)
    if success:
        await rt.bump_stat_counter(user_id, "upgrades_won", 1)
        leveled = await rt.grant_xp(user_id, "upgrade_win")
        await rt.pvp_track(user_id, "upgrades_won", 1)
        achievements = await rt.check_achievements_after_action(
            user_id, "upgrade", {"success": True},
        )
        if leveled.get("leveled_up"):
            achievements += await rt.check_achievements_after_action(user_id, "level_up")
        return {
            "ok": True,
            "success": True,
            "probability": probability,
            "new_item": {
                "id": int(new_inv["id"]),
                "name": target["full_name"],
                "weapon": target["weapon"],
                "skin_name": target["skin_name"],
                "rarity": target["rarity"],
                "rarity_color": target["rarity_color"],
                "image_url": target["image_url"],
                "float": fl,
                "wear": wear_name,
                "price": new_price,
            },
            "new_balance": int(bal_row2["balance"]),
            "level": leveled,
            "achievements": achievements,
        }
    return {
        "ok": True,
        "success": False,
        "probability": probability,
        "new_balance": int(bal_row2["balance"]),
    }


# ============ casino ============

async def play_coinflip(user_id: int, bet: int, side: str) -> dict:
    """50/50 doubler. side = 'heads' or 'tails'."""
    import random
    if bet <= 0:
        return {"ok": False, "error": "Bet must be positive"}
    if side not in ("heads", "tails"):
        return {"ok": False, "error": "Invalid side"}
    async with pool().acquire() as conn:
        async with conn.transaction():
            bal_row = await conn.fetchrow(
                "select balance from economy_users where tg_id = $1 for update",
                user_id,
            )
            if bal_row is None or int(bal_row["balance"]) < bet:
                return {"ok": False, "error": "Not enough coins"}
            actual = random.choice(["heads", "tails"])
            win = (actual == side)
            # Pay 1.95x on win → 2.5% house edge, preserves "double" feel.
            delta = int(bet * 0.95) if win else -bet
            new_bal_row = await conn.fetchrow(
                "update economy_users set balance = balance + $2, "
                "total_earned = total_earned + greatest($2, 0), "
                "total_spent = total_spent + greatest(-$2, 0) "
                "where tg_id = $1 returning balance",
                user_id, delta,
            )
            new_bal = int(new_bal_row["balance"])
            await conn.execute(
                "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                "values ($1, $2, 'casino', $3, $4)",
                user_id, delta, f"coinflip_{side}_vs_{actual}", new_bal,
            )
    from app.economy import retention as rt
    leveled = await rt.grant_xp(user_id, "coinflip")
    await rt.track_mission_progress(user_id, "coinflips", 1)
    if win:
        await rt.pvp_track(user_id, "coinflip_won", 1)
        await rt.pvp_track(user_id, "total_winnings", delta)
        await rt.increment_stattrak_kills_on_win(user_id, 1)
    achievements = []
    if int(new_bal) >= 1_000_000:
        achievements = await rt.check_achievements_after_action(user_id, "balance")
    if leveled.get("leveled_up"):
        achievements += await rt.check_achievements_after_action(user_id, "level_up")
    return {"ok": True, "win": win, "result": actual, "delta": delta, "new_balance": new_bal,
            "level": leveled, "achievements": achievements}


SLOT_SYMBOLS = ["💀", "🔫", "💣", "💎", "🏆", "7️⃣"]
# three-of-a-kind multipliers — calibrated for ~7% house edge with NO pair payout.
SLOT_PAYOUTS = {
    "💀": 10,
    "🔫": 15,
    "💣": 20,
    "💎": 30,
    "🏆": 50,
    "7️⃣": 200,
}


async def play_slots(user_id: int, bet: int) -> dict:
    import random
    if bet <= 0:
        return {"ok": False, "error": "Bet must be positive"}
    async with pool().acquire() as conn:
        async with conn.transaction():
            bal_row = await conn.fetchrow(
                "select balance from economy_users where tg_id = $1 for update", user_id,
            )
            if bal_row is None or int(bal_row["balance"]) < bet:
                return {"ok": False, "error": "Not enough coins"}
            reels = [random.choice(SLOT_SYMBOLS) for _ in range(3)]
            payout = 0
            outcome = "lose"
            if reels[0] == reels[1] == reels[2]:
                payout = bet * SLOT_PAYOUTS[reels[0]]
                outcome = "jackpot"
            # No small "pair" payout — keeps house edge healthy (~7%).
            delta = payout - bet
            new_bal_row = await conn.fetchrow(
                "update economy_users set balance = balance + $2, "
                "total_earned = total_earned + greatest($2, 0), "
                "total_spent = total_spent + greatest(-$2, 0) "
                "where tg_id = $1 returning balance",
                user_id, delta,
            )
            new_bal = int(new_bal_row["balance"])
            await conn.execute(
                "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                "values ($1, $2, 'casino', $3, $4)",
                user_id, delta, f"slots_{''.join(reels)}_{outcome}", new_bal,
            )
    from app.economy import retention as rt
    await rt.bump_stat_counter(user_id, "slots_spins", 1)
    leveled = await rt.grant_xp(user_id, "slot_jackpot" if outcome == "jackpot" else "slot_spin")
    await rt.track_mission_progress(user_id, "slot_spins", 1)
    if outcome == "jackpot":
        await rt.bump_stat_counter(user_id, "slots_jackpots", 1)
        await rt.track_mission_progress(user_id, "slot_jackpots", 1)
        await rt.pvp_track(user_id, "slots_jackpots", 1)
    if delta > 0:
        await rt.pvp_track(user_id, "slots_won", 1)
        await rt.pvp_track(user_id, "total_winnings", delta)
        await rt.increment_stattrak_kills_on_win(user_id, 1)
    achievements = await rt.check_achievements_after_action(
        user_id, "slot_spin", {"jackpot": outcome == "jackpot"},
    )
    if int(new_bal) >= 1_000_000:
        achievements += await rt.check_achievements_after_action(user_id, "balance")
    if leveled.get("leveled_up"):
        achievements += await rt.check_achievements_after_action(user_id, "level_up")
    return {
        "ok": True,
        "reels": reels,
        "outcome": outcome,
        "delta": delta,
        "payout": payout,
        "bet": bet,
        "new_balance": new_bal,
        "level": leveled,
        "achievements": achievements,
    }


async def play_crash(user_id: int, bet: int, target_mult: float) -> dict:
    """Player sets target multiplier. Server rolls crash point.
    Win if target <= crash_point."""
    import random, math
    if bet <= 0 or target_mult < 1.01:
        return {"ok": False, "error": "Bet > 0, target >= 1.01"}
    if target_mult > 50:
        return {"ok": False, "error": "Max target 50x"}
    async with pool().acquire() as conn:
        async with conn.transaction():
            bal_row = await conn.fetchrow(
                "select balance from economy_users where tg_id = $1 for update", user_id,
            )
            if bal_row is None or int(bal_row["balance"]) < bet:
                return {"ok": False, "error": "Not enough coins"}
            # House edge 5%: crash_point distribution gives avg ~0.95x on naive 1/u random
            # Implementation: u ~ Uniform(0,1), crash_point = max(1.00, 0.95 / (1 - u))
            # Equivalent to common crash games
            # Stable 5% house edge: P(crash >= T) = 0.95/T.
            # crash_point = 0.95 / (1 - u), u ~ U(0, 1).
            u = random.random()
            denom = max(1e-6, 1 - u)
            crash_point = max(1.00, min(100.0, 0.95 / denom))
            crash_point = round(crash_point, 2)
            win = target_mult <= crash_point
            payout = int(bet * target_mult) if win else 0
            delta = payout - bet
            new_bal_row = await conn.fetchrow(
                "update economy_users set balance = balance + $2, "
                "total_earned = total_earned + greatest($2, 0), "
                "total_spent = total_spent + greatest(-$2, 0) "
                "where tg_id = $1 returning balance",
                user_id, delta,
            )
            new_bal = int(new_bal_row["balance"])
            await conn.execute(
                "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                "values ($1, $2, 'casino', $3, $4)",
                user_id, delta, f"crash_t{target_mult}_c{crash_point}_{'W' if win else 'L'}", new_bal,
            )
    from app.economy import retention as rt
    leveled = None
    achievements = []
    if win:
        leveled = await rt.grant_xp(user_id, "crash_win")
        await rt.track_mission_progress(user_id, "crash_wins", 1)
        await rt.pvp_track(user_id, "crash_profit", delta)
        await rt.pvp_track(user_id, "total_winnings", delta)
        await rt.increment_stattrak_kills_on_win(user_id, 1)
    if int(new_bal) >= 1_000_000:
        achievements = await rt.check_achievements_after_action(user_id, "balance")
    if leveled and leveled.get("leveled_up"):
        achievements += await rt.check_achievements_after_action(user_id, "level_up")
    return {
        "ok": True,
        "win": win,
        "target": target_mult,
        "crash_point": crash_point,
        "delta": delta,
        "payout": payout,
        "new_balance": new_bal,
        "level": leveled,
        "achievements": achievements,
    }


# ============ daily task ============

TASK_POOL = [
    # (kind, prompt_template, answer_fn, reward)
]


def _make_math_task() -> dict:
    import random
    a = random.randint(12, 99)
    b = random.randint(3, 15)
    op = random.choice(["+", "-", "*"])
    if op == "+":
        q = f"Сколько будет {a} + {b}?"
        ans = a + b
    elif op == "-":
        q = f"Сколько будет {a} − {b}?"
        ans = a - b
    else:
        q = f"Сколько будет {a} × {b}?"
        ans = a * b
    return {"kind": "math", "prompt": q, "answer": str(ans), "reward": 120}


def _make_cs_trivia_task() -> dict:
    import random
    pool = [
        ("Сколько секунд после плана C4 до взрыва?", "40", 150),
        ("Сколько раундов до победы в премьер CS2?", "13", 150),
        ("Максимум патронов в магазине AWP?", "10", 180),
        ("Сколько стоит AK-47 в CS2?", "2700", 180),
        ("Сколько стоит AWP в CS2?", "4750", 180),
        ("Какая нация у s1mple (по стране)?", "украина", 200),
        ("Как называется главная точка плана B на Mirage одним словом?", "b", 100),
        ("Сколько игроков в команде в премьер матче CS2?", "5", 120),
    ]
    q, a, r = random.choice(pool)
    return {"kind": "cs_trivia", "prompt": q, "answer": a.lower(), "reward": r}


def _make_logic_task() -> dict:
    import random
    pool = [
        ("У тебя 3 флэшки. Ты кинул 2. Сколько осталось?", "1", 120),
        ("Мать тильтит 20 минут. Ты - 40. На сколько ты тильтишь дольше?", "20", 120),
        ("Раунд длится 1:55. Прошло 1:40. Сколько осталось (в секундах)?", "15", 150),
    ]
    q, a, r = random.choice(pool)
    return {"kind": "logic", "prompt": q, "answer": a.lower(), "reward": r}


def _new_task() -> dict:
    import random
    return random.choice([_make_math_task, _make_cs_trivia_task, _make_logic_task])()


async def get_or_create_daily_task(user_id: int) -> dict:
    from datetime import date as _date
    today = _date.today()
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "select kind, prompt, answer, reward, solved, attempts from economy_tasks "
            "where user_id = $1 and day = $2",
            user_id, today,
        )
        if row is None:
            task = _new_task()
            await conn.execute(
                "insert into economy_tasks (user_id, day, kind, prompt, answer, reward) "
                "values ($1, $2, $3, $4, $5, $6) "
                "on conflict (user_id) do update set "
                "  day = excluded.day, kind = excluded.kind, prompt = excluded.prompt, "
                "  answer = excluded.answer, reward = excluded.reward, "
                "  solved = false, attempts = 0, created_at = now(), solved_at = null",
                user_id, today, task["kind"], task["prompt"], task["answer"], task["reward"],
            )
            return {"kind": task["kind"], "prompt": task["prompt"], "reward": task["reward"], "solved": False, "attempts": 0}
        return {
            "kind": row["kind"],
            "prompt": row["prompt"],
            "reward": int(row["reward"]),
            "solved": bool(row["solved"]),
            "attempts": int(row["attempts"]),
        }


async def submit_daily_task(user_id: int, answer: str) -> dict:
    from datetime import date as _date
    today = _date.today()
    normalized = (answer or "").strip().lower()
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select kind, prompt, answer, reward, solved, attempts "
                "from economy_tasks where user_id = $1 and day = $2 for update",
                user_id, today,
            )
            if row is None:
                return {"ok": False, "error": "No task today — call /task first"}
            if row["solved"]:
                return {"ok": False, "error": "Already solved", "correct": True}
            if int(row["attempts"]) >= 5:
                return {"ok": False, "error": "Too many attempts today"}

            correct = normalized == row["answer"]
            await conn.execute(
                "update economy_tasks set attempts = attempts + 1, "
                "solved = $2, solved_at = case when $2 then now() else solved_at end "
                "where user_id = $1 and day = $3",
                user_id, correct, today,
            )
            if correct:
                reward = int(row["reward"])
                new_bal_row = await conn.fetchrow(
                    "update economy_users set balance = balance + $2, total_earned = total_earned + $2 "
                    "where tg_id = $1 returning balance",
                    user_id, reward,
                )
                new_bal = int(new_bal_row["balance"])
                await conn.execute(
                    "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                    "values ($1, $2, 'task', 'daily', $3)",
                    user_id, reward, new_bal,
                )
    # outside tx — retention hooks only on correct answer
    if correct:
        from app.economy import retention as rt
        leveled = await rt.grant_xp(user_id, "task")
        await rt.track_mission_progress(user_id, "tasks", 1)
        achievements = []
        if leveled.get("leveled_up"):
            achievements = await rt.check_achievements_after_action(user_id, "level_up")
        return {"ok": True, "correct": True, "reward": reward, "new_balance": new_bal,
                "level": leveled, "achievements": achievements}
    return {"ok": True, "correct": False, "attempts_left": max(0, 5 - int(row["attempts"]) - 1)}


# ============ quiz rewards ============

async def register_quiz(poll_id: str, correct_option_id: int, reward: int = 20) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "insert into economy_quizzes (poll_id, correct_option_id, reward) "
            "values ($1, $2, $3) on conflict (poll_id) do nothing",
            poll_id, correct_option_id, reward,
        )


async def handle_quiz_answer(poll_id: str, user_id: int, option_ids: list[int]) -> dict:
    """Credit user if they answered a registered quiz correctly, once per quiz."""
    async with pool().acquire() as conn:
        async with conn.transaction():
            quiz = await conn.fetchrow(
                "select correct_option_id, reward from economy_quizzes where poll_id = $1",
                poll_id,
            )
            if quiz is None:
                return {"ok": False, "registered": False}
            # Idempotency
            already = await conn.fetchval(
                "select correct from economy_quiz_answers where poll_id = $1 and user_id = $2",
                poll_id, user_id,
            )
            correct = bool(option_ids and option_ids[0] == int(quiz["correct_option_id"]))
            if already is None:
                await conn.execute(
                    "insert into economy_quiz_answers (poll_id, user_id, correct) values ($1, $2, $3)",
                    poll_id, user_id, correct,
                )
            if not correct or already is not None:
                return {"ok": True, "correct": correct, "rewarded": False}
            reward = int(quiz["reward"])
            await ensure_user(user_id)
            new_bal_row = await conn.fetchrow(
                "update economy_users set balance = balance + $2, total_earned = total_earned + $2 "
                "where tg_id = $1 returning balance",
                user_id, reward,
            )
            new_bal = int(new_bal_row["balance"]) if new_bal_row else 0
            await conn.execute(
                "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                "values ($1, $2, 'quiz', $3, $4)",
                user_id, reward, poll_id, new_bal,
            )
            return {"ok": True, "correct": True, "rewarded": True, "reward": reward, "new_balance": new_bal}
