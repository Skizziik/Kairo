"""Flappy Bird — «Взлёт» mini-game.

Players tap to flap a bird through pipes. Distance + collected coins
translate into Pluma (internal currency). Pluma is exchanged 1:1 for casino
coins (taxable through accrue_tax) — but per-run base values + multipliers
stack massively, so endgame players earn billions per session.

Architecture mirrors snake.py: configs + tier lists + JSONB-backed state.
Server is authoritative on Pluma amounts via record_run validation.

Currency flow:
- Per-truck: 50 Pluma base × (1 + level / 10)
- Coin pickups: bronze 100 / star 500 / crystal 2,500 / rainbow 10,000
- Run-wide multipliers (greed, total, map, bird, coin_booster) stack
- Endgame ceiling: ~5M Pluma / 30s run with full kit

Cash-out mechanic: at any point during a run, player can press CASH OUT
button to bank current Pluma × cash_out_mult (1.5x at start, scaling to 5x
at 500 trucks). Cancels the rest of the run safely.
"""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.db.client import pool

log = logging.getLogger(__name__)


# ============================================================
# COIN RARITIES — what falls in the air during a run
# ============================================================

COIN_RARITIES: list[dict] = [
    {"key": "bronze",  "weight": 60, "pluma":   100, "image": "bronze.png",  "color": "#cd7f32"},
    {"key": "star",    "weight": 25, "pluma":   500, "image": "star.png",    "color": "#ffd700"},
    {"key": "crystal", "weight": 12, "pluma":  2500, "image": "crystal.png", "color": "#5aa9ff"},
    {"key": "rainbow", "weight":  3, "pluma": 10000, "image": "rainbow.png", "color": "#ff58e8"},
]


# ============================================================
# BIRDS — 5 selectable, each with passive
# ============================================================

BIRDS: list[dict] = [
    {
        "key": "basic", "name": "Базовая птица", "image": "basic.png",
        "price": 0,
        "passive_short": "Стандарт",
        "passive_long":  "Без особых способностей. Идеальна для старта.",
        "passive": {},
    },
    {
        "key": "drone", "name": "Механический дрон", "image": "drone.png",
        "price": 50_000,
        "passive_short": "−20% гравитация",
        "passive_long":  "Дрон управляется плавнее — гравитация ослабляется на 20%.",
        "passive": {"gravity_mult": 0.80},
    },
    {
        "key": "ice", "name": "Ледяная птица", "image": "ice.png",
        "price": 200_000,
        "passive_short": "+1 щит на ран",
        "passive_long":  "Каждый ран начинается с дополнительным щитом — переживёшь один удар.",
        "passive": {"start_shield_bonus": 1},
    },
    {
        "key": "cosmic", "name": "Космическая птица", "image": "cosmic.png",
        "price": 500_000,
        "passive_short": "Авто-левитация",
        "passive_long":  "Каждые 10 секунд — короткая левитация (1.5 сек), стабилизирует позицию.",
        "passive": {"levitate_period_sec": 10, "levitate_duration_sec": 1.5},
    },
    {
        "key": "fire", "name": "Огненная птица", "image": "fire.png",
        "price": 1_000_000,
        "passive_short": "+25% Pluma",
        "passive_long":  "Огненная аура +25% к Pluma за ВСЁ — трубы, монеты, бонусы.",
        "passive": {"pluma_mult": 1.25},
    },
]
BIRD_BY_KEY = {b["key"]: b for b in BIRDS}


# ============================================================
# MAPS — 9 with unique bonuses (motivation to unlock all)
# ============================================================

