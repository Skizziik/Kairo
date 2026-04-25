"""Weapon Forge — idle clicker where users break real CS2 skins for particles.

Flow:
- User hits weapon (rate-limited server side)
- Damage reduces weapon HP (base damage from upgrade + crit chance)
- When HP reaches 0 → particles credited, new weapon spawns
- Particles spent on upgrades (damage, crit, luck, AFK workers, offline cap)
- Particles exchangeable for coins at 10:1 rate
- AFK workers produce particles idle; daily cap 30000 from AFK

All heavy math + weapon pool cached in Python — DB touched only for state changes.
"""
from __future__ import annotations

import logging
import random
from datetime import date, datetime, timedelta, timezone

from app.db.client import pool
from app.economy import prestige as _prestige

log = logging.getLogger(__name__)


def _parse_gear_affixes(raw) -> dict:
    """forge_users.gear_affixes may be dict (asyncpg) or str (fallback)."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        import json as _json
        try:
            return _json.loads(raw) or {}
        except Exception:
            return {}
    return {}


# ============================================================
# WEAPON TIER CONFIG (what spawns on the anvil)
# ============================================================

TIER_CONFIG = {
    "pistol": {
        "hp": 50, "particles": 2, "weight": 55, "min_damage_level": 0,
        "weapons": {"Desert Eagle", "USP-S", "Glock-18", "P2000", "Five-SeveN",
                    "Tec-9", "P250", "R8 Revolver", "Dual Berettas", "CZ75-Auto"},
        "rarities": None,  # any
    },
    "rifle": {
        "hp": 300, "particles": 10, "weight": 30, "min_damage_level": 0,
        "weapons": {"AK-47", "M4A4", "M4A1-S", "Galil AR", "FAMAS", "AUG", "SG 553",
                    "MP9", "MAC-10", "MP7", "MP5-SD", "P90", "UMP-45", "PP-Bizon"},
        "rarities": None,
    },
    "awp": {
        "hp": 1500, "particles": 60, "weight": 12, "min_damage_level": 3,
        "weapons": {"AWP", "SSG 08", "SCAR-20", "G3SG1",
                    "Negev", "M249", "Nova", "XM1014", "Sawed-Off", "MAG-7"},
        "rarities": None,
    },
    "golden": {
        "hp": 10000, "particles": 500, "weight": 2.5, "min_damage_level": 8,
        "weapons": None,  # any weapon, rarity Covert
        "rarities": {"covert"},
    },
    "legendary": {
        "hp": 80000, "particles": 5000, "weight": 0.5, "min_damage_level": 15,
        "weapons": None,  # knives/gloves only
        "rarities": {"exceedingly_rare"},
    },
}

TIER_ORDER = ["pistol", "rifle", "awp", "golden", "legendary"]

# Cache of available skin_ids per tier, rebuilt every 10 min.
_skin_pool_cache: dict[str, list[int]] = {}
_skin_pool_cache_ts: datetime | None = None
_SKIN_POOL_TTL = timedelta(minutes=10)


async def _refresh_skin_pools() -> None:
    global _skin_pool_cache, _skin_pool_cache_ts
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "select id, weapon, rarity, category from economy_skins_catalog where active"
        )
    # Separate knives/gloves from regular weapons so Golden tier (covert-only)
    # doesn't accidentally include knives/gloves.
    by_weapon: dict[tuple[str, str], list[int]] = {}
    knives: list[int] = []
    gloves: list[int] = []
    for r in rows:
        if r["category"] == "knife":
            knives.append(int(r["id"]))
            continue
        if r["category"] == "gloves":
            gloves.append(int(r["id"]))
            continue
        by_weapon.setdefault((r["weapon"], r["rarity"]), []).append(int(r["id"]))

    pools: dict[str, list[int]] = {k: [] for k in TIER_CONFIG}
    for tier_key, cfg in TIER_CONFIG.items():
        if tier_key == "legendary":
            pools[tier_key] = knives + gloves
            continue
        for (weapon, rarity), ids in by_weapon.items():
            if cfg["weapons"] is not None and weapon not in cfg["weapons"]:
                continue
            if cfg["rarities"] is not None and rarity not in cfg["rarities"]:
                continue
            pools[tier_key].extend(ids)
    _skin_pool_cache = pools
    _skin_pool_cache_ts = datetime.now(timezone.utc)


async def _get_skin_pool(tier: str) -> list[int]:
    global _skin_pool_cache_ts
    now = datetime.now(timezone.utc)
    if _skin_pool_cache_ts is None or now - _skin_pool_cache_ts > _SKIN_POOL_TTL:
        await _refresh_skin_pools()
    return _skin_pool_cache.get(tier, [])


def _roll_tier(damage_level: int = 0) -> str:
    """Roll a tier, filtered by user's progression. Low damage levels
    never get expensive weapons so new players don't get stuck."""
    eligible = {t: c for t, c in TIER_CONFIG.items()
                if damage_level >= c.get("min_damage_level", 0)}
    if not eligible:
        return "pistol"
    r = random.uniform(0, sum(c["weight"] for c in eligible.values()))
    cum = 0.0
    for t, cfg in eligible.items():
        cum += cfg["weight"]
        if r <= cum:
            return t
    return "pistol"


# ============================================================
# UPGRADE TREE
# ============================================================

# For each branch: list of (level_reached_after_buy, effect_value, cost_particles)
# Level 0 = base state (no upgrade bought).
# Upgrade tiers generated from formulas for smoother progression.
def _build_tiers(max_level: int, effect_fn, cost_fn, round_effect: bool = True) -> list[tuple]:
    tiers = []
    for lvl in range(1, max_level + 1):
        effect = effect_fn(lvl)
        if round_effect:
            effect = int(round(effect))
        cost = int(round(cost_fn(lvl)))
        tiers.append((lvl, effect, cost))
    return tiers


