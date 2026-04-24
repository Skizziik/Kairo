"""Retention hooks: achievements, levels, weekly missions, wheel, events, PvP stats."""
from __future__ import annotations

import json
import logging
import random
from datetime import date, datetime, timedelta, timezone

from app.db.client import pool

log = logging.getLogger(__name__)


# ============================================================
# LEVELS / XP
# ============================================================

# XP reward per action
XP_REWARDS = {
    "daily": 50,
    "task": 80,
    "quiz": 15,
    "slot_spin": 2,
    "slot_jackpot": 100,
    "coinflip": 3,
    "crash_win": 10,
    "case_open": 25,
    "upgrade_win": 60,
    "message": 1,
    "mission_complete": 150,
    "achievement": 200,
    "wheel_spin": 30,
}


def xp_for_level(level: int) -> int:
    """Total XP required to reach the given level (level 1 = 0)."""
    if level <= 1:
        return 0
    # Gentle quadratic curve: level 2 = 100, 3 = 250, 4 = 450, 5 = 700...
    return int(50 * level * (level - 1))


def level_from_xp(xp: int) -> int:
    level = 1
    while xp >= xp_for_level(level + 1):
        level += 1
        if level > 100:
            break
    return level


LEVEL_PERKS = {
    # level: human-readable perk descriptor
    2: "Доступ к колесу фортуны",
    5: "Unlock: 'Игорь — мид проебал' кейс",
    10: "Скидка 5% на кейсы",
    15: "+1 случайная крутка колеса в день",
    20: "Скидка 10% на кейсы",
    25: "Unlock: эксклюзивный кейс Легенда",
    40: "Скидка 15% на кейсы",
}


async def grant_xp(user_id: int, source: str, amount: int | None = None) -> dict:
    """Add XP. Returns {old_level, new_level, leveled_up, xp, next_level_xp}."""
    amt = amount if amount is not None else XP_REWARDS.get(source, 0)
    if amt <= 0:
        return {"leveled_up": False}
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "update economy_users set xp = xp + $2 "
            "where tg_id = $1 returning xp, level",
            user_id, amt,
        )
        if row is None:
            return {"leveled_up": False}
        xp = int(row["xp"])
        old_level = int(row["level"])
        new_level = level_from_xp(xp)
        if new_level > old_level:
            await conn.execute(
                "update economy_users set level = $2 where tg_id = $1",
                user_id, new_level,
            )
    return {
        "leveled_up": new_level > old_level,
        "old_level": old_level,
        "new_level": new_level,
        "xp": xp,
        "next_level_xp": xp_for_level(new_level + 1),
        "current_level_xp": xp_for_level(new_level),
        "perk": LEVEL_PERKS.get(new_level) if new_level > old_level else None,
    }


# ============================================================
# ACHIEVEMENTS
# ============================================================

# Each achievement: code → (name, description, check_fn, reward_coins, title)
ACHIEVEMENTS = {
    "first_case":        ("🎁 Первый кейс",      "Открыл свой первый кейс",                  100,  None),
    "ten_cases":         ("📦 Кейс-охотник",    "Открыл 10 кейсов",                         300,  None),
    "hundred_cases":     ("📦📦 Лудоман кейсов", "Открыл 100 кейсов",                       2000, "Лудоман"),
    "first_jackpot":     ("🎰 Джекпот!",        "Собрал три одинаковых в слотах",            500,  None),
    "ten_jackpots":      ("🎰 Слот-босс",       "Выбил 10 джекпотов в слотах",              3000, "Слот-Босс"),
    "hundred_spins":     ("🎰 Маньяк слотов",   "Крутнул слоты 100 раз",                    500,  None),
    "first_upgrade":     ("⚡ Апгрейд!",         "Успешно прокачал скин",                    250,  None),
    "ten_upgrades":      ("⚡⚡ Апгрейдер",      "Успешно прокачал 10 скинов",                2000, "Апгрейдер"),
    "millionaire":       ("💎 Миллионер",       "Накопил 1 000 000 коинов",                 5000, "Миллионер"),
    "streak_7":          ("🔥 Недельный",      "Стрик daily 7 дней подряд",                500,  None),
    "streak_30":         ("🔥🔥 Чёткий",       "Стрик daily 30 дней подряд",               5000, "Чёткий"),
    "first_covert":      ("🟥 Красненький",     "Выбил Covert-скин",                        300,  None),
    "first_knife":       ("🗡️ Нож!",            "Выбил нож или перчатки",                  2000, "Ножевой"),
    "first_trade":       ("🤝 Первый трейд",    "Обменял скин с кем-то",                   200,  None),
    "first_sell":        ("💰 Продал дилеру",   "Продал первый скин дилеру",               50,   None),
    "level_5":           ("⭐ Уровень 5",       "Достиг 5 уровня",                           200,  None),
    "level_10":          ("⭐⭐ Уровень 10",     "Достиг 10 уровня",                          500,  None),
    "level_20":          ("⭐⭐⭐ Уровень 20",    "Достиг 20 уровня",                         2000, "Ветеран"),
}