MAPS: list[dict] = [
    {
        "key": "classic", "name": "🌳 Классика",     "image": "classic.jpeg",
        "unlock_lvl": 1,  "price": 0,
        "bonus_short": "Стандарт",
        "bonus_long":  "Лесные трубы. Базовые правила.",
        "bonus": {},
    },
    {
        "key": "desert", "name": "🏜 Пустыня",       "image": "desert.png",
        "unlock_lvl": 5,  "price": 50_000,
        "bonus_short": "+25% Bronze coins",
        "bonus_long":  "Пески приносят больше базовых монет — +25% к dropу Bronze Coin.",
        "bonus": {"coin_mult_bronze": 1.25},
    },
    {
        "key": "space", "name": "🌌 Космос",          "image": "space.jpeg",
        "unlock_lvl": 10, "price": 200_000,
        "bonus_short": "+50% Star coins",
        "bonus_long":  "Звёзды повсюду — +50% к dropу Star Coin.",
        "bonus": {"coin_mult_star": 1.50},
    },
    {
        "key": "ice", "name": "❄️ Ледник",            "image": "ice.png",
        "unlock_lvl": 15, "price": 500_000,
        "bonus_short": "+1 power-up на старте",
        "bonus_long":  "Каждый ран начинается с одним случайным power-up'ом.",
        "bonus": {"start_powerup_bonus": 1},
    },
    {
        "key": "jungle", "name": "🌳 Джунгли",        "image": "jungle.png",
        "unlock_lvl": 20, "price": 1_000_000,
        "bonus_short": "+30% Crystal drop",
        "bonus_long":  "Кристаллы, зашитые в листве — +30% к dropу Crystal.",
        "bonus": {"coin_mult_crystal": 1.30},
    },
    {
        "key": "volcano", "name": "🌋 Вулкан",        "image": "volcano.png",
        "unlock_lvl": 30, "price": 3_000_000,
        "bonus_short": "×2 combo множитель",
        "bonus_long":  "Жар вулкана раскачивает combo — множитель за серию УДВАИВАЕТСЯ.",
        "bonus": {"combo_mult_bonus": 2.0},
    },
    {
        "key": "city", "name": "🏙 Ночной город",    "image": "city.jpeg",
        "unlock_lvl": 40, "price": 10_000_000,
        "bonus_short": "+50% магнит",
        "bonus_long":  "Электричество усиливает магнит — радиус сбора монет +50%.",
        "bonus": {"magnet_radius_bonus": 1.50},
    },
    {
        "key": "underwater", "name": "🐠 Подводный", "image": "underwater.jpeg",
        "unlock_lvl": 50, "price": 30_000_000,
        "bonus_short": "−20% скорость труб",
        "bonus_long":  "Вода тормозит препятствия. Легче проходить сложные участки.",
        "bonus": {"pipe_speed_mult": 0.80},
    },
    {
        "key": "cosmos2", "name": "🌌 Космос-2",     "image": "cosmos.png",
        "unlock_lvl": 75, "price": 100_000_000,
        "bonus_short": "+200% Rainbow Gem",
        "bonus_long":  "Эндгейм-карта. Радужные гемы появляются ×3 чаще.",
        "bonus": {"coin_mult_rainbow": 3.0},
    },
]
MAP_BY_KEY = {m["key"]: m for m in MAPS}


# ============================================================
# POWER-UPS — random spawns during runs
# ============================================================

POWER_UPS: list[dict] = [
    {"key": "magnet",       "name": "🧲 Магнит",          "image": "magnet.png",       "duration_sec": 5,  "weight": 25},
    {"key": "shield",       "name": "🛡 Щит",              "image": "shield.png",       "duration_sec": 0,  "weight": 25},  # consumable on hit
    {"key": "rocket",       "name": "🚀 Ракета",          "image": "rocket.png",       "duration_sec": 3,  "weight": 15},
    {"key": "slowmo",       "name": "⏰ Slow-Mo",          "image": "slowmo.png",       "duration_sec": 5,  "weight": 20},
    {"key": "double_coins", "name": "💰 Double Coins",    "image": "double_coins.png", "duration_sec": 10, "weight": 15},
]


# ============================================================
# UPGRADES — 3 branches × 5 perks × max 100 levels
# ============================================================

def _build_tiers(max_level: int, effect_fn, cost_fn) -> list[tuple]:
    out = []
    for lvl in range(1, max_level + 1):
        e = effect_fn(lvl)
        if isinstance(e, float):
            e = round(e, 4)
        out.append((lvl, e, int(round(cost_fn(lvl)))))
    return out


BRANCHES: list[dict] = [
    {"key": "flight", "name": "Полёт",      "icon": "✈️", "color": "#5aa9ff"},
    {"key": "greed",  "name": "Жадность",   "icon": "💰", "color": "#ffd700"},
    {"key": "endure", "name": "Выносливость", "icon": "🛡", "color": "#5cc15c"},
]