# Damage: 25 levels, 2 → ~94 damage. Steeper late-game so high-HP weapons
# (AWP 1500, Golden 10k, Legendary 80k) don't require thousands of clicks.
DAMAGE_TIERS = _build_tiers(
    max_level=25,
    effect_fn=lambda L: 1 + L * 1.0 + (L ** 1.65) * 0.35,
    cost_fn=lambda L: 15 * (1.42 ** (L - 1)),
)
# Crit: 20 levels, 0 → 20%. Total ~160k.
CRIT_TIERS = _build_tiers(
    max_level=20,
    effect_fn=lambda L: L,  # 1% per level up to 20%
    cost_fn=lambda L: 40 * (1.38 ** (L - 1)),
)
# Luck: 20 levels, 0 → 60%. Total ~160k.
LUCK_TIERS = _build_tiers(
    max_level=20,
    effect_fn=lambda L: L * 3,  # +3% per level
    cost_fn=lambda L: 40 * (1.38 ** (L - 1)),
)
# Offline cap: 8 levels, 8h → 24h.
OFFLINE_TIERS = _build_tiers(
    max_level=8,
    effect_fn=lambda L: 8 + L * 2,  # +2h per level (8 → 24h)
    cost_fn=lambda L: 2000 * (1.9 ** (L - 1)),
)

# AFK bots — 20 levels each
SILVER_UNLOCK_COST = 80                        # affordable very early (~4 rifles)
SILVER_BASE_RATE = 0.3                         # weak but present auto-farm
SILVER_UPGRADE_TIERS = _build_tiers(
    max_level=20,
    effect_fn=lambda L: 12.3 + L * 0.12,       # +10 base → L1=12.42/s ... L20=14.7/s
    cost_fn=lambda L: 100 * (1.35 ** (L - 1)),
    round_effect=False,
)
GOLD_UNLOCK_COST = 8000
GOLD_BASE_RATE = 1.0
GOLD_UPGRADE_TIERS = _build_tiers(
    max_level=20,
    effect_fn=lambda L: 13.0 + L * 0.5,        # +10 base → L1=13.5/s ... L20=23/s
    cost_fn=lambda L: 250 * (1.3 ** (L - 1)),
    round_effect=False,
)
GLOBAL_UNLOCK_COST = 60000
GLOBAL_BASE_RATE = 4.0
GLOBAL_UPGRADE_TIERS = _build_tiers(
    max_level=20,
    effect_fn=lambda L: 16.0 + L * 1.5,        # +10 base → L1=17.5/s ... L20=46/s
    cost_fn=lambda L: 1500 * (1.3 ** (L - 1)),
    round_effect=False,
)

# ===== New branches (wiring up existing DB columns) =====

# Crit power — crit multiplier grows from x3 base
CRIT_POWER_TIERS = _build_tiers(
    max_level=10,
    effect_fn=lambda L: 3.0 + L * 0.3,        # x3.3, x3.6, ... x6.0 at L10
    cost_fn=lambda L: 150 * (1.45 ** (L - 1)),
    round_effect=False,
)

# StatTrak hunter — chance of ST weapon spawning
STATTRAK_HUNTER_TIERS = _build_tiers(
    max_level=10,
    effect_fn=lambda L: 5 + L,                # 6% … 15% (base 5)
    cost_fn=lambda L: 200 * (1.42 ** (L - 1)),
)

# Tier luck — shifts spawn toward higher tiers
TIER_LUCK_TIERS = _build_tiers(
    max_level=10,
    effect_fn=lambda L: L * 2,                # +2%..+20% "tier shift up" chance
    cost_fn=lambda L: 500 * (1.45 ** (L - 1)),
)


def crit_multiplier_at(level: int) -> float:
    if level <= 0:
        return 3.0
    return CRIT_POWER_TIERS[min(level, 10) - 1][1]


def stattrak_chance_at(level: int) -> float:
    base = STATTRAK_SPAWN_CHANCE_BASE
    if level <= 0:
        return base
    return STATTRAK_HUNTER_TIERS[min(level, 10) - 1][1] / 100.0


def tier_luck_at(level: int) -> float:
    if level <= 0:
        return 0.0
    return TIER_LUCK_TIERS[min(level, 10) - 1][1] / 100.0


def damage_at(level: int) -> int:
    if level <= 0:
        return 1
    return DAMAGE_TIERS[min(level, len(DAMAGE_TIERS)) - 1][1]


def crit_chance_at(level: int) -> int:
    if level <= 0:
        return 0
    return CRIT_TIERS[min(level, len(CRIT_TIERS)) - 1][1]


def luck_bonus_at(level: int) -> int:
    if level <= 0:
        return 0
    return LUCK_TIERS[min(level, len(LUCK_TIERS)) - 1][1]


def offline_hours_at(level: int) -> int:
    if level <= 0:
        return 8
    return OFFLINE_TIERS[min(level, len(OFFLINE_TIERS)) - 1][1]


def silver_rate_at(level: int) -> float:
    if level < 0:
        return 0.0
    if level == 0:
        return SILVER_BASE_RATE
    return SILVER_UPGRADE_TIERS[min(level, len(SILVER_UPGRADE_TIERS)) - 1][1]


def gold_rate_at(level: int) -> float:
    if level < 0:
        return 0.0
    if level == 0:
        return GOLD_BASE_RATE
    return GOLD_UPGRADE_TIERS[min(level, len(GOLD_UPGRADE_TIERS)) - 1][1]


def global_rate_at(level: int) -> float:
    if level < 0:
        return 0.0
    if level == 0:
        return GLOBAL_BASE_RATE
    return GLOBAL_UPGRADE_TIERS[min(level, len(GLOBAL_UPGRADE_TIERS)) - 1][1]


def total_afk_rate(silver_lvl: int, gold_lvl: int, global_lvl: int) -> float:
    return silver_rate_at(silver_lvl) + gold_rate_at(gold_lvl) + global_rate_at(global_lvl)