async def earn_achievement(user_id: int, code: str) -> dict | None:
    """Award if not already earned. Returns achievement info or None if already had."""
    if code not in ACHIEVEMENTS:
        return None
    name, desc, reward, title = ACHIEVEMENTS[code]
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "insert into economy_achievements (user_id, code) values ($1, $2) "
            "on conflict do nothing returning code",
            user_id, code,
        )
        if row is None:
            return None
        # credit coins + xp
        if reward > 0:
            await conn.execute(
                "update economy_users set balance = balance + $2, total_earned = total_earned + $2 "
                "where tg_id = $1",
                user_id, reward,
            )
            await conn.execute(
                "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                "select $1, $2, 'achievement', $3, balance from economy_users where tg_id = $1",
                user_id, reward, code,
            )
    # XP for achievement
    await grant_xp(user_id, "achievement")
    return {"code": code, "name": name, "description": desc, "reward": reward, "title": title}


async def list_achievements_for_user(user_id: int) -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "select code, earned_at from economy_achievements where user_id = $1",
            user_id,
        )
    earned = {r["code"]: r["earned_at"] for r in rows}
    out = []
    for code, (name, desc, reward, title) in ACHIEVEMENTS.items():
        out.append({
            "code": code,
            "name": name,
            "description": desc,
            "reward": reward,
            "title": title,
            "earned": code in earned,
            "earned_at": earned[code].isoformat() if code in earned else None,
        })
    return out


async def set_active_title(user_id: int, title: str | None) -> bool:
    """User can pick any title from their earned achievements."""
    if title is None:
        async with pool().acquire() as conn:
            await conn.execute(
                "update economy_users set active_title = null where tg_id = $1",
                user_id,
            )
        return True
    # Verify user has earned an achievement with this title
    earned = await list_achievements_for_user(user_id)
    valid_titles = {a["title"] for a in earned if a["earned"] and a["title"]}
    if title not in valid_titles:
        return False
    async with pool().acquire() as conn:
        await conn.execute(
            "update economy_users set active_title = $2 where tg_id = $1",
            user_id, title,
        )
    return True


# ============================================================
# WEEKLY MISSIONS
# ============================================================

# Missions rotate: each week user gets 6 from this pool.
MISSION_POOL = [
    {"code": "open_3_cases",     "title": "Открой 3 кейса",           "target": 3,     "metric": "cases", "reward": 300},
    {"code": "open_10_cases",    "title": "Открой 10 кейсов",          "target": 10,    "metric": "cases", "reward": 1500},
    {"code": "slots_20_spins",   "title": "Крути слоты 20 раз",        "target": 20,    "metric": "slot_spins", "reward": 500},
    {"code": "slots_jackpot_1",  "title": "Собери джекпот в слотах",  "target": 1,     "metric": "slot_jackpots", "reward": 1000},
    {"code": "coinflip_10",      "title": "Сыграй 10 coinflip'ов",     "target": 10,    "metric": "coinflips", "reward": 400},
    {"code": "crash_3_wins",     "title": "Выиграй 3 раза в crash",    "target": 3,     "metric": "crash_wins", "reward": 700},
    {"code": "daily_5",          "title": "Забери daily 5 дней",       "target": 5,     "metric": "dailies", "reward": 600},
    {"code": "sell_5_items",     "title": "Продай 5 предметов",        "target": 5,     "metric": "sells", "reward": 400},
    {"code": "upgrade_attempt",  "title": "Попробуй апгрейд",          "target": 1,     "metric": "upgrade_attempts", "reward": 300},
    {"code": "earn_5000",        "title": "Заработай 5000 коинов",     "target": 5000,  "metric": "earned", "reward": 1500},
    {"code": "task_7",           "title": "Реши 7 ежедневных задач",   "target": 7,     "metric": "tasks", "reward": 1500},
    {"code": "quiz_correct_5",   "title": "Верно ответь на 5 quiz'ов", "target": 5,     "metric": "quiz_correct", "reward": 500},
]