UPGRADE_DEFS: dict[str, dict] = {
    # ───── ✈️ FLIGHT ─────
    "flap_power": {
        "branch": "flight", "name": "Сила взмаха", "icon": "⚡",
        "desc": "+%/lvl к импульсу взмаха", "unit": "%",
        "tiers": _build_tiers(100, lambda L: L * 0.5, lambda L: 500 * (1.18 ** (L - 1))),
    },
    "lighter_lungs": {
        "branch": "flight", "name": "Лёгкие как пух", "icon": "🪶",
        "desc": "−%/lvl к гравитации", "unit": "%",
        "tiers": _build_tiers(100, lambda L: L * 0.3, lambda L: 700 * (1.18 ** (L - 1))),
    },
    "fast_recovery": {
        "branch": "flight", "name": "Быстрая регенерация", "icon": "💨",
        "desc": "Щит регенится через сек", "unit": "сек",
        "tiers": _build_tiers(50, lambda L: max(5, 30 - L * 0.5), lambda L: 2000 * (1.20 ** (L - 1))),
    },
    "wing_master": {
        "branch": "flight", "name": "Мастер крыла", "icon": "🦅",
        "desc": "+%/lvl к combo за near-miss", "unit": "%",
        "tiers": _build_tiers(100, lambda L: L * 0.5, lambda L: 1500 * (1.18 ** (L - 1))),
    },
    "pure_flight": {
        "branch": "flight", "name": "Чистый полёт", "icon": "🌬",
        "desc": "+%/lvl к скорости труб (бонус Pluma)", "unit": "%",
        "tiers": _build_tiers(100, lambda L: L * 0.4, lambda L: 3000 * (1.19 ** (L - 1))),
    },

    # ───── 💰 GREED ─────
    "coin_booster": {
        "branch": "greed", "name": "Coin Booster", "icon": "💰",
        "desc": "Все Pluma ×(1+%/lvl)", "unit": "%",
        "tiers": _build_tiers(100, lambda L: L * 2.0, lambda L: 1000 * (1.20 ** (L - 1))),
    },
    "magnet_range": {
        "branch": "greed", "name": "Магнит", "icon": "🧲",
        "desc": "Радиус магнита +%/lvl", "unit": "%",
        "tiers": _build_tiers(100, lambda L: L * 2.0, lambda L: 800 * (1.18 ** (L - 1))),
    },
    "lucky_strike": {
        "branch": "greed", "name": "Lucky Strike", "icon": "🍀",
        "desc": "%/lvl шанс на ×2 Pluma за пикап", "unit": "%",
        "tiers": _build_tiers(100, lambda L: L * 0.5, lambda L: 1500 * (1.18 ** (L - 1))),
    },
    "crit_pickup": {
        "branch": "greed", "name": "Crit Pickup", "icon": "💥",
        "desc": "%/lvl шанс на ×10 Pluma за пикап", "unit": "%",
        "tiers": _build_tiers(100, lambda L: L * 0.15, lambda L: 5000 * (1.19 ** (L - 1))),
    },
    "rainbow_hunter": {
        "branch": "greed", "name": "Охотник за радугой", "icon": "🌈",
        "desc": "+%/lvl к Rainbow Gem drop chance", "unit": "%",
        "tiers": _build_tiers(100, lambda L: L * 0.5, lambda L: 8000 * (1.19 ** (L - 1))),
    },

    # ───── 🛡 ENDURE ─────
    "start_shield": {
        "branch": "endure", "name": "Стартовый щит", "icon": "🛡",
        "desc": "Щитов на старте", "unit": "шт",
        "tiers": _build_tiers(10, lambda L: L, lambda L: 5000 * (1.7 ** (L - 1))),
    },
    "start_rocket": {
        "branch": "endure", "name": "Стартовая ракета", "icon": "🚀",
        "desc": "Ракет на старте", "unit": "шт",
        "tiers": _build_tiers(5, lambda L: L, lambda L: 50_000 * (2.0 ** (L - 1))),
    },
    "powerup_master": {
        "branch": "endure", "name": "Мастер power-ups", "icon": "✨",
        "desc": "Длительность power-ups +%/lvl", "unit": "%",
        "tiers": _build_tiers(100, lambda L: L * 1.0, lambda L: 1500 * (1.19 ** (L - 1))),
    },
    "daily_first_run": {
        "branch": "endure", "name": "Первый ран дня", "icon": "🌅",
        "desc": "Первый ран дня даёт +%/lvl Pluma", "unit": "%",
        "tiers": _build_tiers(50, lambda L: L * 5, lambda L: 5000 * (1.20 ** (L - 1))),
    },
    "xp_boost": {
        "branch": "endure", "name": "XP Boost", "icon": "📈",
        "desc": "+%/lvl к XP за ран", "unit": "%",
        "tiers": _build_tiers(100, lambda L: L * 1.0, lambda L: 1000 * (1.18 ** (L - 1))),
    },
}


# ============================================================
# ARTIFACTS — 12 always-on, looted from cases
# ============================================================