# Branches catalog for UI / API
UPGRADE_BRANCHES = {
    "damage":          {"name": "⚒ Молот",        "description": "Урон за клик",                   "unit": "dmg",  "tiers": DAMAGE_TIERS},
    "crit":            {"name": "🎯 Крит",         "description": "Шанс критического удара",        "unit": "%",    "tiers": CRIT_TIERS},
    "crit_power":      {"name": "💥 Сила Крита",   "description": "Множитель урона крита (база x3)", "unit": "x",    "tiers": CRIT_POWER_TIERS},
    "luck":            {"name": "🍀 Удача",        "description": "Бонус к particles",              "unit": "%",    "tiers": LUCK_TIERS},
    "tier_luck":       {"name": "🔮 Везение",      "description": "Шанс спавна лучшего тира",       "unit": "%",    "tiers": TIER_LUCK_TIERS},
    "stattrak_hunter": {"name": "🎯 Охотник ST™",  "description": "Шанс ST™-оружия (особый дроп)",  "unit": "%",    "tiers": STATTRAK_HUNTER_TIERS},
    "offline_cap":     {"name": "⏰ Ночная смена", "description": "Часы AFK + daily cap",           "unit": "ч",    "tiers": OFFLINE_TIERS},
    "silver":          {"name": "🥉 Silver-бот",   "description": "Автофарм",                      "unit": "/сек", "tiers": SILVER_UPGRADE_TIERS, "unlock_cost": SILVER_UNLOCK_COST},
    "gold":            {"name": "🥈 Gold-бот",     "description": "Автофарм+",                     "unit": "/сек", "tiers": GOLD_UPGRADE_TIERS,   "unlock_cost": GOLD_UNLOCK_COST},
    "global":          {"name": "🥇 Global-бот",   "description": "Автофарм++",                    "unit": "/сек", "tiers": GLOBAL_UPGRADE_TIERS, "unlock_cost": GLOBAL_UNLOCK_COST},
}

# Dynamic max_level populated from actual tiers
for _key, _cfg in UPGRADE_BRANCHES.items():
    _cfg["max_level"] = len(_cfg["tiers"])

# ============================================================
# CONSTANTS
# ============================================================

EXCHANGE_RATE = 10  # 10 particles = 1 coin
AFK_DAILY_CAP_BASE = 30000
MIN_HIT_INTERVAL_MS = 70  # max ~14 hits/sec allowed (anti-autoclick is softer, UX)
STATTRAK_SPAWN_CHANCE_BASE = 0.05
STATTRAK_PARTICLE_MULT = 2.0

# Daily cap extender — offline_cap_level also grows AFK daily cap
AFK_DAILY_CAP_PER_OFFLINE_LEVEL = 15000  # +15k per level → L8 = 30k + 120k = 150k

def afk_daily_cap_for(offline_cap_level: int) -> int:
    return AFK_DAILY_CAP_BASE + max(0, offline_cap_level) * AFK_DAILY_CAP_PER_OFFLINE_LEVEL


# Legacy alias kept for minimal diff (not used in new code but referenced elsewhere)
AFK_DAILY_CAP = AFK_DAILY_CAP_BASE


# ============================================================
# STATE I/O
# ============================================================

async def ensure_forge_user(tg_id: int) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "insert into forge_users (tg_id) values ($1) on conflict do nothing",
            tg_id,
        )


async def get_state(tg_id: int) -> dict:
    await ensure_forge_user(tg_id)
    afk_gained, afk_breaks = await _tick_afk(tg_id)
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "select f.*, s.full_name, s.weapon, s.skin_name, s.rarity, s.rarity_color, "
            "s.image_url, s.category "
            "from forge_users f "
            "left join economy_skins_catalog s on s.id = f.current_skin_id "
            "where f.tg_id = $1",
            tg_id,
        )
    if row is None:
        return {}

    # If no current weapon, spawn one
    if row["current_skin_id"] is None:
        await _spawn_weapon(tg_id)
        return await get_state(tg_id)

    # Prestige bonuses — applied on top of base values for display
    hp_pow_lvl = int(row["hammer_power_lvl"] or 0) if "hammer_power_lvl" in row else 0
    dust_lvl = int(row["dust_magic_lvl"] or 0) if "dust_magic_lvl" in row else 0
    bot_tune_lvl = int(row["bot_tune_lvl"] or 0) if "bot_tune_lvl" in row else 0
    sharpen_lvl = int(row["sharpen_lvl"] or 0) if "sharpen_lvl" in row else 0
    fortune_lvl = int(row["fortune_lvl"] or 0) if "fortune_lvl" in row else 0
    gear = _parse_gear_affixes(row["gear_affixes"]) if "gear_affixes" in row else {}

    # Gear multipliers (% values from items are whole percentage points)
    g_dmg   = 1 + float(gear.get("dmg", 0)) / 100
    g_part  = 1 + float(gear.get("particles", 0)) / 100
    g_crit  = float(gear.get("crit", 0))
    g_crit_dmg = 1 + float(gear.get("crit_dmg", 0)) / 100
    g_afk   = 1 + float(gear.get("afk", 0)) / 100
    g_tier_luck = float(gear.get("tier_luck", 0)) / 100
    g_st_hunter = float(gear.get("st_hunter", 0)) / 100
    g_afk_cap = 1 + float(gear.get("afk_cap", 0)) / 100
    g_offline_h = int(gear.get("offline_hours", 0))

    dmg_base = damage_at(int(row["damage_level"]))
    dmg = int(dmg_base * _prestige.hammer_power_mult(hp_pow_lvl) * g_dmg)
    crit_c = crit_chance_at(int(row["crit_level"])) + _prestige.sharpen_flat_crit(sharpen_lvl) + int(g_crit)
    crit_mult = crit_multiplier_at(int(row["crit_power_level"] or 0)) * g_crit_dmg
    luck_b = luck_bonus_at(int(row["luck_level"]))
    tier_luck = tier_luck_at(int(row["tier_luck_level"] or 0)) + _prestige.fortune_flat_tier_luck(fortune_lvl) + g_tier_luck
    st_hunt = stattrak_chance_at(int(row["stattrak_hunter_level"] or 0)) + g_st_hunter
    silver_lvl = int(row["silver_level"])
    gold_lvl = int(row["gold_level"])
    global_lvl = int(row["global_level"])
    offline_cap_lvl = int(row["offline_cap_level"])
    afk_rate = total_afk_rate(silver_lvl, gold_lvl, global_lvl) * _prestige.bot_tune_mult(bot_tune_lvl) * g_afk
    daily_cap_today = int(afk_daily_cap_for(offline_cap_lvl) * g_afk_cap)
    offline_h_total = offline_hours_at(offline_cap_lvl) + g_offline_h

    return {
        "particles": int(row["particles"]),
        "total_particles_earned": int(row["total_particles_earned"]),
        "total_breaks": int(row["total_breaks"]),
        "total_clicks": int(row["total_clicks"]),
        "total_crits": int(row["total_crits"]),
        "levels": {
            "damage": int(row["damage_level"]),
            "crit": int(row["crit_level"]),
            "crit_power": int(row["crit_power_level"] or 0),
            "luck": int(row["luck_level"]),
            "tier_luck": int(row["tier_luck_level"] or 0),
            "stattrak_hunter": int(row["stattrak_hunter_level"] or 0),
            "offline_cap": offline_cap_lvl,
            "silver": silver_lvl,
            "gold": gold_lvl,
            "global": global_lvl,
        },
        "effects": {
            "damage": dmg,
            "crit_chance": crit_c,
            "crit_multiplier": crit_mult,
            "luck_bonus_pct": luck_b,
            "tier_luck_pct": round(tier_luck * 100, 1),
            "stattrak_chance_pct": round(st_hunt * 100, 1),
            "afk_rate_per_sec": round(afk_rate, 2),
            "offline_cap_hours": offline_h_total,
            "afk_daily_cap": daily_cap_today,
        },
        "prestige": {
            "level": int(row["prestige_level"] or 0) if "prestige_level" in row else 0,
            "jetons": int(row["jetons"] or 0) if "jetons" in row else 0,
        },
        "afk": {
            "buffer": 0,
            "just_gained": int(afk_gained),
            "just_broken": int(afk_breaks),
            "daily_earned": int(row["daily_afk_earned"] or 0),
            "daily_cap": daily_cap_today,
        },
        "weapon": {
            "skin_id": int(row["current_skin_id"]),
            "full_name": row["full_name"],
            "weapon": row["weapon"],
            "skin_name": row["skin_name"],
            "rarity": row["rarity"],
            "rarity_color": row["rarity_color"],
            "image_url": row["image_url"],
            "category": row["category"],
            "tier": row["current_weapon_tier"],
            "max_hp": int(row["current_weapon_max_hp"]),
            "hp": int(row["current_weapon_hp"]),
            "particles_reward": int(row["current_weapon_particles"]),
            "stattrak": bool(row["current_weapon_stattrak"]),
        },
    }


