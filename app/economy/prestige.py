"""Prestige system — soft-reset Forge progress for permanent bonuses.

Flow:
1. User reaches eligibility (run_particles_earned >= threshold).
2. `do_prestige` wipes all upgrade levels, current weapon, particle balance,
   run stats. User gets `jetons` proportional to sqrt(run_particles / 5000).
3. Jetons spent on permanent bonus tree. Bonuses apply multiplicatively at
   calc sites (damage, particles, AFK rate, crit, tier_luck, case luck).
4. Starting capital bonus gives N particles on next reset — skips early grind.

Bonuses are additive per level, globally capped. All tiers + effect formulas
are declared once here and consumed by forge.py and repo.py.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

from app.db.client import pool

log = logging.getLogger(__name__)

# ============================================================
# CONFIG
# ============================================================

# Particles in one run required for first prestige
PRESTIGE_THRESHOLD = 100_000

# Jetons awarded = floor(sqrt(run_particles / 5000))
# 100k → 4 jetons, 500k → 10, 1M → 14, 10M → 44.
JETONS_DIVISOR = 5000


def compute_jetons(run_particles: int) -> int:
    if run_particles < PRESTIGE_THRESHOLD:
        return 0
    return max(1, int(math.sqrt(run_particles / JETONS_DIVISOR)))


def is_eligible(run_particles: int) -> bool:
    return run_particles >= PRESTIGE_THRESHOLD


# Bonus tree. Each branch:
#   name, desc, max_level, cost_fn(L)->jetons, effect_per_level (raw value),
#   effect_unit (for UI label)
# Effect is ADDITIVE per level; total = lvl * effect_per_level.

BONUSES = {
    "hammer_power": {
        "name": "⚒ Сила молота",
        "desc": "+5% к урону за клик",
        "max_level": 20,
        "cost_fn": lambda L: 1 + (L - 1) // 3,   # 1..1..1..2..2..2..3..
        "effect_per_level": 0.05,                 # +5% per level, max +100%
        "unit": "% dmg",
    },
    "dust_magic": {
        "name": "✨ Магия пыли",
        "desc": "+5% particles за поломку (клик + AFK)",
        "max_level": 20,
        "cost_fn": lambda L: 1 + (L - 1) // 3,
        "effect_per_level": 0.05,
        "unit": "% particles",
    },
    "bot_tune": {
        "name": "🤖 Тюнинг ботов",
        "desc": "+8% к AFK-скорости всех ботов",
        "max_level": 15,
        "cost_fn": lambda L: 1 + (L - 1) // 2,
        "effect_per_level": 0.08,
        "unit": "% AFK",
    },
    "sharpen": {
        "name": "🎯 Заточка",
        "desc": "+1% к базовому шансу крита",
        "max_level": 20,
        "cost_fn": lambda L: 1 + (L - 1) // 3,
        "effect_per_level": 1.0,                  # +1 percentage point per level
        "unit": "% crit",
    },
    "fortune": {
        "name": "🔮 Удачливость",
        "desc": "+2% к базе tier_luck (лучшие спавны)",
        "max_level": 10,
        "cost_fn": lambda L: 1 + (L - 1) // 2,
        "effect_per_level": 2.0,                  # +2 percentage points per level
        "unit": "% tier-luck",
    },
    "starting_capital": {
        "name": "💸 Стартовый капитал",
        "desc": "На следующем престиже стартуешь с particles",
        "max_level": 10,
        "cost_fn": lambda L: 2 + (L - 1),
        "effect_per_level": 500,                  # 500 particles per level
        "unit": "⚙ стартовых",
    },
    "discount": {
        "name": "🏷 Скидка на рынке",
        "desc": "-2% к стоимости всех апгрейдов",
        "max_level": 10,
        "cost_fn": lambda L: 2 + (L - 1) // 2,
        "effect_per_level": 0.02,
        "unit": "% скидка",
    },
    "case_face": {
        "name": "🎁 Лицо случая",
        "desc": "+1% к шансу топ-рарити в кейсах",
        "max_level": 10,
        "cost_fn": lambda L: 2 + (L - 1) // 2,
        "effect_per_level": 0.01,
        "unit": "% case-luck",
    },
}

# Map branch → DB column
_BRANCH_COL = {
    "hammer_power": "hammer_power_lvl",
    "dust_magic": "dust_magic_lvl",
    "bot_tune": "bot_tune_lvl",
    "sharpen": "sharpen_lvl",
    "fortune": "fortune_lvl",
    "starting_capital": "starting_capital_lvl",
    "discount": "discount_lvl",
    "case_face": "case_face_lvl",
}


# ============================================================
# EFFECT ACCESSORS — called from forge.py and repo.py
# ============================================================

def hammer_power_mult(lvl: int) -> float:
    """Multiplier applied to per-click damage. 1.0 = no bonus."""
    return 1.0 + max(0, lvl) * BONUSES["hammer_power"]["effect_per_level"]

def dust_magic_mult(lvl: int) -> float:
    """Multiplier applied to particle rewards from breaks."""
    return 1.0 + max(0, lvl) * BONUSES["dust_magic"]["effect_per_level"]

def bot_tune_mult(lvl: int) -> float:
    """Multiplier applied to AFK bot rates."""
    return 1.0 + max(0, lvl) * BONUSES["bot_tune"]["effect_per_level"]

def sharpen_flat_crit(lvl: int) -> int:
    """Flat percentage points added to crit chance."""
    return int(max(0, lvl) * BONUSES["sharpen"]["effect_per_level"])

def fortune_flat_tier_luck(lvl: int) -> float:
    """Flat decimal added to tier_luck probability. lvl 5 → +0.10 (+10%)."""
    return max(0, lvl) * BONUSES["fortune"]["effect_per_level"] / 100.0

def starting_capital_amount(lvl: int) -> int:
    """Particles given at the start of the next run after a prestige."""
    return int(max(0, lvl) * BONUSES["starting_capital"]["effect_per_level"])

def discount_mult(lvl: int) -> float:
    """Multiplier applied to upgrade costs. lvl 5 → 0.9 (10% off)."""
    return max(0.0, 1.0 - max(0, lvl) * BONUSES["discount"]["effect_per_level"])

def case_face_bonus_pct(lvl: int) -> float:
    """Decimal bonus to top-rarity case roll. lvl 5 → +0.05 (+5pp)."""
    return max(0, lvl) * BONUSES["case_face"]["effect_per_level"]


# ============================================================
# SCHEMA — idempotent migration runner
# ============================================================

async def ensure_schema() -> None:
    """Apply migration_prestige.sql at startup. Safe to re-run."""
    sql_path = Path(__file__).parent.parent / "db" / "migration_prestige.sql"
    if not sql_path.exists():
        log.warning("prestige migration SQL missing: %s", sql_path)
        return
    sql = sql_path.read_text(encoding="utf-8")
    async with pool().acquire() as conn:
        await conn.execute(sql)
    log.info("prestige schema ensured")


# ============================================================
# STATE FETCH
# ============================================================

async def get_state(tg_id: int) -> dict:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """select prestige_level, jetons, jetons_lifetime, run_particles_earned,
                      hammer_power_lvl, dust_magic_lvl, bot_tune_lvl, sharpen_lvl,
                      fortune_lvl, starting_capital_lvl, discount_lvl, case_face_lvl
               from forge_users where tg_id = $1""",
            tg_id,
        )
    if row is None:
        return {
            "level": 0, "jetons": 0, "jetons_lifetime": 0, "run_particles": 0,
            "next_prestige": {"eligible": False, "jetons_on_prestige": 0, "threshold": PRESTIGE_THRESHOLD},
            "bonuses": [],
        }

    levels = {k: int(row[c] or 0) for k, c in _BRANCH_COL.items()}
    run_p = int(row["run_particles_earned"] or 0)

    bonuses = []
    for key, cfg in BONUSES.items():
        lvl = levels[key]
        max_lvl = cfg["max_level"]
        next_cost = None
        if lvl < max_lvl:
            next_cost = cfg["cost_fn"](lvl + 1)
        bonuses.append({
            "key": key,
            "name": cfg["name"],
            "desc": cfg["desc"],
            "level": lvl,
            "max_level": max_lvl,
            "effect_per_level": cfg["effect_per_level"],
            "unit": cfg["unit"],
            "current_total": round(lvl * cfg["effect_per_level"], 3),
            "next_total": round((lvl + 1) * cfg["effect_per_level"], 3) if lvl < max_lvl else None,
            "next_cost": next_cost,
        })

    return {
        "level": int(row["prestige_level"] or 0),
        "jetons": int(row["jetons"] or 0),
        "jetons_lifetime": int(row["jetons_lifetime"] or 0),
        "run_particles": run_p,
        "next_prestige": {
            "eligible": is_eligible(run_p),
            "jetons_on_prestige": compute_jetons(run_p),
            "threshold": PRESTIGE_THRESHOLD,
        },
        "bonuses": bonuses,
    }


# ============================================================
# DO PRESTIGE
# ============================================================

async def do_prestige(tg_id: int) -> dict:
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select run_particles_earned, prestige_level, jetons, "
                "jetons_lifetime, starting_capital_lvl "
                "from forge_users where tg_id = $1 for update",
                tg_id,
            )
            if row is None:
                return {"ok": False, "error": "No forge state"}
            run_p = int(row["run_particles_earned"] or 0)
            if not is_eligible(run_p):
                return {
                    "ok": False,
                    "error": f"Нужно {PRESTIGE_THRESHOLD - run_p:,} particles в этом ране",
                    "run_particles": run_p,
                    "threshold": PRESTIGE_THRESHOLD,
                }
            earned = compute_jetons(run_p)
            starting_p = starting_capital_amount(int(row["starting_capital_lvl"] or 0))

            # Reset run state — keep lifetime stats, jetons, bonuses, prestige_level (+1)
            await conn.execute(
                """
                update forge_users set
                  damage_level = 0,
                  crit_level = 0,
                  crit_power_level = 0,
                  luck_level = 0,
                  tier_luck_level = 0,
                  stattrak_hunter_level = 0,
                  offline_cap_level = 0,
                  silver_level = -1,
                  gold_level = -1,
                  global_level = -1,
                  particles = $2,
                  run_particles_earned = 0,
                  current_skin_id = null,
                  current_weapon_tier = null,
                  current_weapon_hp = null,
                  current_weapon_max_hp = null,
                  current_weapon_particles = null,
                  current_weapon_stattrak = false,
                  prestige_level = prestige_level + 1,
                  jetons = jetons + $3,
                  jetons_lifetime = jetons_lifetime + $3,
                  updated_at = now()
                where tg_id = $1
                """,
                tg_id, starting_p, earned,
            )
    return {
        "ok": True,
        "jetons_earned": earned,
        "starting_particles": starting_p,
        "new_prestige_level": int(row["prestige_level"] or 0) + 1,
        "new_jetons_balance": int(row["jetons"] or 0) + earned,
    }


# ============================================================
# BUY BONUS UPGRADE
# ============================================================

async def buy_upgrade(tg_id: int, branch: str) -> dict:
    if branch not in BONUSES:
        return {"ok": False, "error": "Unknown bonus"}
    cfg = BONUSES[branch]
    col = _BRANCH_COL[branch]
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                f"select jetons, {col} as lvl from forge_users where tg_id = $1 for update",
                tg_id,
            )
            if row is None:
                return {"ok": False, "error": "No forge state"}
            lvl = int(row["lvl"] or 0)
            if lvl >= cfg["max_level"]:
                return {"ok": False, "error": "Уже макс"}
            cost = cfg["cost_fn"](lvl + 1)
            if int(row["jetons"] or 0) < cost:
                return {"ok": False, "error": "Недостаточно жетонов", "cost": cost}
            await conn.execute(
                f"update forge_users set jetons = jetons - $2, {col} = {col} + 1 where tg_id = $1",
                tg_id, cost,
            )
    return {
        "ok": True,
        "branch": branch,
        "new_level": lvl + 1,
        "cost": cost,
        "new_jetons": int(row["jetons"]) - cost,
    }


# ============================================================
# LEVELS HELPER (for forge.py callers)
# ============================================================

async def get_user_bonus_levels(tg_id: int) -> dict:
    """Single-query fetch of all bonus levels for use in hit_batch, tick_afk, spawn."""
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """select hammer_power_lvl, dust_magic_lvl, bot_tune_lvl, sharpen_lvl,
                      fortune_lvl, discount_lvl, case_face_lvl
               from forge_users where tg_id = $1""",
            tg_id,
        )
    if row is None:
        return {k: 0 for k in BONUSES}
    return {
        "hammer_power": int(row["hammer_power_lvl"] or 0),
        "dust_magic": int(row["dust_magic_lvl"] or 0),
        "bot_tune": int(row["bot_tune_lvl"] or 0),
        "sharpen": int(row["sharpen_lvl"] or 0),
        "fortune": int(row["fortune_lvl"] or 0),
        "discount": int(row["discount_lvl"] or 0),
        "case_face": int(row["case_face_lvl"] or 0),
    }