ARTIFACTS: list[dict] = [
    {
        "key": "basic_wing",
        "name": "Basic Wing Upgrade",
        "image": "basic_wing.png",
        "tier": "common",
        "buff_short": "+10% сила взмаха",
        "buff_long":  "Постоянный +10% к импульсу взмаха. Стакается с апгрейдами.",
        "effect": {"flap_power_bonus": 0.10},
    },
    {
        "key": "starter_shield",
        "name": "Starter Shield",
        "image": "starter_shield.png",
        "tier": "common",
        "buff_short": "+1 щит на ран",
        "buff_long":  "Каждый ран начинается с дополнительным щитом.",
        "effect": {"start_shield_bonus": 1},
    },
    {
        "key": "feather_token",
        "name": "Feather Token",
        "image": "feather_token.png",
        "tier": "common",
        "buff_short": "+20% Pluma за трубы",
        "buff_long":  "Каждая пройденная труба приносит +20% Pluma.",
        "effect": {"pluma_per_truck_mult": 1.20},
    },
    {
        "key": "coin_magnet",
        "name": "Coin Magnet Core",
        "image": "coin_magnet.png",
        "tier": "gold",
        "buff_short": "Постоянный магнит",
        "buff_long":  "Без power-up'а всегда работает магнит малого радиуса.",
        "effect": {"passive_magnet_radius": 80},
    },
    {
        "key": "lucky_charm",
        "name": "Lucky Charm",
        "image": "lucky_charm.png",
        "tier": "gold",
        "buff_short": "+30% к Lucky Strike",
        "buff_long":  "Шансы Lucky Strike (×2 Pluma) увеличиваются на 30%.",
        "effect": {"lucky_chance_bonus": 0.30},
    },
    {
        "key": "wing_booster",
        "name": "Wing Booster",
        "image": "wing_booster.png",
        "tier": "gold",
        "buff_short": "+30% сила взмаха",
        "buff_long":  "Сильный буст к импульсу взмаха.",
        "effect": {"flap_power_bonus": 0.30},
    },
    {
        "key": "galaxy_dust",
        "name": "Galaxy Dust",
        "image": "galaxy_dust.png",
        "tier": "cosmic",
        "buff_short": "Combo ×1.5",
        "buff_long":  "Множитель за combo-серию увеличивается ×1.5.",
        "effect": {"combo_mult_extra": 1.5},
    },
    {
        "key": "black_hole",
        "name": "Mini Black Hole",
        "image": "black_hole.png",
        "tier": "cosmic",
        "buff_short": "Авто-сбор Rainbow",
        "buff_long":  "Радужные гемы автоматически притягиваются к птице с любой точки экрана.",
        "effect": {"auto_grab_rainbow": True},
    },
    {
        "key": "orbit_ring",
        "name": "Orbit Ring",
        "image": "orbit_ring.png",
        "tier": "cosmic",
        "buff_short": "Power-up каждые 50 truck",
        "buff_long":  "Каждые 50 пройденных труб — гарантированный случайный power-up.",
        "effect": {"powerup_every_n_trucks": 50},
    },
    {
        "key": "phoenix",
        "name": "Phoenix Feather",
        "image": "phoenix.png",
        "tier": "legendary",
        "buff_short": "1 воскресение/run",
        "buff_long":  "После удара — 1 раз за ран птица возрождается с полным импульсом.",
        "effect": {"resurrect_per_run": 1},
    },
    {
        "key": "crown",
        "name": "Crown Token",
        "image": "crown.png",
        "tier": "legendary",
        "buff_short": "×1.05^level Pluma",
        "buff_long":  "Финальный множитель Pluma = 1.05^уровень. На 50 уровне — ×11.5 ко всему.",
        "effect": {"crown_level_mult": True},
    },
    {
        "key": "supernova",
        "name": "Supernova Core",
        "image": "supernova.png",
        "tier": "legendary",
        "buff_short": "Каждый 50-й truck ×100",
        "buff_long":  "Каждая 50-я пройденная труба даёт ×100 Pluma jackpot.",
        "effect": {"jackpot_every_50_trucks": 100},
    },
]
ARTIFACT_BY_KEY = {a["key"]: a for a in ARTIFACTS}


# ============================================================
# CASES — 4 tiers, drop tables
# ============================================================

CASES: list[dict] = [
    {
        "key": "common", "name": "Обычный кейс", "image": "common.png",
        "price": 10_000,
        "drops": {"common": 100},  # only common artifacts
    },
    {
        "key": "gold", "name": "Золотой кейс", "image": "gold.png",
        "price": 100_000,
        "drops": {"common": 25, "gold": 75},
    },
    {
        "key": "cosmic", "name": "Космический кейс", "image": "cosmic.png",
        "price": 500_000,
        "drops": {"gold": 30, "cosmic": 70},
    },
    {
        "key": "legendary", "name": "Легендарный кейс", "image": "legendary.png",
        "price": 2_000_000,
        "drops": {"cosmic": 40, "legendary": 60},
    },
]
CASE_BY_KEY = {c["key"]: c for c in CASES}


# ============================================================
# CASH-OUT MULTIPLIERS — by trucks passed
# ============================================================

def cash_out_multiplier(trucks_passed: int) -> float:
    """1.5x at 0 trucks, scaling up with progression. Encourages continued
    runs but rewards safe locks-in at milestones."""
    if trucks_passed < 10:
        return 1.50
    if trucks_passed < 50:
        return 2.00
    if trucks_passed < 100:
        return 2.50
    if trucks_passed < 200:
        return 3.00
    if trucks_passed < 500:
        return 4.00
    return 5.00


# ============================================================
# LEVEL / XP
# ============================================================

def xp_needed_for(level: int) -> int:
    if level < 1: return 0
    return int(80 * (level ** 1.6))


def level_for_xp(xp: int) -> int:
    if xp < 0: return 1
    lvl = 1
    while xp >= xp_needed_for(lvl):
        lvl += 1
        if lvl > 200: break
    return lvl


# ============================================================
# DB / SCHEMA
# ============================================================

async def ensure_schema() -> None:
    sql_path = Path(__file__).parent.parent / "db" / "migration_flappy.sql"
    if not sql_path.exists():
        log.warning("flappy migration SQL missing")
        return
    sql = sql_path.read_text(encoding="utf-8")
    async with pool().acquire() as conn:
        await conn.execute(sql)
    log.info("flappy schema ensured")