async def _spawn_weapon(tg_id: int, levels: dict | None = None) -> None:
    """Pick a new weapon, honoring damage_level gate, tier_luck (boost to higher tier)
    and stattrak_hunter (boosted ST spawn chance).

    If `levels` is provided (dict with damage_level, tier_luck_level,
    stattrak_hunter_level) we skip the preliminary SELECT — used by hit_batch
    which already has these in hand. Saves one query per weapon break."""
    if levels is None:
        async with pool().acquire() as conn:
            row = await conn.fetchrow(
                "select damage_level, tier_luck_level, stattrak_hunter_level, "
                "fortune_lvl, gear_affixes from forge_users where tg_id = $1", tg_id,
            )
        dmg_lvl = int(row["damage_level"]) if row else 0
        tier_luck_lvl = int(row["tier_luck_level"] or 0) if row else 0
        st_lvl = int(row["stattrak_hunter_level"] or 0) if row else 0
        fortune_lvl = int(row["fortune_lvl"] or 0) if row else 0
        gear = _parse_gear_affixes(row["gear_affixes"]) if row else {}
    else:
        dmg_lvl = int(levels.get("damage_level", 0))
        tier_luck_lvl = int(levels.get("tier_luck_level", 0) or 0)
        st_lvl = int(levels.get("stattrak_hunter_level", 0) or 0)
        fortune_lvl = int(levels.get("fortune_lvl", 0) or 0)
        gear = levels.get("gear_affixes") or {}
        if not isinstance(gear, dict):
            gear = _parse_gear_affixes(gear)

    gear_tier_luck = float(gear.get("tier_luck", 0)) / 100
    gear_st_hunter = float(gear.get("st_hunter", 0)) / 100

    tier = _roll_tier(damage_level=dmg_lvl)
    tier_luck_pct = tier_luck_at(tier_luck_lvl) + _prestige.fortune_flat_tier_luck(fortune_lvl) + gear_tier_luck
    if tier_luck_pct > 0 and random.random() < tier_luck_pct:
        up_order = ["pistol", "rifle", "awp", "golden", "legendary"]
        try:
            idx = up_order.index(tier)
            if idx + 1 < len(up_order):
                next_tier = up_order[idx + 1]
                if dmg_lvl >= TIER_CONFIG[next_tier].get("min_damage_level", 0):
                    tier = next_tier
        except ValueError:
            pass

    cfg = TIER_CONFIG[tier]
    skin_pool = await _get_skin_pool(tier)
    if not skin_pool:
        for fallback in ["awp", "rifle", "pistol"]:
            pool_fb = await _get_skin_pool(fallback)
            if pool_fb:
                skin_pool = pool_fb
                tier = fallback
                cfg = TIER_CONFIG[tier]
                break
    if not skin_pool:
        return
    skin_id = random.choice(skin_pool)
    st_chance = stattrak_chance_at(st_lvl) + gear_st_hunter
    stattrak = random.random() < st_chance
    particles = cfg["particles"]
    if stattrak:
        particles = int(particles * STATTRAK_PARTICLE_MULT)
    hp = cfg["hp"]
    async with pool().acquire() as conn:
        await conn.execute(
            """
            update forge_users set
              current_skin_id = $2,
              current_weapon_tier = $3,
              current_weapon_max_hp = $4,
              current_weapon_hp = $4,
              current_weapon_particles = $5,
              current_weapon_stattrak = $6,
              updated_at = now()
            where tg_id = $1
            """,
            tg_id, skin_id, tier, hp, particles, stattrak,
        )


# ============================================================
# HIT (tap)
# ============================================================

