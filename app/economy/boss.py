"""Boss raids — endgame Forge content.

10 story bosses with scaling HP, then endless mode (HP × 1.5 per next tier).
Bosses persist HP between sessions — chip away over days. Each click attacks
the boss; damage scales from your forge upgrades, prestige, and gear's
boss_dmg affix. First-time kill of a tier unlocks the next, every kill grants
coins + trophies.
"""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path

from app.db.client import pool
from app.economy import prestige as _prestige

log = logging.getLogger(__name__)


# ============================================================
# 10 STORY BOSSES + ENDLESS
# ============================================================

# Each boss: (tier, name, lore, icon, hp, coin_reward_base)
BOSSES: list[dict] = [
    {"tier": 1,  "name": "Карманный Лудоман",   "icon": "🪙", "hp": 50_000,        "coin_reward": 500,    "lore": "Кидает все коины в crash и orgазмирует на x1.05"},
    {"tier": 2,  "name": "Игорёк-АФК-Дед",       "icon": "💤", "hp": 200_000,       "coin_reward": 1_500,  "lore": "Стоит в спавне, ловит фраги ради экспы"},
    {"tier": 3,  "name": "Кейс-Маньяк",          "icon": "🎁", "hp": 700_000,       "coin_reward": 5_000,  "lore": "Выбил Glock | Сухая Пустыня и плачет"},
    {"tier": 4,  "name": "Ножевой Задрот",       "icon": "🔪", "hp": 2_500_000,     "coin_reward": 15_000, "lore": "Каждое прокачивание оружия — нервный тик"},
    {"tier": 5,  "name": "Скам-Форсер",          "icon": "💸", "hp": 8_000_000,     "coin_reward": 50_000, "lore": "Покупает крафт, выкидывает к деду в дилерскую"},
    {"tier": 6,  "name": "Тильт-Машина",         "icon": "🔥", "hp": 25_000_000,    "coin_reward": 150_000, "lore": "Ливает с 12-12 с фрейзой 'все рандомы'"},
    {"tier": 7,  "name": "AFK-Демон",            "icon": "🤖", "hp": 80_000_000,    "coin_reward": 400_000, "lore": "Бот, который не спит и не ест"},
    {"tier": 8,  "name": "Прокрастинатор",       "icon": "⏰", "hp": 250_000_000,   "coin_reward": 1_000_000, "lore": "Завтра точно начнёт качать прицел"},
    {"tier": 9,  "name": "Глобал-Эло",           "icon": "🏆", "hp": 1_000_000_000, "coin_reward": 3_000_000, "lore": "AWP в каждой руке, пулька в каждом таргете"},
    {"tier": 10, "name": "👑 Кайро-Финал",       "icon": "👑", "hp": 5_000_000_000, "coin_reward": 12_000_000, "lore": "Сам Кайро. Хохочет в твою тильт-сессию"},
]


def boss_for_tier(tier: int) -> dict:
    """Return boss config for any tier. Tiers 1-10 are story; >10 is endless mode."""
    if 1 <= tier <= 10:
        return BOSSES[tier - 1]
    # Endless: scale from boss 10 by 1.5× per tier above 10
    base = BOSSES[9]
    levels_above = tier - 10
    hp = int(base["hp"] * (1.5 ** levels_above))
    coin = int(base["coin_reward"] * (1.4 ** levels_above))
    return {
        "tier": tier,
        "name": f"♾ Endless #{levels_above}",
        "icon": "♾",
        "hp": hp,
        "coin_reward": coin,
        "lore": f"Бесконечный режим. Тир {levels_above} после Финала.",
    }


# ============================================================
# BOSS HUNTER — prestige branches (jeton-bought permanent bonuses)
# ============================================================