async def ensure_user(tg_id: int) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "insert into flappy_users (tg_id) values ($1) on conflict do nothing",
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
# READ STATE / CONFIG
# ============================================================

async def get_state(tg_id: int) -> dict:
    await ensure_user(tg_id)
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "select * from flappy_users where tg_id = $1", tg_id,
        )
        bal_row = await conn.fetchrow(
            "select balance from economy_users where tg_id = $1", tg_id,
        )
    if row is None:
        return {}

    upgrades   = _parse_jsonb(row["upgrades"]) or {}
    owned_birds = _parse_jsonb(row["owned_birds"]) or ["basic"]
    owned_maps  = _parse_jsonb(row["owned_maps"]) or ["classic"]
    artifacts   = _parse_jsonb(row["artifacts"]) or []

    cur_xp = int(row["xp"] or 0)
    cur_lvl = level_for_xp(cur_xp)
    cur_level_xp = xp_needed_for(cur_lvl - 1) if cur_lvl > 1 else 0
    next_level_xp = xp_needed_for(cur_lvl)

    return {
        "tg_id":             int(row["tg_id"]),
        "level":             cur_lvl,
        "xp":                cur_xp,
        "current_level_xp":  cur_level_xp,
        "next_level_xp":     next_level_xp,
        "pluma_balance":     int(row["pluma_balance"]),
        "pluma_lifetime":    int(row["pluma_lifetime"]),
        "balance":           int(bal_row["balance"]) if bal_row else 0,
        "runs_count":        int(row["runs_count"]),
        "distance_lifetime": int(row["distance_lifetime"]),
        "best_run_distance": int(row["best_run_distance"]),
        "best_run_pluma":    int(row["best_run_pluma"]),
        "best_combo":        int(row["best_combo"]),
        "current_bird_id":   row["current_bird_id"],
        "owned_birds":       owned_birds,
        "current_map_id":    row["current_map_id"],
        "owned_maps":        owned_maps,
        "upgrades":          upgrades,
        "artifacts":         artifacts,
        "cases_opened":      int(row["cases_opened"]),
    }


async def get_config() -> dict:
    return {
        "coin_rarities": COIN_RARITIES,
        "birds":         BIRDS,
        "maps":          MAPS,
        "power_ups":     POWER_UPS,
        "branches":      BRANCHES,
        "upgrades": [
            {
                "key": k,
                "branch": v["branch"], "name": v["name"], "icon": v["icon"],
                "desc": v["desc"], "unit": v["unit"],
                "tiers": v["tiers"],
                "max_level": len(v["tiers"]),
            } for k, v in UPGRADE_DEFS.items()
        ],
        "artifacts": ARTIFACTS,
        "cases":     CASES,
    }


# ============================================================
# RUN — record finished/cashed-out run
# ============================================================

# Anti-cheat: hard ceiling on raw Pluma per second of run.
# Empirical max with all stacked multipliers is ~5M Pluma/sec on Cosmos-2;
# we use 100M/sec as a generous ceiling that catches blatant inflation.
MAX_PLUMA_PER_SECOND = 100_000_000