async def hit(tg_id: int) -> dict:
    now = datetime.now(timezone.utc)
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select damage_level, crit_level, crit_power_level, luck_level, "
                "current_weapon_hp, current_weapon_max_hp, current_weapon_particles, "
                "current_weapon_stattrak, last_hit_at, total_clicks, total_crits, "
                "hammer_power_lvl, dust_magic_lvl, sharpen_lvl, gear_affixes "
                "from forge_users where tg_id = $1 for update",
                tg_id,
            )
            if row is None:
                return {"ok": False, "error": "No state — open the forge first"}
            if row["current_weapon_hp"] is None:
                return {"ok": False, "error": "No weapon — open the forge first"}

            # Anti-abuse: minimum interval between hits
            if row["last_hit_at"] is not None:
                gap_ms = (now - row["last_hit_at"]).total_seconds() * 1000
                if gap_ms < MIN_HIT_INTERVAL_MS:
                    return {"ok": False, "error": "Too fast", "retry_after_ms": int(MIN_HIT_INTERVAL_MS - gap_ms)}

            dmg_lvl = int(row["damage_level"])
            crit_lvl = int(row["crit_level"])
            crit_power_lvl = int(row["crit_power_level"] or 0)
            luck_lvl = int(row["luck_level"])
            hp_pow_mult = _prestige.hammer_power_mult(int(row["hammer_power_lvl"] or 0))
            dust_mult = _prestige.dust_magic_mult(int(row["dust_magic_lvl"] or 0))
            sharpen_flat = _prestige.sharpen_flat_crit(int(row["sharpen_lvl"] or 0))
            _gear = _parse_gear_affixes(row["gear_affixes"])
            gear_dmg = 1 + float(_gear.get("dmg", 0)) / 100
            gear_part = 1 + float(_gear.get("particles", 0)) / 100
            gear_crit = int(_gear.get("crit", 0))
            gear_crit_dmg = 1 + float(_gear.get("crit_dmg", 0)) / 100

            base_dmg = int(damage_at(dmg_lvl) * hp_pow_mult * gear_dmg)
            crit_chance = crit_chance_at(crit_lvl) + sharpen_flat + gear_crit
            crit_mult = crit_multiplier_at(crit_power_lvl) * gear_crit_dmg
            is_crit = random.randint(1, 100) <= crit_chance
            damage = int(base_dmg * crit_mult) if is_crit else base_dmg

            # МОЛОТ ИГОРЯ — instant break: any hit kills the current weapon
            if int(_gear.get("instant_break", 0)) > 0:
                damage = int(row["current_weapon_hp"])

            new_hp = int(row["current_weapon_hp"]) - damage
            broken = new_hp <= 0
            particles_earned = 0
            if broken:
                new_hp = 0
                base_particles = int(row["current_weapon_particles"])
                luck_b = luck_bonus_at(luck_lvl)
                particles_earned = int(base_particles * (1 + luck_b / 100) * dust_mult * gear_part)

            # Update click counters + last_hit
            await conn.execute(
                """
                update forge_users set
                  current_weapon_hp = $2,
                  total_clicks = total_clicks + 1,
                  total_crits = total_crits + $3,
                  last_hit_at = $4,
                  updated_at = now()
                where tg_id = $1
                """,
                tg_id, new_hp, 1 if is_crit else 0, now,
            )

            if broken:
                await conn.execute(
                    """
                    update forge_users set
                      particles = particles + $2,
                      total_particles_earned = total_particles_earned + $2,
                      run_particles_earned = run_particles_earned + $2,
                      total_breaks = total_breaks + 1
                    where tg_id = $1
                    """,
                    tg_id, particles_earned,
                )

    # Spawn next weapon outside the txn
    if broken:
        await _spawn_weapon(tg_id)
    return {
        "ok": True,
        "damage": damage,
        "crit": is_crit,
        "new_hp": new_hp,
        "broken": broken,
        "particles_earned": particles_earned,
    }


# ============================================================
# HIT BATCH — many clicks in one transaction
# ============================================================

MAX_BATCH_SIZE = 30  # clients shouldn't be able to claim > ~10 clicks/sec for long