FINAL_MISSION_REWARD = 5000  # complete all 6 missions = 5000 bonus


def _this_week_monday() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())


async def get_or_create_missions(user_id: int) -> dict:
    week = _this_week_monday()
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "select progress, completed, final_claimed from economy_missions "
            "where user_id = $1 and week = $2",
            user_id, week,
        )
        if row is None:
            # Roll 6 random missions from the pool
            chosen = random.sample(MISSION_POOL, 6)
            # Initialize progress map
            progress_init = {m["code"]: 0 for m in chosen}
            definitions = {m["code"]: m for m in chosen}
            progress_init["_defs"] = definitions
            progress_str = json.dumps(progress_init)
            await conn.execute(
                "insert into economy_missions (user_id, week, progress, completed, final_claimed) "
                "values ($1, $2, $3::jsonb, '[]'::jsonb, false) on conflict do nothing",
                user_id, week, progress_str,
            )
            row = await conn.fetchrow(
                "select progress, completed, final_claimed from economy_missions "
                "where user_id = $1 and week = $2",
                user_id, week,
            )
    progress = row["progress"] if isinstance(row["progress"], dict) else json.loads(row["progress"])
    completed = row["completed"] if isinstance(row["completed"], list) else json.loads(row["completed"])
    defs = progress.get("_defs") or {}
    missions = []
    for code, d in defs.items():
        missions.append({
            "code": code,
            "title": d["title"],
            "target": d["target"],
            "metric": d["metric"],
            "reward": d["reward"],
            "current": int(progress.get(code, 0)),
            "completed": code in completed,
        })
    missions.sort(key=lambda x: (x["completed"], x["target"]))
    return {
        "week": week.isoformat(),
        "missions": missions,
        "final_reward": FINAL_MISSION_REWARD,
        "final_claimed": bool(row["final_claimed"]),
        "all_complete": len(completed) == len(defs) and len(defs) > 0,
    }


async def track_mission_progress(user_id: int, metric: str, amount: int = 1) -> list[dict]:
    """Increment progress for any active mission matching this metric.
    Returns newly-completed missions."""
    week = _this_week_monday()
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select progress, completed from economy_missions "
                "where user_id = $1 and week = $2 for update",
                user_id, week,
            )
            if row is None:
                return []
            progress = row["progress"] if isinstance(row["progress"], dict) else json.loads(row["progress"])
            completed = row["completed"] if isinstance(row["completed"], list) else json.loads(row["completed"])
            defs = progress.get("_defs") or {}
            newly_completed = []
            changed = False
            for code, d in defs.items():
                if d["metric"] != metric or code in completed:
                    continue
                progress[code] = int(progress.get(code, 0)) + amount
                changed = True
                if progress[code] >= d["target"]:
                    completed.append(code)
                    newly_completed.append({"code": code, "title": d["title"], "reward": d["reward"]})
                    # Pay reward
                    await conn.execute(
                        "update economy_users set balance = balance + $2, total_earned = total_earned + $2 "
                        "where tg_id = $1",
                        user_id, int(d["reward"]),
                    )
                    await conn.execute(
                        "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                        "select $1, $2, 'mission', $3, balance from economy_users where tg_id = $1",
                        user_id, int(d["reward"]), code,
                    )
            if changed:
                await conn.execute(
                    "update economy_missions set progress = $3::jsonb, completed = $4::jsonb, updated_at = now() "
                    "where user_id = $1 and week = $2",
                    user_id, week, json.dumps(progress), json.dumps(completed),
                )
    return newly_completed


async def claim_final_mission_reward(user_id: int) -> dict:
    week = _this_week_monday()
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select progress, completed, final_claimed from economy_missions "
                "where user_id = $1 and week = $2 for update",
                user_id, week,
            )
            if row is None:
                return {"ok": False, "error": "No missions this week"}
            if row["final_claimed"]:
                return {"ok": False, "error": "Already claimed"}
            progress = row["progress"] if isinstance(row["progress"], dict) else json.loads(row["progress"])
            completed = row["completed"] if isinstance(row["completed"], list) else json.loads(row["completed"])
            defs = progress.get("_defs") or {}
            if len(completed) < len(defs):
                return {"ok": False, "error": "Not all missions completed"}
            await conn.execute(
                "update economy_users set balance = balance + $2, total_earned = total_earned + $2 "
                "where tg_id = $1",
                user_id, FINAL_MISSION_REWARD,
            )
            new_bal_row = await conn.fetchrow(
                "select balance from economy_users where tg_id = $1", user_id,
            )
            await conn.execute(
                "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                "values ($1, $2, 'mission', 'weekly_final', $3)",
                user_id, FINAL_MISSION_REWARD, int(new_bal_row["balance"]),
            )
            await conn.execute(
                "update economy_missions set final_claimed = true "
                "where user_id = $1 and week = $2",
                user_id, week,
            )
    return {"ok": True, "reward": FINAL_MISSION_REWARD, "new_balance": int(new_bal_row["balance"])}