# These are exposed via _prestige module (we register them there for unified UI).
BOSS_HUNTER_BRANCHES = {
    "boss_dmg": {
        "name": "🛡 Сила охотника",
        "desc": "+10% урон по боссам за уровень",
        "max_level": 25,
        "cost_fn": lambda L: 1 + (L - 1) // 3,
        "effect_per_level": 0.10,
        "unit": "% boss-dmg",
    },
    "boss_crit": {
        "name": "🎯 Точка слабости",
        "desc": "+1% шанс крита по боссам",
        "max_level": 30,
        "cost_fn": lambda L: 1 + (L - 1) // 4,
        "effect_per_level": 1.0,
        "unit": "% boss-crit",
    },
    "boss_coin": {
        "name": "💰 Боевой трофей",
        "desc": "+5% коинов с убийства боссов",
        "max_level": 20,
        "cost_fn": lambda L: 1 + (L - 1) // 3,
        "effect_per_level": 0.05,
        "unit": "% coin reward",
    },
    "boss_double": {
        "name": "⚡ Двойной удар",
        "desc": "Шанс двойного урона за тап",
        "max_level": 15,
        "cost_fn": lambda L: 1 + (L - 1) // 2,
        "effect_per_level": 1.0,
        "unit": "% double-hit",
    },
    "boss_pierce": {
        "name": "🩸 Кровопускание",
        "desc": "Боссы спавнятся с -1% HP за уровень",
        "max_level": 10,
        "cost_fn": lambda L: 2 + (L - 1) // 2,
        "effect_per_level": 1.0,
        "unit": "% HP discount",
    },
    "boss_megahit": {
        "name": "💥 Мега-удар",
        "desc": "Каждый 25-й удар наносит ×10",
        "max_level": 10,
        "cost_fn": lambda L: 2 + (L - 1) // 2,
        "effect_per_level": 1.0,  # reduces interval: 25 - lvl (min 15)
        "unit": "удар",
    },
}

_BOSS_HUNTER_COL = {
    "boss_dmg":    "boss_dmg_lvl",
    "boss_crit":   "boss_crit_lvl",
    "boss_coin":   "boss_coin_lvl",
    "boss_double": "boss_double_lvl",
    "boss_pierce": "boss_pierce_lvl",
    "boss_megahit": "boss_megahit_lvl",
}


# ============================================================
# SCHEMA + STATE
# ============================================================

async def ensure_schema() -> None:
    sql_path = Path(__file__).parent.parent / "db" / "migration_bosses.sql"
    if not sql_path.exists():
        log.warning("boss migration SQL missing")
        return
    sql = sql_path.read_text(encoding="utf-8")
    async with pool().acquire() as conn:
        await conn.execute(sql)
    log.info("boss schema ensured")


def _parse_gear(raw) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw) or {}
        except Exception:
            return {}
    return {}


def _compute_max_hp(tier: int, pierce_lvl: int) -> int:
    boss = boss_for_tier(tier)
    pierce_pct = pierce_lvl * 1.0  # 1% per level
    pierce_pct = min(pierce_pct, 50)  # safety cap
    return int(boss["hp"] * (1 - pierce_pct / 100))


async def get_state(tg_id: int) -> dict:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "select boss_tier, boss_current_hp, boss_total_kills, boss_max_tier, boss_endless_kills, "
            "boss_dmg_lvl, boss_crit_lvl, boss_coin_lvl, boss_double_lvl, boss_pierce_lvl, boss_megahit_lvl, "
            "damage_level, crit_level, crit_power_level, luck_level, "
            "hammer_power_lvl, dust_magic_lvl, sharpen_lvl, "
            "gear_affixes, total_clicks "
            "from forge_users where tg_id = $1",
            tg_id,
        )
    if row is None:
        return {"unlocked": False}

    tier = int(row["boss_tier"])
    boss = boss_for_tier(tier)
    pierce_lvl = int(row["boss_pierce_lvl"] or 0)
    max_hp = _compute_max_hp(tier, pierce_lvl)
    cur_hp = int(row["boss_current_hp"])
    if cur_hp <= 0 or cur_hp > max_hp:
        cur_hp = max_hp  # initialize / sanity

    dmg_per_hit = _preview_damage(row)
    return {
        "unlocked": True,
        "tier": tier,
        "name": boss["name"],
        "icon": boss["icon"],
        "lore": boss["lore"],
        "hp": cur_hp,
        "max_hp": max_hp,
        "coin_reward": int(boss["coin_reward"]),
        "total_kills": int(row["boss_total_kills"]),
        "max_tier": int(row["boss_max_tier"]),
        "endless_kills": int(row["boss_endless_kills"]),
        "preview_dmg": dmg_per_hit,
        "boss_levels": {
            "boss_dmg":    int(row["boss_dmg_lvl"] or 0),
            "boss_crit":   int(row["boss_crit_lvl"] or 0),
            "boss_coin":   int(row["boss_coin_lvl"] or 0),
            "boss_double": int(row["boss_double_lvl"] or 0),
            "boss_pierce": int(row["boss_pierce_lvl"] or 0),
            "boss_megahit": int(row["boss_megahit_lvl"] or 0),
        },
    }