async def hit_batch(tg_id: int, count: int) -> dict:
    """Apply up to `count` clicks in a single transaction. Rate-limited by elapsed
    time since last batch. Returns aggregate + inline state (no extra get_state
    call — saves ~50ms by avoiding a second tx + _tick_afk rerun)."""
    if count <= 0:
        return {"ok": False, "error": "Zero count"}
    count = min(int(count), MAX_BATCH_SIZE)

    now = datetime.now(timezone.utc)
    needs_new_spawn = False
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select damage_level, crit_level, crit_power_level, luck_level, "
                "tier_luck_level, stattrak_hunter_level, "
                "current_weapon_hp, current_weapon_max_hp, current_weapon_particles, "
                "current_weapon_stattrak, last_hit_at, "
                "particles, total_breaks, "
                "hammer_power_lvl, dust_magic_lvl, sharpen_lvl, fortune_lvl, "
                "gear_affixes "
                "from forge_users where tg_id = $1 for update",
                tg_id,
            )
            if row is None:
                return {"ok": False, "error": "No state — open the forge first"}
            if row["current_weapon_hp"] is None:
                return {"ok": False, "error": "No weapon — open the forge first"}

            if row["last_hit_at"] is not None:
                gap_ms = (now - row["last_hit_at"]).total_seconds() * 1000
                max_allowed = max(1, int(gap_ms / MIN_HIT_INTERVAL_MS))
                count = min(count, max_allowed)
            if count <= 0:
                return {"ok": False, "error": "Too fast"}

            dmg_lvl = int(row["damage_level"])
            crit_lvl = int(row["crit_level"])
            crit_power_lvl = int(row["crit_power_level"] or 0)
            luck_lvl = int(row["luck_level"])
            # Prestige bonuses
            hp_pow_mult = _prestige.hammer_power_mult(int(row["hammer_power_lvl"] or 0))
            dust_mult = _prestige.dust_magic_mult(int(row["dust_magic_lvl"] or 0))
            sharpen = _prestige.sharpen_flat_crit(int(row["sharpen_lvl"] or 0))
            # Gear affixes
            _gear = _parse_gear_affixes(row["gear_affixes"])
            gear_dmg = 1 + float(_gear.get("dmg", 0)) / 100
            gear_part = 1 + float(_gear.get("particles", 0)) / 100
            gear_crit = int(_gear.get("crit", 0))
            gear_crit_dmg = 1 + float(_gear.get("crit_dmg", 0)) / 100

            base_dmg = int(damage_at(dmg_lvl) * hp_pow_mult * gear_dmg)
            crit_chance = crit_chance_at(crit_lvl) + sharpen + gear_crit
            crit_mult = crit_multiplier_at(crit_power_lvl) * gear_crit_dmg
            luck_b = luck_bonus_at(luck_lvl)

            cur_hp = int(row["current_weapon_hp"])
            cur_max_hp = int(row["current_weapon_max_hp"])
            cur_reward = int(row["current_weapon_particles"])

            total_damage = 0
            total_crits = 0
            breaks_count = 0
            particles_gained = 0

            instant_break = int(_gear.get("instant_break", 0)) > 0
            for _ in range(count):
                is_crit = random.randint(1, 100) <= crit_chance
                damage = int(base_dmg * crit_mult) if is_crit else base_dmg
                # МОЛОТ ИГОРЯ — kill the current weapon in one hit
                if instant_break:
                    damage = max(damage, cur_hp)
                total_damage += damage
                if is_crit:
                    total_crits += 1
                cur_hp -= damage
                if cur_hp <= 0:
                    breaks_count += 1
                    particles_gained += int(cur_reward * (1 + luck_b / 100) * dust_mult * gear_part)
                    needs_new_spawn = True
                    cur_hp = 0
                    break

            await conn.execute(
                """
                update forge_users set
                  current_weapon_hp = $2,
                  total_clicks = total_clicks + $3,
                  total_crits = total_crits + $4,
                  last_hit_at = $5,
                  updated_at = now()
                where tg_id = $1
                """,
                tg_id, cur_hp, count, total_crits, now,
            )
            if breaks_count > 0:
                await conn.execute(
                    """
                    update forge_users set
                      particles = particles + $2,
                      total_particles_earned = total_particles_earned + $2,
                      run_particles_earned = run_particles_earned + $2,
                      total_breaks = total_breaks + $3
                    where tg_id = $1
                    """,
                    tg_id, particles_gained, breaks_count,
                )

    new_particles = int(row["particles"]) + particles_gained
    new_total_breaks = int(row["total_breaks"]) + breaks_count

    # If weapon broke — spawn new + fetch its display data in ONE query
    weapon_obj: dict | None = None
    if needs_new_spawn:
        # Reuse levels from the initial SELECT — saves an extra query inside _spawn_weapon.
        await _spawn_weapon(tg_id, levels={
            "damage_level": row["damage_level"],
            "tier_luck_level": row["tier_luck_level"],
            "stattrak_hunter_level": row["stattrak_hunter_level"],
            "fortune_lvl": row["fortune_lvl"],
            "gear_affixes": row["gear_affixes"],
        })
        async with pool().acquire() as conn:
            wrow = await conn.fetchrow(
                "select f.current_skin_id, f.current_weapon_tier, f.current_weapon_hp, "
                "f.current_weapon_max_hp, f.current_weapon_particles, f.current_weapon_stattrak, "
                "s.full_name, s.weapon, s.skin_name, s.rarity, s.rarity_color, "
                "s.image_url, s.category "
                "from forge_users f "
                "left join economy_skins_catalog s on s.id = f.current_skin_id "
                "where f.tg_id = $1",
                tg_id,
            )
        if wrow and wrow["current_skin_id"] is not None:
            weapon_obj = {
                "skin_id": int(wrow["current_skin_id"]),
                "full_name": wrow["full_name"],
                "weapon": wrow["weapon"],
                "skin_name": wrow["skin_name"],
                "rarity": wrow["rarity"],
                "rarity_color": wrow["rarity_color"],
                "image_url": wrow["image_url"],
                "category": wrow["category"],
                "tier": wrow["current_weapon_tier"],
                "max_hp": int(wrow["current_weapon_max_hp"]),
                "hp": int(wrow["current_weapon_hp"]),
                "particles_reward": int(wrow["current_weapon_particles"]),
                "stattrak": bool(wrow["current_weapon_stattrak"]),
            }
    else:
        # Same weapon — client has its metadata locally, we only need fresh HP.
        weapon_obj = {"hp": cur_hp, "max_hp": cur_max_hp}

    return {
        "ok": True,
        "applied": count,
        "damage": total_damage,
        "crits": total_crits,
        "breaks": breaks_count,
        "particles_earned": particles_gained,
        "particles": new_particles,
        "total_breaks": new_total_breaks,
        "weapon": weapon_obj,
        "weapon_swapped": needs_new_spawn,
    }


# ============================================================
# AFK tick (called on state fetch)
# ============================================================

