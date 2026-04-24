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

log = logging.getLogger(__name__)


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


# Damage: 25 levels, 1 → ~200 damage. Total cost ~350k particles.
DAMAGE_TIERS = _build_tiers(
    max_level=25,
    effect_fn=lambda L: 1 + L * 0.8 + (L ** 1.55) * 0.25,  # smooth growth
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
    effect_fn=lambda L: 0.3 + L * 0.12,        # +0.12/sec per level → max 2.7/sec
    cost_fn=lambda L: 100 * (1.35 ** (L - 1)),
    round_effect=False,
)
GOLD_UNLOCK_COST = 12000
GOLD_BASE_RATE = 1.0
GOLD_UPGRADE_TIERS = _build_tiers(
    max_level=20,
    effect_fn=lambda L: 1.0 + L * 0.5,        # +0.5/sec per level → max 11/sec
    cost_fn=lambda L: 800 * (1.38 ** (L - 1)),
    round_effect=False,
)
GLOBAL_UNLOCK_COST = 100000
GLOBAL_BASE_RATE = 4.0
GLOBAL_UPGRADE_TIERS = _build_tiers(
    max_level=20,
    effect_fn=lambda L: 4.0 + L * 1.5,        # +1.5/sec per level → max 34/sec
    cost_fn=lambda L: 8000 * (1.4 ** (L - 1)),
    round_effect=False,
)


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
    "damage":      {"name": "⚒ Молот",        "description": "Урон за клик",       "unit": "dmg",   "tiers": DAMAGE_TIERS},
    "crit":        {"name": "🎯 Крит",         "description": "Шанс x3 урона",      "unit": "%",     "tiers": CRIT_TIERS},
    "luck":        {"name": "🍀 Удача",        "description": "Бонус к particles",  "unit": "%",     "tiers": LUCK_TIERS},
    "offline_cap": {"name": "⏰ Сон бота",     "description": "Часы оффлайн фарма",  "unit": "ч",     "tiers": OFFLINE_TIERS},
    "silver":      {"name": "🥉 Silver-бот",   "description": "Автофарм",           "unit": "/сек",  "tiers": SILVER_UPGRADE_TIERS, "unlock_cost": SILVER_UNLOCK_COST},
    "gold":        {"name": "🥈 Gold-бот",     "description": "Автофарм+",          "unit": "/сек",  "tiers": GOLD_UPGRADE_TIERS,   "unlock_cost": GOLD_UNLOCK_COST},
    "global":      {"name": "🥇 Global-бот",   "description": "Автофарм++",         "unit": "/сек",  "tiers": GLOBAL_UPGRADE_TIERS, "unlock_cost": GLOBAL_UNLOCK_COST},
}

# Dynamic max_level populated from actual tiers
for _key, _cfg in UPGRADE_BRANCHES.items():
    _cfg["max_level"] = len(_cfg["tiers"])

# ============================================================
# CONSTANTS
# ============================================================