def _preview_damage(row) -> int:
    """Preview damage per click against boss (before crit/double/megahit randomness)."""
    from app.economy.forge import damage_at, crit_multiplier_at
    dmg_lvl = int(row["damage_level"])
    crit_power_lvl = int(row["crit_power_level"] or 0)
    base_dmg = damage_at(dmg_lvl)

    # Prestige
    hp_pow_mult = _prestige.hammer_power_mult(int(row["hammer_power_lvl"] or 0))
    boss_dmg_lvl = int(row["boss_dmg_lvl"] or 0)
    boss_dmg_mult = 1.0 + boss_dmg_lvl * 0.10  # 10% per level

    # Gear
    gear = _parse_gear(row["gear_affixes"])
    gear_dmg_mult = 1.0 + float(gear.get("dmg", 0)) / 100
    gear_boss_dmg_mult = 1.0 + float(gear.get("boss_dmg", 0)) / 100

    # Avg damage including crit
    crit_chance = 0.20  # rough avg
    crit_mult = crit_multiplier_at(crit_power_lvl)
    avg_dmg = base_dmg * (1 - crit_chance + crit_chance * crit_mult)

    final = avg_dmg * hp_pow_mult * boss_dmg_mult * gear_dmg_mult * gear_boss_dmg_mult
    return max(1, int(final))


# ============================================================
# ATTACK
# ============================================================