async def _tick_afk(tg_id: int) -> tuple[int, int]:
    """AFK bot = auto-clicker. Rate is DAMAGE per second. Deals damage to
    current weapon; when HP hits 0 → break + credit particles + spawn next.
    Daily cap applies. Returns (particles_gained, weapons_broken)."""
    now = datetime.now(timezone.utc)
    today = now.date()
    needs_new_spawn = False
    particles_gained = 0
    breaks = 0
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select silver_level, gold_level, global_level, offline_cap_level, "
                "last_afk_tick_at, daily_afk_day, daily_afk_earned, damage_level, "
                "luck_level, tier_luck_level, stattrak_hunter_level, "
                "current_weapon_hp, current_weapon_particles, "
                "bot_tune_lvl, dust_magic_lvl, fortune_lvl, gear_affixes "
                "from forge_users where tg_id = $1 for update",
                tg_id,
            )
            if row is None:
                return (0, 0)
            # Bot tune multiplier applied to combined rate
            bot_tune_mult = _prestige.bot_tune_mult(int(row["bot_tune_lvl"] or 0))
            # Gear affixes
            _gear = _parse_gear_affixes(row["gear_affixes"])
            gear_afk = 1 + float(_gear.get("afk", 0)) / 100
            gear_part = 1 + float(_gear.get("particles", 0)) / 100
            gear_tier_luck = float(_gear.get("tier_luck", 0)) / 100
            gear_afk_cap_mult = 1 + float(_gear.get("afk_cap", 0)) / 100
            gear_offline_h = int(_gear.get("offline_hours", 0))
            rate = total_afk_rate(
                int(row["silver_level"]), int(row["gold_level"]), int(row["global_level"])
            ) * bot_tune_mult * gear_afk
            if rate <= 0:
                return (0, 0)

            offline_cap = (offline_hours_at(int(row["offline_cap_level"])) + gear_offline_h) * 3600
            last_tick = row["last_afk_tick_at"]
            elapsed_raw = 0.0 if last_tick is None else (now - last_tick).total_seconds()
            elapsed = min(elapsed_raw, offline_cap)
            damage_budget = int(rate * elapsed)
            if damage_budget <= 0:
                # Fractional damage (<1) — do NOT update last_afk_tick_at so partial
                # time accumulates across polls. Otherwise rates <1/sec never tick.
                if last_tick is None:
                    await conn.execute(
                        "update forge_users set last_afk_tick_at = $2 where tg_id = $1",
                        tg_id, now,
                    )
                return (0, 0)

            # Advance tick by the exact time it took to produce the integer damage
            # budget, preserving the fractional remainder for the next poll.
            time_used = damage_budget / rate
            if last_tick is None or elapsed_raw > offline_cap:
                tick_advance_to = now
            else:
                tick_advance_to = last_tick + timedelta(seconds=time_used)
                if tick_advance_to > now:
                    tick_advance_to = now

            daily_earned = 0 if row["daily_afk_day"] != today else int(row["daily_afk_earned"] or 0)
            daily_cap = int(afk_daily_cap_for(int(row["offline_cap_level"] or 0)) * gear_afk_cap_mult)
            cap_left = max(0, daily_cap - daily_earned)

            damage_level = int(row["damage_level"])
            tier_luck_lvl = int(row["tier_luck_level"] or 0)
            luck_mult = 1.0 + luck_bonus_at(int(row["luck_level"])) / 100.0
            # Prestige + gear bonuses for AFK farming
            dust_mult = _prestige.dust_magic_mult(int(row["dust_magic_lvl"] or 0)) * gear_part
            fortune_flat = _prestige.fortune_flat_tier_luck(int(row["fortune_lvl"] or 0))
            tier_luck_pct_afk = tier_luck_at(tier_luck_lvl) + fortune_flat + gear_tier_luck
            cur_hp = int(row["current_weapon_hp"]) if row["current_weapon_hp"] is not None else 0
            cur_particles = int(row["current_weapon_particles"]) if row["current_weapon_particles"] is not None else 0

            if cur_hp <= 0:
                needs_new_spawn = True
                cur_hp = 50
                cur_particles = 2

            # Simulate breaks in a loop using tier averages for future weapons
            while damage_budget > 0 and cap_left > 0:
                if damage_budget >= cur_hp:
                    damage_budget -= cur_hp
                    # Apply luck bonus (upgrade) + dust magic (prestige)
                    lucky_reward = int(cur_particles * luck_mult * dust_mult)
                    award = min(lucky_reward, cap_left)
                    particles_gained += award
                    cap_left -= award
                    breaks += 1
                    needs_new_spawn = True
                    tier = _roll_tier(damage_level)
                    # Apply tier_luck boost during AFK simulation too
                    if tier_luck_pct_afk > 0 and random.random() < tier_luck_pct_afk:
                        up_order = ["pistol", "rifle", "awp", "golden", "legendary"]
                        try:
                            idx = up_order.index(tier)
                            if idx + 1 < len(up_order):
                                next_tier = up_order[idx + 1]
                                if damage_level >= TIER_CONFIG[next_tier].get("min_damage_level", 0):
                                    tier = next_tier
                        except ValueError:
                            pass
                    cfg = TIER_CONFIG[tier]
                    cur_hp = cfg["hp"]
                    cur_particles = cfg["particles"]
                else:
                    cur_hp -= damage_budget
                    damage_budget = 0

            new_daily = daily_earned + particles_gained
            if breaks > 0:
                await conn.execute(
                    """
                    update forge_users set
                      particles = particles + $2,
                      total_particles_earned = total_particles_earned + $2,
                      run_particles_earned = run_particles_earned + $2,
                      total_breaks = total_breaks + $3,
                      daily_afk_day = $4, daily_afk_earned = $5,
                      last_afk_tick_at = $6,
                      current_skin_id = null, current_weapon_tier = null,
                      current_weapon_hp = null, current_weapon_max_hp = null,
                      current_weapon_particles = null, current_weapon_stattrak = false
                    where tg_id = $1
                    """,
                    tg_id, particles_gained, breaks, today, new_daily, tick_advance_to,
                )
            else:
                await conn.execute(
                    """
                    update forge_users set
                      current_weapon_hp = $2,
                      daily_afk_day = $3, daily_afk_earned = $4,
                      last_afk_tick_at = $5
                    where tg_id = $1
                    """,
                    tg_id, cur_hp, today, new_daily, tick_advance_to,
                )
    if needs_new_spawn:
        await _spawn_weapon(tg_id)
    return (particles_gained, breaks)


async def skip_weapon(tg_id: int) -> dict:
    """Escape hatch: give up the current weapon. Refund = 10% of base particles,
    PRORATED by HP damage already dealt. Fresh (undamaged) weapon → 0 refund, so
    spam-skipping can't be milked for free particles."""
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select current_weapon_particles, current_weapon_hp, "
                "current_weapon_max_hp, current_skin_id "
                "from forge_users where tg_id = $1 for update", tg_id,
            )
            if row is None or row["current_skin_id"] is None:
                return {"ok": False, "error": "No weapon to skip"}
            base_particles = int(row["current_weapon_particles"] or 0)
            max_hp = int(row["current_weapon_max_hp"] or 0)
            cur_hp = int(row["current_weapon_hp"] or 0)
            if max_hp <= 0:
                dmg_pct = 0.0
            else:
                dmg_pct = max(0.0, min(1.0, (max_hp - cur_hp) / max_hp))
            refund = int(base_particles * dmg_pct * 0.10)  # 10% of proportional reward
            await conn.execute(
                "update forge_users set "
                "particles = particles + $2, "
                "total_particles_earned = total_particles_earned + $2, "
                "current_skin_id = null, current_weapon_hp = null, "
                "current_weapon_max_hp = null, current_weapon_tier = null, "
                "current_weapon_particles = null, current_weapon_stattrak = false "
                "where tg_id = $1",
                tg_id, refund,
            )
    await _spawn_weapon(tg_id)
    return {"ok": True, "refund": refund, "damage_pct": round(dmg_pct * 100, 1)}