# ============================================================
# WHEEL OF FORTUNE
# ============================================================

WHEEL_PRIZES = [
    {"kind": "coins", "amount": 0,      "label": "Ничего 😢", "weight": 8},
    {"kind": "coins", "amount": 100,    "label": "+100 🪙",    "weight": 20},
    {"kind": "coins", "amount": 300,    "label": "+300 🪙",    "weight": 25},
    {"kind": "coins", "amount": 700,    "label": "+700 🪙",    "weight": 18},
    {"kind": "coins", "amount": 1500,   "label": "+1 500 🪙",  "weight": 15},
    {"kind": "coins", "amount": 3000,   "label": "+3 000 🪙",  "weight": 8},
    {"kind": "coins", "amount": 7000,   "label": "+7 000 🪙",  "weight": 4},
    {"kind": "coins", "amount": 15000,  "label": "+15 000 🪙", "weight": 1.5},
    {"kind": "coins", "amount": 50000,  "label": "+50 000 🪙 JACKPOT!", "weight": 0.5},
]


async def spin_wheel(user_id: int) -> dict:
    """One free spin per 22 hours (gives wiggle room for timezone)."""
    now = datetime.now(timezone.utc)
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select last_spin_at from economy_wheel where user_id = $1 for update",
                user_id,
            )
            if row is not None and row["last_spin_at"] is not None:
                since = (now - row["last_spin_at"]).total_seconds()
                if since < 22 * 3600:
                    return {"ok": False, "error": "too_early", "next_in_seconds": int(22 * 3600 - since)}

            total_weight = sum(p["weight"] for p in WHEEL_PRIZES)
            r = random.uniform(0, total_weight)
            cum = 0.0
            prize = WHEEL_PRIZES[0]
            for p in WHEEL_PRIZES:
                cum += p["weight"]
                if r <= cum:
                    prize = p
                    break

            # Credit
            if prize["kind"] == "coins" and prize["amount"] > 0:
                await conn.execute(
                    "update economy_users set balance = balance + $2, total_earned = total_earned + $2 "
                    "where tg_id = $1",
                    user_id, int(prize["amount"]),
                )
                bal = await conn.fetchval(
                    "select balance from economy_users where tg_id = $1", user_id,
                )
                await conn.execute(
                    "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                    "values ($1, $2, 'wheel', $3, $4)",
                    user_id, int(prize["amount"]), prize["label"], int(bal),
                )

            # Update wheel state
            await conn.execute(
                "insert into economy_wheel (user_id, last_spin_at, total_spins) "
                "values ($1, $2, 1) "
                "on conflict (user_id) do update set "
                "  last_spin_at = excluded.last_spin_at, "
                "  total_spins = economy_wheel.total_spins + 1",
                user_id, now,
            )
            bal_row = await conn.fetchrow(
                "select balance from economy_users where tg_id = $1", user_id,
            )
    return {
        "ok": True,
        "prize": prize,
        "new_balance": int(bal_row["balance"]) if bal_row else 0,
    }


async def wheel_status(user_id: int) -> dict:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "select last_spin_at, total_spins from economy_wheel where user_id = $1",
            user_id,
        )
    if row is None or row["last_spin_at"] is None:
        return {"available": True, "next_in_seconds": 0, "total_spins": 0}
    now = datetime.now(timezone.utc)
    since = (now - row["last_spin_at"]).total_seconds()
    if since >= 22 * 3600:
        return {"available": True, "next_in_seconds": 0, "total_spins": int(row["total_spins"] or 0)}
    return {
        "available": False,
        "next_in_seconds": int(22 * 3600 - since),
        "total_spins": int(row["total_spins"] or 0),
    }


# ============================================================
# PvP TRACKING (for weekly tournament)
# ============================================================