EXCHANGE_RATE = 10  # 10 particles = 1 coin
AFK_DAILY_CAP = 30000
MIN_HIT_INTERVAL_MS = 70  # max ~14 hits/sec allowed (anti-autoclick is softer, UX)
STATTRAK_SPAWN_CHANCE = 0.05
STATTRAK_PARTICLE_MULT = 2.0


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

    dmg = damage_at(int(row["damage_level"]))
    crit_c = crit_chance_at(int(row["crit_level"]))
    luck_b = luck_bonus_at(int(row["luck_level"]))
    silver_lvl = int(row["silver_level"])
    gold_lvl = int(row["gold_level"])
    global_lvl = int(row["global_level"])
    afk_rate = total_afk_rate(silver_lvl, gold_lvl, global_lvl)

    return {
        "particles": int(row["particles"]),
        "total_particles_earned": int(row["total_particles_earned"]),
        "total_breaks": int(row["total_breaks"]),
        "total_clicks": int(row["total_clicks"]),
        "total_crits": int(row["total_crits"]),
        "levels": {
            "damage": int(row["damage_level"]),
            "crit": int(row["crit_level"]),
            "luck": int(row["luck_level"]),
            "offline_cap": int(row["offline_cap_level"]),
            "silver": silver_lvl,
            "gold": gold_lvl,
            "global": global_lvl,
        },
        "effects": {
            "damage": dmg,
            "crit_chance": crit_c,
            "luck_bonus_pct": luck_b,
            "afk_rate_per_sec": round(afk_rate, 2),
            "offline_cap_hours": offline_hours_at(int(row["offline_cap_level"])),
        },
        "afk": {
            "buffer": 0,
            "just_gained": int(afk_gained),
            "just_broken": int(afk_breaks),
            "daily_earned": int(row["daily_afk_earned"] or 0),
            "daily_cap": AFK_DAILY_CAP,
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


async def _spawn_weapon(tg_id: int) -> None:
    """Pick a new weapon from the catalog matching a rolled tier."""
    # Read user's damage level to filter tier eligibility
    async with pool().acquire() as conn:
        dmg_lvl_row = await conn.fetchrow(
            "select damage_level from forge_users where tg_id = $1", tg_id,
        )
    dmg_lvl = int(dmg_lvl_row["damage_level"]) if dmg_lvl_row else 0
    tier = _roll_tier(damage_level=dmg_lvl)
    cfg = TIER_CONFIG[tier]
    skin_pool = await _get_skin_pool(tier)
    if not skin_pool:
        # fallback: try next lower tier
        for fallback in ["awp", "rifle", "pistol"]:
            pool_fb = await _get_skin_pool(fallback)
            if pool_fb:
                skin_pool = pool_fb
                tier = fallback
                cfg = TIER_CONFIG[tier]
                break
    if not skin_pool:
        return  # catalog empty — impossible after seed
    skin_id = random.choice(skin_pool)
    stattrak = random.random() < STATTRAK_SPAWN_CHANCE
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
                "select damage_level, crit_level, luck_level, "
                "current_weapon_hp, current_weapon_max_hp, current_weapon_particles, "
                "current_weapon_stattrak, last_hit_at, total_clicks, total_crits "
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
            luck_lvl = int(row["luck_level"])
            base_dmg = damage_at(dmg_lvl)
            crit_chance = crit_chance_at(crit_lvl)
            is_crit = random.randint(1, 100) <= crit_chance
            damage = base_dmg * 3 if is_crit else base_dmg

            new_hp = int(row["current_weapon_hp"]) - damage
            broken = new_hp <= 0
            particles_earned = 0
            if broken:
                new_hp = 0
                base_particles = int(row["current_weapon_particles"])
                luck_b = luck_bonus_at(luck_lvl)
                particles_earned = int(base_particles * (1 + luck_b / 100))

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
                "luck_level, current_weapon_hp, current_weapon_particles "
                "from forge_users where tg_id = $1 for update",
                tg_id,
            )
            if row is None:
                return (0, 0)
            rate = total_afk_rate(
                int(row["silver_level"]), int(row["gold_level"]), int(row["global_level"])
            )
            if rate <= 0:
                await conn.execute(
                    "update forge_users set last_afk_tick_at = $2 where tg_id = $1",
                    tg_id, now,
                )
                return (0, 0)

            offline_cap = offline_hours_at(int(row["offline_cap_level"])) * 3600
            last_tick = row["last_afk_tick_at"]
            elapsed = 0.0 if last_tick is None else (now - last_tick).total_seconds()
            elapsed = min(elapsed, offline_cap)
            damage_budget = int(rate * elapsed)
            if damage_budget <= 0:
                await conn.execute(
                    "update forge_users set last_afk_tick_at = $2 where tg_id = $1",
                    tg_id, now,
                )
                return (0, 0)

            daily_earned = 0 if row["daily_afk_day"] != today else int(row["daily_afk_earned"] or 0)
            cap_left = max(0, AFK_DAILY_CAP - daily_earned)

            damage_level = int(row["damage_level"])
            luck_mult = 1.0 + luck_bonus_at(int(row["luck_level"])) / 100.0
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
                    # Apply luck bonus just like manual hits do
                    lucky_reward = int(cur_particles * luck_mult)
                    award = min(lucky_reward, cap_left)
                    particles_gained += award
                    cap_left -= award
                    breaks += 1
                    needs_new_spawn = True
                    tier = _roll_tier(damage_level)
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
                      total_breaks = total_breaks + $3,
                      daily_afk_day = $4, daily_afk_earned = $5,
                      last_afk_tick_at = $6,
                      current_skin_id = null, current_weapon_tier = null,
                      current_weapon_hp = null, current_weapon_max_hp = null,
                      current_weapon_particles = null, current_weapon_stattrak = false
                    where tg_id = $1
                    """,
                    tg_id, particles_gained, breaks, today, new_daily, now,
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
                    tg_id, cur_hp, today, new_daily, now,
                )
    if needs_new_spawn:
        await _spawn_weapon(tg_id)
    return (particles_gained, breaks)


async def skip_weapon(tg_id: int) -> dict:
    """Escape hatch: give up the current weapon, get minimal particles (10% of full reward),
    spawn a new one. Useful when stuck with too-high-HP weapon relative to damage level."""
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select current_weapon_particles, current_skin_id "
                "from forge_users where tg_id = $1 for update", tg_id,
            )
            if row is None or row["current_skin_id"] is None:
                return {"ok": False, "error": "No weapon to skip"}
            base_particles = int(row["current_weapon_particles"] or 0)
            refund = max(1, base_particles // 10)
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
    return {"ok": True, "refund": refund}


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
        "offline_cap": "offline_cap_level",
        "silver": "silver_level", "gold": "gold_level", "global": "global_level",
    }[branch]

    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                f"select particles, {column} as lvl from forge_users where tg_id = $1 for update",
                tg_id,
            )
            if row is None:
                return {"ok": False, "error": "No state"}
            lvl = int(row["lvl"])
            balance = int(row["particles"])

            # AFK bots have unlock step
            if branch in ("silver", "gold", "global") and lvl < 0:
                unlock_cost = cfg.get("unlock_cost", 0)
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
            next_level, new_effect, cost = tiers[lvl]  # tier idx = current level
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
    particle_amount = (particle_amount // EXCHANGE_RATE) * EXCHANGE_RATE  # round down to multiple
    coins_given = particle_amount // EXCHANGE_RATE
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select particles from forge_users where tg_id = $1 for update",
                tg_id,
            )
            if row is None or int(row["particles"]) < particle_amount:
                return {"ok": False, "error": "Not enough particles"}
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
                   f.total_crits, f.total_clicks,
                   u.username, u.first_name
            from forge_users f
            left join users u on u.tg_id = f.tg_id
            where f.total_particles_earned > 0
            order by f.total_particles_earned desc
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