async def claim_afk(tg_id: int) -> dict:
    """Legacy endpoint. AFK is now auto-applied on every state fetch;
    this just triggers a tick and reports what was gained."""
    gained, breaks = await _tick_afk(tg_id)
    return {"ok": True, "claimed": gained, "breaks": breaks}


# ============================================================
# UPGRADE
# ============================================================

async def buy_upgrade(tg_id: int, branch: str) -> dict:
    if branch not in UPGRADE_BRANCHES:
        return {"ok": False, "error": "Unknown branch"}
    cfg = UPGRADE_BRANCHES[branch]
    column = {
        "damage": "damage_level", "crit": "crit_level", "luck": "luck_level",
        "crit_power": "crit_power_level",
        "stattrak_hunter": "stattrak_hunter_level",
        "tier_luck": "tier_luck_level",
        "offline_cap": "offline_cap_level",
        "silver": "silver_level", "gold": "gold_level", "global": "global_level",
    }[branch]

    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                f"select particles, {column} as lvl, discount_lvl from forge_users where tg_id = $1 for update",
                tg_id,
            )
            if row is None:
                return {"ok": False, "error": "No state"}
            lvl = int(row["lvl"])
            balance = int(row["particles"])
            discount = _prestige.discount_mult(int(row["discount_lvl"] or 0))

            # AFK bots have unlock step
            if branch in ("silver", "gold", "global") and lvl < 0:
                unlock_cost = int(cfg.get("unlock_cost", 0) * discount)
                if balance < unlock_cost:
                    return {"ok": False, "error": "Not enough particles to unlock", "cost": unlock_cost}
                await conn.execute(
                    f"update forge_users set particles = particles - $2, {column} = 0 "
                    f"where tg_id = $1",
                    tg_id, unlock_cost,
                )
                return {"ok": True, "unlocked": True, "branch": branch, "cost": unlock_cost, "new_level": 0, "new_balance": balance - unlock_cost}

            max_lvl = cfg["max_level"]
            if lvl >= max_lvl:
                return {"ok": False, "error": "Already max level"}
            tiers = cfg["tiers"]
            next_level, new_effect, base_cost = tiers[lvl]  # tier idx = current level
            cost = int(base_cost * discount)
            if balance < cost:
                return {"ok": False, "error": "Not enough particles", "cost": cost}
            await conn.execute(
                f"update forge_users set particles = particles - $2, {column} = $3 "
                f"where tg_id = $1",
                tg_id, cost, next_level,
            )
    return {"ok": True, "branch": branch, "new_level": next_level, "effect": new_effect, "cost": cost, "new_balance": balance - cost}


# ============================================================
# EXCHANGE particles → coins
# ============================================================

async def exchange(tg_id: int, particle_amount: int) -> dict:
    if particle_amount < EXCHANGE_RATE:
        return {"ok": False, "error": f"Min {EXCHANGE_RATE} particles"}
    particle_amount = (particle_amount // EXCHANGE_RATE) * EXCHANGE_RATE
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select particles, gear_affixes from forge_users where tg_id = $1 for update",
                tg_id,
            )
            if row is None or int(row["particles"]) < particle_amount:
                return {"ok": False, "error": "Not enough particles"}
            gear = _parse_gear_affixes(row["gear_affixes"])
            coin_gain_mult = 1 + float(gear.get("coin_gain", 0)) / 100
            coins_given = int((particle_amount // EXCHANGE_RATE) * coin_gain_mult)
            await conn.execute(
                "update forge_users set particles = particles - $2 where tg_id = $1",
                tg_id, particle_amount,
            )
            new_bal_row = await conn.fetchrow(
                "update economy_users set balance = balance + $2, total_earned = total_earned + $2 "
                "where tg_id = $1 returning balance",
                tg_id, coins_given,
            )
            new_bal = int(new_bal_row["balance"]) if new_bal_row else 0
            await conn.execute(
                "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                "values ($1, $2, 'forge_exchange', $3, $4)",
                tg_id, coins_given, f"{particle_amount} particles", new_bal,
            )
    return {"ok": True, "coins": coins_given, "particles_spent": particle_amount, "new_balance": new_bal}


# ============================================================
# UPGRADE TREE INTROSPECTION (for UI)
# ============================================================

async def leaderboard(limit: int = 20) -> list[dict]:
    """Top forgers by total_particles_earned (lifetime grind)."""
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            select f.tg_id, f.particles, f.total_particles_earned, f.total_breaks,
                   f.total_crits, f.total_clicks, f.prestige_level,
                   u.username, u.first_name
            from forge_users f
            left join users u on u.tg_id = f.tg_id
            where f.total_particles_earned > 0
            order by f.prestige_level desc, f.total_particles_earned desc
            limit $1
            """,
            limit,
        )
    return [
        {
            "tg_id": int(r["tg_id"]),
            "username": r["username"],
            "first_name": r["first_name"],
            "particles": int(r["particles"]),
            "total_earned": int(r["total_particles_earned"]),
            "total_breaks": int(r["total_breaks"]),
            "total_crits": int(r["total_crits"] or 0),
            "total_clicks": int(r["total_clicks"] or 0),
            "prestige": int(r["prestige_level"] or 0),
        }
        for r in rows
    ]


def get_branches_info() -> list[dict]:
    """Return branch metadata for the upgrade UI."""
    out = []
    for key, cfg in UPGRADE_BRANCHES.items():
        tiers = [{"level": t[0], "effect": t[1], "cost": t[2]} for t in cfg["tiers"]]
        entry = {
            "key": key,
            "name": cfg["name"],
            "description": cfg["description"],
            "max_level": cfg["max_level"],
            "tiers": tiers,
        }
        if "unlock_cost" in cfg:
            entry["unlock_cost"] = cfg["unlock_cost"]
        out.append(entry)
    return out