async def record_run(
    tg_id: int,
    distance: int,
    pluma_earned: int,           # raw client-reported pluma (with all per-pickup mults applied)
    coin_pickups: dict[str, int],# {"bronze": 5, "star": 2, ...} for stats
    duration_sec: int,
    map_id: str,
    bird_id: str,
    best_combo: int = 0,
    cashed_out: bool = False,
    cashout_mult: float = 1.0,
    died_to: str = "pipe",
) -> dict:
    """Validate and credit a run. Returns credit summary."""
    await ensure_user(tg_id)

    if duration_sec < 0 or duration_sec > 1800:
        return {"ok": False, "error": "Invalid duration"}
    if map_id not in MAP_BY_KEY:
        return {"ok": False, "error": "Unknown map"}
    if bird_id not in BIRD_BY_KEY:
        return {"ok": False, "error": "Unknown bird"}

    distance = max(0, int(distance or 0))
    pluma_earned = max(0, int(pluma_earned or 0))
    duration_sec = max(1, int(duration_sec or 1))
    best_combo = max(0, int(best_combo or 0))
    cashout_mult = max(1.0, float(cashout_mult or 1.0))

    # Anti-cheat raw cap
    max_allowed = int(MAX_PLUMA_PER_SECOND * duration_sec)
    if pluma_earned > max_allowed:
        log.warning("flappy: tg=%s pluma %d > cap %d, clipping",
                    tg_id, pluma_earned, max_allowed)
        pluma_earned = max_allowed

    # Pull full state — we need xp, best_*, etc. for the UPDATE below.
    # Earlier this only fetched level/upgrades/artifacts/last_run_at and the
    # later code KeyError'd on row["best_run_pluma"], crashing the endpoint.
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "select * from flappy_users where tg_id = $1", tg_id,
        )
    if row is None:
        return {"ok": False, "error": "Нет состояния"}
    upgrades = _parse_jsonb(row["upgrades"]) or {}
    artifacts = _parse_jsonb(row["artifacts"]) or []
    art_eff = aggregate_artifact_effects(artifacts)

    # Run-wide multipliers (server-controlled — trusted)
    pluma = pluma_earned

    # coin_booster upgrade
    cb_lvl = int(upgrades.get("coin_booster", 0))
    pluma = int(pluma * (1.0 + cb_lvl * 0.02))

    # Bird passive (pluma_mult)
    bird = BIRD_BY_KEY.get(bird_id, BIRDS[0])
    bird_mult = float(bird.get("passive", {}).get("pluma_mult", 1.0))
    pluma = int(pluma * bird_mult)

    # Daily first-run bonus
    today = datetime.now(timezone.utc).date()
    last_run_at = row["last_run_at"]
    is_first_today = (last_run_at is None) or (last_run_at.date() < today)
    if is_first_today:
        first_lvl = int(upgrades.get("daily_first_run", 0))
        if first_lvl > 0:
            pluma = int(pluma * (1.0 + first_lvl * 0.05))

    # Crown artifact — ×1.05^level total
    if art_eff["crown_level_mult"]:
        cur_lvl_now = int(row["level"] or 1)
        pluma = int(pluma * (1.05 ** cur_lvl_now))

    # Cash-out multiplier (player chose to lock in safely)
    if cashed_out:
        pluma = int(pluma * cashout_mult)

    # XP — based on distance + bonuses
    xp = distance * 2
    xp_lvl = int(upgrades.get("xp_boost", 0))
    if xp_lvl > 0:
        xp = int(xp * (1.0 + xp_lvl * 0.01))

    # Persist — accumulate XP from existing total, max for bests.
    cur_xp = int(row["xp"] or 0) + xp
    cur_best_dist      = max(int(row["best_run_distance"] or 0), distance)
    cur_best_pluma_run = max(int(row["best_run_pluma"] or 0), pluma)
    cur_best_combo     = max(int(row["best_combo"] or 0), best_combo)
    async with pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                update flappy_users set
                  xp = $2, level = $3,
                  pluma_balance = pluma_balance + $4,
                  pluma_lifetime = pluma_lifetime + $4,
                  runs_count = runs_count + 1,
                  distance_lifetime = distance_lifetime + $5,
                  best_run_distance = $6,
                  best_run_pluma = $7,
                  best_combo = $8,
                  last_run_at = $9
                where tg_id = $1
                """,
                tg_id, cur_xp, level_for_xp(cur_xp), pluma, distance,
                cur_best_dist, cur_best_pluma_run, cur_best_combo,
                datetime.now(timezone.utc),
            )
            await conn.execute(
                """
                insert into flappy_runs
                  (user_id, distance, pluma, bird, map_id, best_combo,
                   cashed_out, cashout_mult, duration_sec, died_to)
                values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                tg_id, distance, pluma, bird_id, map_id, best_combo,
                cashed_out, cashout_mult, duration_sec, died_to,
            )
            # Trim history to last 50
            await conn.execute(
                """
                delete from flappy_runs where user_id = $1 and id not in (
                    select id from flappy_runs where user_id = $1
                    order by created_at desc limit 50
                )
                """, tg_id,
            )

    return {
        "ok": True,
        "pluma_credited": pluma,
        "xp_gained": xp,
        "new_level": level_for_xp(cur_xp),
        "is_first_today": is_first_today,
    }


# ============================================================
# AGGREGATE ARTIFACT EFFECTS
# ============================================================

def aggregate_artifact_effects(owned: list[str]) -> dict:
    """Sum effects of all owned artifacts for use in record_run + UI."""
    eff = {
        "flap_power_bonus":      0.0,
        "start_shield_bonus":    0,
        "pluma_per_truck_mult":  1.0,
        "passive_magnet_radius": 0,
        "lucky_chance_bonus":    0.0,
        "combo_mult_extra":      1.0,
        "auto_grab_rainbow":     False,
        "powerup_every_n_trucks": 0,
        "resurrect_per_run":     0,
        "crown_level_mult":      False,
        "jackpot_every_50_trucks": 0,
    }
    for key in owned or []:
        a = ARTIFACT_BY_KEY.get(key)
        if not a:
            continue
        e = a.get("effect", {}) or {}
        for k, v in e.items():
            if k in ("flap_power_bonus", "lucky_chance_bonus"):
                eff[k] += float(v)
            elif k in ("start_shield_bonus", "passive_magnet_radius",
                       "powerup_every_n_trucks", "resurrect_per_run",
                       "jackpot_every_50_trucks"):
                eff[k] = max(eff[k], int(v))
            elif k in ("pluma_per_truck_mult", "combo_mult_extra"):
                eff[k] *= float(v)
            elif k in ("auto_grab_rainbow", "crown_level_mult"):
                eff[k] = bool(v) or eff[k]
    return eff