async def pvp_track(user_id: int, metric: str, amount: int = 1) -> None:
    week = _this_week_monday()
    metric_col = {
        "slots_won": "slots_won",
        "slots_jackpots": "slots_jackpots",
        "coinflip_won": "coinflip_won",
        "crash_profit": "crash_profit",
        "cases_opened": "cases_opened",
        "upgrades_won": "upgrades_won",
        "total_winnings": "total_winnings",
    }.get(metric)
    if metric_col is None:
        return
    async with pool().acquire() as conn:
        await conn.execute(
            f"""
            insert into economy_pvp_week (user_id, week, {metric_col})
            values ($1, $2, $3)
            on conflict (user_id, week) do update set
              {metric_col} = economy_pvp_week.{metric_col} + $3,
              updated_at = now()
            """,
            user_id, week, amount,
        )


async def pvp_leaderboard(metric: str = "total_winnings", limit: int = 10) -> list[dict]:
    allowed = {"slots_won", "slots_jackpots", "coinflip_won", "crash_profit",
               "cases_opened", "upgrades_won", "total_winnings"}
    if metric not in allowed:
        metric = "total_winnings"
    week = _this_week_monday()
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            select p.user_id, p.{metric} as value, u.username, u.first_name
            from economy_pvp_week p
            left join users u on u.tg_id = p.user_id
            where p.week = $1 and p.{metric} > 0
            order by p.{metric} desc
            limit $2
            """,
            week, limit,
        )
    return [
        {
            "tg_id": int(r["user_id"]),
            "value": int(r["value"]),
            "username": r["username"],
            "first_name": r["first_name"],
        }
        for r in rows
    ]


# ============================================================
# STATTRAK COUNTERS
# ============================================================

async def increment_stattrak_kills_on_win(user_id: int, increment: int = 1) -> int:
    """For every StatTrak item the user owns, add +1 kill. Realistic vibe."""
    async with pool().acquire() as conn:
        result = await conn.execute(
            "update economy_inventory set kills = kills + $2 "
            "where user_id = $1 and stat_trak = true and not locked",
            user_id, increment,
        )
    # asyncpg returns command tag like "UPDATE N" — parse out the row count
    try:
        return int(result.split()[-1])
    except Exception:
        return 0


# ============================================================
# ACHIEVEMENT AUTO-CHECK
# ============================================================

async def check_achievements_after_action(user_id: int, action: str, context: dict | None = None) -> list[dict]:
    """Check which achievements should be unlocked after a specific action."""
    context = context or {}
    earned = []
    async with pool().acquire() as conn:
        user = await conn.fetchrow(
            "select balance, cases_opened, current_streak, slots_spins, slots_jackpots, upgrades_won, level "
            "from economy_users where tg_id = $1",
            user_id,
        )
        if user is None:
            return []

    checks: list[tuple[str, bool]] = []

    if action == "case_open":
        cases_opened = int(user["cases_opened"])
        checks.append(("first_case", cases_opened >= 1))
        checks.append(("ten_cases", cases_opened >= 10))
        checks.append(("hundred_cases", cases_opened >= 100))
        rarity = context.get("rarity")
        if rarity == "covert":
            checks.append(("first_covert", True))
        if context.get("category") in ("knife", "gloves"):
            checks.append(("first_knife", True))

    elif action == "slot_spin":
        spins = int(user["slots_spins"])
        jackpots = int(user["slots_jackpots"])
        checks.append(("hundred_spins", spins >= 100))
        if context.get("jackpot"):
            checks.append(("first_jackpot", True))
            checks.append(("ten_jackpots", jackpots >= 10))

    elif action == "upgrade":
        if context.get("success"):
            checks.append(("first_upgrade", True))
            checks.append(("ten_upgrades", int(user["upgrades_won"]) >= 10))

    elif action == "sell":
        checks.append(("first_sell", True))

    elif action == "daily":
        streak = int(user["current_streak"])
        checks.append(("streak_7", streak >= 7))
        checks.append(("streak_30", streak >= 30))

    elif action == "balance":
        checks.append(("millionaire", int(user["balance"]) >= 1_000_000))

    elif action == "level_up":
        level = int(user["level"])
        checks.append(("level_5", level >= 5))
        checks.append(("level_10", level >= 10))
        checks.append(("level_20", level >= 20))

    for code, condition in checks:
        if condition:
            result = await earn_achievement(user_id, code)
            if result:
                earned.append(result)
    return earned


async def bump_stat_counter(user_id: int, counter: str, amount: int = 1) -> None:
    allowed = {"slots_spins", "slots_jackpots", "upgrades_won"}
    if counter not in allowed:
        return
    async with pool().acquire() as conn:
        await conn.execute(
            f"update economy_users set {counter} = {counter} + $2 where tg_id = $1",
            user_id, amount,
        )