async def attack(tg_id: int, taps: int = 1) -> dict:
    """Apply `taps` attacks against current boss. Returns dmg dealt, kills, hp left, rewards."""
    if taps <= 0 or taps > 50:
        return {"ok": False, "error": "Invalid taps"}

    from app.economy.forge import damage_at, crit_chance_at, crit_multiplier_at

    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select boss_tier, boss_current_hp, boss_total_kills, boss_max_tier, boss_endless_kills, "
                "boss_dmg_lvl, boss_crit_lvl, boss_coin_lvl, boss_double_lvl, boss_pierce_lvl, boss_megahit_lvl, "
                "damage_level, crit_level, crit_power_level, "
                "hammer_power_lvl, sharpen_lvl, "
                "gear_affixes, total_clicks "
                "from forge_users where tg_id = $1 for update",
                tg_id,
            )
            if row is None:
                return {"ok": False, "error": "Forge not opened"}

            tier = int(row["boss_tier"])
            pierce_lvl = int(row["boss_pierce_lvl"] or 0)
            max_hp = _compute_max_hp(tier, pierce_lvl)
            cur_hp = int(row["boss_current_hp"]) if int(row["boss_current_hp"]) > 0 else max_hp
            if cur_hp > max_hp:
                cur_hp = max_hp

            # Damage components
            dmg_lvl = int(row["damage_level"])
            crit_lvl = int(row["crit_level"])
            crit_power_lvl = int(row["crit_power_level"] or 0)
            base_dmg = damage_at(dmg_lvl)

            # Prestige multipliers
            hp_pow_mult = _prestige.hammer_power_mult(int(row["hammer_power_lvl"] or 0))
            sharpen_flat = _prestige.sharpen_flat_crit(int(row["sharpen_lvl"] or 0))
            boss_dmg_lvl = int(row["boss_dmg_lvl"] or 0)
            boss_crit_lvl = int(row["boss_crit_lvl"] or 0)
            boss_coin_lvl = int(row["boss_coin_lvl"] or 0)
            boss_double_lvl = int(row["boss_double_lvl"] or 0)
            boss_megahit_lvl = int(row["boss_megahit_lvl"] or 0)

            boss_dmg_mult = 1.0 + boss_dmg_lvl * 0.10
            boss_crit_pct = crit_chance_at(crit_lvl) + sharpen_flat + boss_crit_lvl  # +1pp per level
            double_pct = boss_double_lvl * 1.0  # 1% per level
            megahit_interval = max(15, 25 - boss_megahit_lvl)  # min every 15 hits

            # Gear
            gear = _parse_gear(row["gear_affixes"])
            gear_dmg_mult = 1.0 + float(gear.get("dmg", 0)) / 100
            gear_boss_dmg_mult = 1.0 + float(gear.get("boss_dmg", 0)) / 100
            gear_crit_dmg_mult = 1.0 + float(gear.get("crit_dmg", 0)) / 100
            crit_mult_base = crit_multiplier_at(crit_power_lvl) * gear_crit_dmg_mult

            total_clicks = int(row["total_clicks"] or 0)
            kills = []
            crits = 0
            doubles = 0
            megahits = 0
            total_dmg = 0
            coin_reward_total = 0
            tier_changed = False

            for i in range(taps):
                hit_idx = total_clicks + i + 1
                # Compose damage
                is_crit = random.uniform(0, 100) < boss_crit_pct
                effective_crit = crit_mult_base if is_crit else 1.0
                hit_dmg = int(base_dmg * effective_crit * hp_pow_mult * boss_dmg_mult * gear_dmg_mult * gear_boss_dmg_mult)
                if is_crit:
                    crits += 1

                # Double hit
                if double_pct > 0 and random.uniform(0, 100) < double_pct:
                    hit_dmg *= 2
                    doubles += 1

                # Mega hit (every Nth)
                if hit_idx % megahit_interval == 0 and boss_megahit_lvl > 0:
                    hit_dmg *= 10
                    megahits += 1

                cur_hp -= hit_dmg
                total_dmg += hit_dmg

                if cur_hp <= 0:
                    # Kill!
                    cur_boss = boss_for_tier(tier)
                    coin_pct = 1.0 + boss_coin_lvl * 0.05
                    coin_pay = int(cur_boss["coin_reward"] * coin_pct)
                    coin_reward_total += coin_pay
                    kills.append({
                        "tier": tier,
                        "name": cur_boss["name"],
                        "icon": cur_boss["icon"],
                        "coin_reward": coin_pay,
                    })
                    # Advance tier
                    tier += 1
                    tier_changed = True
                    pierce_lvl_used = pierce_lvl
                    new_boss = boss_for_tier(tier)
                    cur_hp = int(new_boss["hp"] * (1 - pierce_lvl_used / 100))
                    max_hp = cur_hp

            # Persist
            new_total_kills = int(row["boss_total_kills"]) + len(kills)
            new_max_tier = max(int(row["boss_max_tier"]), tier - 1 if tier_changed else tier)
            new_endless_kills = int(row["boss_endless_kills"]) + sum(1 for k in kills if k["tier"] > 10)

            await conn.execute(
                "update forge_users set "
                "  boss_tier = $2, boss_current_hp = $3, boss_total_kills = $4, "
                "  boss_max_tier = $5, boss_endless_kills = $6, "
                "  total_clicks = total_clicks + $7 "
                "where tg_id = $1",
                tg_id, tier, max(0, cur_hp), new_total_kills, new_max_tier, new_endless_kills, taps,
            )

            # Credit coin rewards
            new_bal = None
            if coin_reward_total > 0:
                bal_row = await conn.fetchrow(
                    "update economy_users set balance = balance + $2, total_earned = total_earned + $2 "
                    "where tg_id = $1 returning balance",
                    tg_id, coin_reward_total,
                )
                new_bal = int(bal_row["balance"]) if bal_row else None
                await conn.execute(
                    "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                    "values ($1, $2, 'boss', $3, $4)",
                    tg_id, coin_reward_total,
                    f"boss_kill_x{len(kills)}_top_tier_{kills[-1]['tier']}",
                    new_bal,
                )

    return {
        "ok": True,
        "applied_taps": taps,
        "total_dmg": total_dmg,
        "crits": crits,
        "doubles": doubles,
        "megahits": megahits,
        "kills": kills,
        "boss_after": {
            "tier": tier,
            "hp": max(0, cur_hp),
            "max_hp": max_hp,
            "name": boss_for_tier(tier)["name"],
            "icon": boss_for_tier(tier)["icon"],
        },
        "coin_reward": coin_reward_total,
        "new_balance": new_bal,
    }


# ============================================================
# BUY BOSS-HUNTER PRESTIGE UPGRADE
# ============================================================

async def buy_boss_upgrade(tg_id: int, branch: str) -> dict:
    if branch not in BOSS_HUNTER_BRANCHES:
        return {"ok": False, "error": "Unknown boss-hunter branch"}
    cfg = BOSS_HUNTER_BRANCHES[branch]
    col = _BOSS_HUNTER_COL[branch]
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                f"select jetons, {col} as lvl from forge_users where tg_id = $1 for update",
                tg_id,
            )
            if row is None:
                return {"ok": False, "error": "No state"}
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


def get_branches_info() -> list[dict]:
    """Return boss-hunter prestige branches for UI display."""
    out = []
    for key, cfg in BOSS_HUNTER_BRANCHES.items():
        out.append({
            "key": key,
            "name": cfg["name"],
            "desc": cfg["desc"],
            "max_level": cfg["max_level"],
            "effect_per_level": cfg["effect_per_level"],
            "unit": cfg["unit"],
        })
    return out