# ============================================================
# EXCHANGE Pluma → casino coins (taxable)
# ============================================================

async def exchange_pluma(tg_id: int, amount: int) -> dict:
    """Convert Pluma into casino coins at 1:1. Pluma is debited, balance is
    credited. Tax accrues on the credited amount via accrue_tax (so heavy
    converters pay taxes daily)."""
    amount = int(amount or 0)
    if amount <= 0:
        return {"ok": False, "error": "Сумма должна быть > 0"}

    await ensure_user(tg_id)
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select pluma_balance from flappy_users where tg_id = $1 for update",
                tg_id,
            )
            if row is None:
                return {"ok": False, "error": "Нет состояния"}
            have = int(row["pluma_balance"])
            if have < amount:
                return {"ok": False, "error": "Не хватает Pluma", "have": have, "need": amount}

            await conn.execute(
                "update flappy_users set pluma_balance = pluma_balance - $2 "
                "where tg_id = $1", tg_id, amount,
            )
            new_bal_row = await conn.fetchrow(
                "update economy_users set balance = balance + $2, "
                "total_earned = total_earned + $2 "
                "where tg_id = $1 returning balance",
                tg_id, amount,
            )
            new_bal = int(new_bal_row["balance"]) if new_bal_row else 0
            try:
                await conn.execute(
                    "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                    "values ($1, $2, 'flappy_exchange', $3, $4)",
                    tg_id, amount, f"pluma_exchange_{amount}", new_bal,
                )
            except Exception:
                pass

    # Tax accrual on the converted amount (daily SET will collect)
    try:
        from app.economy import tax as _tax
        await _tax.accrue_tax(tg_id, amount, "flappy_exchange")
    except Exception:
        pass

    return {"ok": True, "exchanged": amount, "new_balance": new_bal,
            "new_pluma_balance": have - amount}


# ============================================================
# BIRDS / MAPS / UPGRADES — purchases
# ============================================================

async def buy_bird(tg_id: int, key: str) -> dict:
    bird = BIRD_BY_KEY.get(key)
    if not bird:
        return {"ok": False, "error": "Unknown bird"}
    await ensure_user(tg_id)
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select pluma_balance, owned_birds from flappy_users where tg_id = $1 for update",
                tg_id,
            )
            if row is None:
                return {"ok": False, "error": "Нет состояния"}
            owned = _parse_jsonb(row["owned_birds"]) or []
            if key in owned:
                return {"ok": False, "error": "Уже куплено"}
            if int(row["pluma_balance"]) < int(bird["price"]):
                return {"ok": False, "error": "Не хватает Pluma", "need": int(bird["price"])}
            await conn.execute(
                "update flappy_users set pluma_balance = pluma_balance - $2, "
                "owned_birds = $3::jsonb where tg_id = $1",
                tg_id, int(bird["price"]), json.dumps(owned + [key]),
            )
    return {"ok": True, "key": key}


async def equip_bird(tg_id: int, key: str) -> dict:
    if key not in BIRD_BY_KEY:
        return {"ok": False, "error": "Unknown bird"}
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "select owned_birds from flappy_users where tg_id = $1", tg_id,
        )
        if row is None:
            return {"ok": False, "error": "Нет состояния"}
        owned = _parse_jsonb(row["owned_birds"]) or ["basic"]
        if key not in owned:
            return {"ok": False, "error": "Птица не куплена"}
        await conn.execute(
            "update flappy_users set current_bird_id = $2 where tg_id = $1",
            tg_id, key,
        )
    return {"ok": True, "current_bird_id": key}


async def unlock_map(tg_id: int, key: str) -> dict:
    m = MAP_BY_KEY.get(key)
    if not m:
        return {"ok": False, "error": "Unknown map"}
    await ensure_user(tg_id)
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select level, pluma_balance, owned_maps from flappy_users "
                "where tg_id = $1 for update", tg_id,
            )
            if row is None:
                return {"ok": False, "error": "Нет состояния"}
            owned = _parse_jsonb(row["owned_maps"]) or ["classic"]
            if key in owned:
                return {"ok": False, "error": "Уже открыта"}
            if int(row["level"]) < int(m["unlock_lvl"]):
                return {"ok": False, "error": f"Нужен уровень {m['unlock_lvl']}"}
            if int(row["pluma_balance"]) < int(m["price"]):
                return {"ok": False, "error": "Не хватает Pluma"}
            await conn.execute(
                "update flappy_users set pluma_balance = pluma_balance - $2, "
                "owned_maps = $3::jsonb where tg_id = $1",
                tg_id, int(m["price"]), json.dumps(owned + [key]),
            )
    return {"ok": True, "key": key}


async def select_map(tg_id: int, key: str) -> dict:
    if key not in MAP_BY_KEY:
        return {"ok": False, "error": "Unknown map"}
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "select owned_maps from flappy_users where tg_id = $1", tg_id,
        )
        if row is None:
            return {"ok": False, "error": "Нет состояния"}
        owned = _parse_jsonb(row["owned_maps"]) or ["classic"]
        if key not in owned:
            return {"ok": False, "error": "Карта не открыта"}
        await conn.execute(
            "update flappy_users set current_map_id = $2 where tg_id = $1",
            tg_id, key,
        )
    return {"ok": True, "current_map_id": key}


async def buy_upgrade(tg_id: int, key: str) -> dict:
    if key not in UPGRADE_DEFS:
        return {"ok": False, "error": "Unknown upgrade"}
    cfg = UPGRADE_DEFS[key]
    tiers = cfg["tiers"]
    max_lvl = len(tiers)
    await ensure_user(tg_id)
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select pluma_balance, upgrades from flappy_users where tg_id = $1 for update",
                tg_id,
            )
            if row is None:
                return {"ok": False, "error": "Нет состояния"}
            ups = _parse_jsonb(row["upgrades"]) or {}
            cur = int(ups.get(key, 0))
            if cur >= max_lvl:
                return {"ok": False, "error": "Max level"}
            _, _, cost = tiers[cur]
            if int(row["pluma_balance"]) < cost:
                return {"ok": False, "error": "Не хватает Pluma", "cost": cost}
            ups[key] = cur + 1
            await conn.execute(
                "update flappy_users set pluma_balance = pluma_balance - $2, "
                "upgrades = $3::jsonb where tg_id = $1",
                tg_id, cost, json.dumps(ups),
            )
    return {"ok": True, "key": key, "new_level": cur + 1, "cost": cost}


# ============================================================
# CASES — open with Pluma, drop artifact
# ============================================================

async def buy_case(tg_id: int, key: str) -> dict:
    case = CASE_BY_KEY.get(key)
    if not case:
        return {"ok": False, "error": "Unknown case"}
    # Pick tier from drops
    tiers = list(case["drops"].keys())
    weights = list(case["drops"].values())
    chosen_tier = random.choices(tiers, weights=weights, k=1)[0]
    pool_artifacts = [a for a in ARTIFACTS if a["tier"] == chosen_tier]
    if not pool_artifacts:
        return {"ok": False, "error": "Drop pool empty"}
    drop = random.choice(pool_artifacts)

    await ensure_user(tg_id)
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select pluma_balance, artifacts from flappy_users where tg_id = $1 for update",
                tg_id,
            )
            if row is None:
                return {"ok": False, "error": "Нет состояния"}
            if int(row["pluma_balance"]) < int(case["price"]):
                return {"ok": False, "error": "Не хватает Pluma", "need": int(case["price"])}
            owned = _parse_jsonb(row["artifacts"]) or []
            already_have = drop["key"] in owned
            new_artifacts = list(owned)
            if not already_have:
                new_artifacts.append(drop["key"])
            await conn.execute(
                "update flappy_users set pluma_balance = pluma_balance - $2, "
                "artifacts = $3::jsonb, cases_opened = cases_opened + 1 "
                "where tg_id = $1",
                tg_id, int(case["price"]), json.dumps(new_artifacts),
            )
    return {
        "ok": True,
        "case": key,
        "drop": {
            "key":   drop["key"],
            "name":  drop["name"],
            "image": drop["image"],
            "tier":  drop["tier"],
            "buff_short": drop["buff_short"],
            "buff_long":  drop["buff_long"],
            "duplicate":  already_have,
        },
        "spent": int(case["price"]),
    }


# ============================================================
# LEADERBOARD
# ============================================================

async def leaderboard(sort_by: str = "lifetime", limit: int = 20) -> list[dict]:
    """Two boards in one shape:
    - sort_by='lifetime' → ranked by pluma_lifetime (total farmed)
    - sort_by='best_run' → ranked by best_run_pluma (single best run)
    Each row includes both metrics so the client can show either column."""
    if sort_by not in ("lifetime", "best_run"):
        sort_by = "lifetime"
    order_col = "f.pluma_lifetime" if sort_by == "lifetime" else "f.best_run_pluma"

    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            select f.tg_id, f.pluma_lifetime, f.runs_count, f.level,
                   f.best_run_distance, f.best_run_pluma, f.best_combo,
                   u.username, u.first_name, u.photo_url
            from flappy_users f
            left join users u on u.tg_id = f.tg_id
            where f.pluma_lifetime > 0 or f.runs_count > 0
            order by {order_col} desc nulls last
            limit $1
            """, limit,
        )
    return [
        {
            "tg_id":             int(r["tg_id"]),
            "username":          r["username"],
            "first_name":        r["first_name"],
            "photo_url":         r["photo_url"],
            "level":             int(r["level"] or 1),
            "pluma_lifetime":    int(r["pluma_lifetime"] or 0),
            "best_run_pluma":    int(r["best_run_pluma"] or 0),
            "best_run_distance": int(r["best_run_distance"] or 0),
            "best_combo":        int(r["best_combo"] or 0),
            "runs_count":        int(r["runs_count"] or 0),
        }
        for r in rows
    ]
