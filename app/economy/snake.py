"""Snake mini-game — full meta-progression sandbox.

Player runs a classic snake: eat skins, grow, die. Every skin eaten credits
real casino coins. On top of the arcade core sits a deep upgrade tree
(42 upgrades across 6 branches), an AFK farm of 7 snake species, 10
cosmetic skin themes, 8 maps, and 4 game modes.

Design constraints:
- Server is authoritative on coin amounts. Client reports run results
  with per-rarity skin counts; server validates rate limits and applies
  multipliers from upgrades before crediting.
- AFK farm ticks lazily on every state fetch (no scheduler needed) plus
  a periodic background tick for offline accumulation.
- Idempotent migrations; all design data lives in this module so tuning
  is fast.
"""
from __future__ import annotations

import json
import logging
import math
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.db.client import pool

log = logging.getLogger(__name__)


# ============================================================
# CORE CONFIG
# ============================================================

# Skin rarities (drives spawn weights + coin reward + XP)
RARITIES: list[dict] = [
    {"key": "consumer",         "weight": 40, "coin_min":    25, "coin_max":     55, "xp": 1,  "color": "#b0c3d9", "emoji": "⚪"},
    {"key": "industrial",       "weight": 25, "coin_min":    60, "coin_max":    150, "xp": 2,  "color": "#5e98d9", "emoji": "🔵"},
    {"key": "milspec",          "weight": 15, "coin_min":   200, "coin_max":    500, "xp": 4,  "color": "#4b69ff", "emoji": "💙"},
    {"key": "restricted",       "weight": 10, "coin_min":  1000, "coin_max":   2500, "xp": 8,  "color": "#8847ff", "emoji": "💜"},
    {"key": "classified",       "weight":  6, "coin_min":  5000, "coin_max":  15000, "xp": 16, "color": "#d32ce6", "emoji": "💗"},
    {"key": "covert",           "weight":  3, "coin_min": 20000, "coin_max":  20000, "xp": 32, "color": "#eb4b4b", "emoji": "❤️"},
    {"key": "exceedingly_rare", "weight":  1, "coin_min": 30000, "coin_max": 100000, "xp": 96, "color": "#e4ae39", "emoji": "🟡"},
]
RARITY_BY_KEY = {r["key"]: r for r in RARITIES}

# Anti-cheat: hard ceiling on coin rate (per second of run).
# Empirical max with all upgrades + best luck on smallest map ~ 35K/sec → cap at 50K.
MAX_COINS_PER_SECOND = 50_000


# ============================================================
# UPGRADE TREE — 42 upgrades, 6 branches
# ============================================================

# Each upgrade: tiers list of (level_after_buy, effect_value, cost_coins).
# `effect_value` interpretation depends on upgrade — see UPGRADE_DEFS below.

def _build_tiers(max_level: int, effect_fn, cost_fn, round_effect: bool = True) -> list[tuple]:
    out = []
    for lvl in range(1, max_level + 1):
        e = effect_fn(lvl)
        if round_effect:
            e = round(e, 3)
        out.append((lvl, e, int(round(cost_fn(lvl)))))
    return out


UPGRADE_DEFS: dict[str, dict] = {
    # ═════════ 🐍 BODY (выживаемость) ═════════
    "reflex_boost": {
        "branch": "body", "name": "Reflex Boost", "icon": "⚡",
        "desc": "Лёгкая пауза перед стеной (мс)", "unit": "ms",
        "tiers": _build_tiers(10, lambda L: 50 + L * 30, lambda L: 200 * (1.45 ** (L - 1))),
    },
    "phantom_tail": {
        "branch": "body", "name": "Phantom Tail", "icon": "👻",
        "desc": "Шанс пройти сквозь себя (%)", "unit": "%",
        "tiers": _build_tiers(15, lambda L: L * 1.7, lambda L: 800 * (1.5 ** (L - 1))),
    },
    "iron_shield": {
        "branch": "body", "name": "Iron Shield", "icon": "🛡",
        "desc": "Щитов на ран", "unit": "шт",
        "tiers": _build_tiers(10, lambda L: L, lambda L: 5000 * (1.7 ** (L - 1))),
    },
    "wall_bounce": {
        "branch": "body", "name": "Wall Bounce", "icon": "🪞",
        "desc": "Отскоков от стен", "unit": "шт",
        "tiers": _build_tiers(5, lambda L: L, lambda L: 50000 * (2.2 ** (L - 1))),
    },
    "extra_life": {
        "branch": "body", "name": "Extra Life", "icon": "💚",
        "desc": "Жизней на ран", "unit": "шт",
        "tiers": _build_tiers(5, lambda L: L, lambda L: 100000 * (2.0 ** (L - 1))),
    },
    "tough_skin": {
        "branch": "body", "name": "Tough Skin", "icon": "🦎",
        "desc": "Восстановить % длины при первой смерти", "unit": "%",
        "tiers": _build_tiers(3, lambda L: 25 + L * 25, lambda L: 75000 * (2.5 ** (L - 1))),
    },
    "recovery": {
        "branch": "body", "name": "Recovery", "icon": "💖",
        "desc": "% от заработка возвращается после смерти", "unit": "%",
        "tiers": _build_tiers(10, lambda L: L * 5, lambda L: 25000 * (1.7 ** (L - 1))),
    },

    # ═════════ 💰 GREED (деньги) ═════════
    "greed_boost": {
        "branch": "greed", "name": "Greed Boost", "icon": "💰",
        "desc": "+% монет за каждый скин", "unit": "%",
        "tiers": _build_tiers(50, lambda L: L * 2, lambda L: 600 * (1.30 ** (L - 1))),
    },
    "combo_chain": {
        "branch": "greed", "name": "Combo Chain", "icon": "⛓",
        "desc": "Множитель за серию из 5 без смерти (×)", "unit": "×",
        "tiers": _build_tiers(15, lambda L: 1.5 + L * 0.17, lambda L: 3000 * (1.50 ** (L - 1))),
    },
    "lucky_strike": {
        "branch": "greed", "name": "Lucky Strike", "icon": "🍀",
        "desc": "Шанс ×2 монеты за укус (%)", "unit": "%",
        "tiers": _build_tiers(20, lambda L: L * 2, lambda L: 1500 * (1.40 ** (L - 1))),
    },
    "critical_bite": {
        "branch": "greed", "name": "Critical Bite", "icon": "💥",
        "desc": "Шанс ×10 монет за укус (%)", "unit": "%",
        "tiers": _build_tiers(10, lambda L: L * 0.5, lambda L: 8000 * (1.65 ** (L - 1))),
    },
    "streak_multiplier": {
        "branch": "greed", "name": "Streak Multiplier", "icon": "🔥",
        "desc": "Бонус за каждый Nй скин подряд (×)", "unit": "×",
        "tiers": _build_tiers(15, lambda L: 1 + L * 0.25, lambda L: 4000 * (1.45 ** (L - 1))),
    },
    "mythic_magnet": {
        "branch": "greed", "name": "Mythic Magnet", "icon": "🧲",
        "desc": "+% шанс covert/exc_rare", "unit": "%",
        "tiers": _build_tiers(15, lambda L: L * 0.5, lambda L: 12000 * (1.55 ** (L - 1))),
    },
    "treasure_pulse": {
        "branch": "greed", "name": "Treasure Pulse", "icon": "🎁",
        "desc": "×2 на следующий скин каждые 30с (раз/ран)", "unit": "шт",
        "tiers": _build_tiers(5, lambda L: L, lambda L: 30000 * (1.85 ** (L - 1))),
    },

    # ═════════ ⚡ MOVEMENT (контроль) ═════════
    "slow_start": {
        "branch": "movement", "name": "Slow Start", "icon": "🐢",
        "desc": "Стартовая скорость медленнее (%)", "unit": "%",
        "tiers": _build_tiers(10, lambda L: L * 3, lambda L: 1500 * (1.40 ** (L - 1))),
    },
    "throttle": {
        "branch": "movement", "name": "Throttle", "icon": "⏸",
        "desc": "Замедление 50% — секунд/ран", "unit": "с",
        "tiers": _build_tiers(10, lambda L: L, lambda L: 5000 * (1.55 ** (L - 1))),
    },
    "speed_burst": {
        "branch": "movement", "name": "Speed Burst", "icon": "🚀",
        "desc": "Спринтов на ран", "unit": "шт",
        "tiers": _build_tiers(10, lambda L: L, lambda L: 4000 * (1.50 ** (L - 1))),
    },
    "perfect_brake": {
        "branch": "movement", "name": "Perfect Brake", "icon": "🛑",
        "desc": "Мгновенных остановок/ран", "unit": "шт",
        "tiers": _build_tiers(3, lambda L: L, lambda L: 25000 * (2.5 ** (L - 1))),
    },
    "pause_token": {
        "branch": "movement", "name": "Pause Token", "icon": "⏯",
        "desc": "2-сек заморозок/ран", "unit": "шт",
        "tiers": _build_tiers(5, lambda L: L, lambda L: 15000 * (1.85 ** (L - 1))),
    },
    "quantum_leap": {
        "branch": "movement", "name": "Quantum Leap", "icon": "🌀",
        "desc": "Телепортов на 3 клетки/ран", "unit": "шт",
        "tiers": _build_tiers(5, lambda L: L, lambda L: 50000 * (2.0 ** (L - 1))),
    },

    # ═════════ 🎯 PERCEPTION (магия) ═════════
    "magnet_range": {
        "branch": "perception", "name": "Magnet Range", "icon": "🧲",
        "desc": "Радиус притяжения скинов (клеток)", "unit": "кл",
        "tiers": _build_tiers(10, lambda L: max(0, (L + 1) // 4), lambda L: 6000 * (1.55 ** (L - 1))),
    },
    "skin_vacuum": {
        "branch": "perception", "name": "Skin Vacuum", "icon": "🌪",
        "desc": "Шанс авто-пожирать соседнего скина (%)", "unit": "%",
        "tiers": _build_tiers(5, lambda L: L * 5, lambda L: 35000 * (1.95 ** (L - 1))),
    },
    "ghost_mode": {
        "branch": "perception", "name": "Ghost Mode", "icon": "👻",
        "desc": "Сек неуязвимости/ран", "unit": "с",
        "tiers": _build_tiers(10, lambda L: L, lambda L: 12000 * (1.55 ** (L - 1))),
    },
    "double_bite": {
        "branch": "perception", "name": "Double Bite", "icon": "👯",
        "desc": "Шанс заспавнить 2 скина (%)", "unit": "%",
        "tiers": _build_tiers(15, lambda L: 4 + L * 2, lambda L: 5000 * (1.45 ** (L - 1))),
    },
    "map_vision": {
        "branch": "perception", "name": "Map Vision", "icon": "🔮",
        "desc": "Подсветка следующего премиум-скина (с)", "unit": "с",
        "tiers": _build_tiers(10, lambda L: L * 0.5, lambda L: 8000 * (1.50 ** (L - 1))),
    },
    "skin_radar": {
        "branch": "perception", "name": "Skin Radar", "icon": "📡",
        "desc": "Стрелка к ближайшему mythic", "unit": "вкл",
        "tiers": _build_tiers(5, lambda L: L, lambda L: 40000 * (1.90 ** (L - 1))),
    },
    "time_slow": {
        "branch": "perception", "name": "Time Slow", "icon": "⏳",
        "desc": "Замедление мира при крите (%)", "unit": "%",
        "tiers": _build_tiers(10, lambda L: L * 5, lambda L: 18000 * (1.55 ** (L - 1))),
    },

    # ═════════ 🏞 FIELD (поле) ═════════
    "field_expansion": {
        "branch": "field", "name": "Field Expansion", "icon": "🗺",
        "desc": "Размер поля (клеток)", "unit": "×",
        "tiers": _build_tiers(10, lambda L: 15 + L * 2, lambda L: 25000 * (1.85 ** (L - 1))),
    },
    "obstacle_smash": {
        "branch": "field", "name": "Obstacle Smash", "icon": "💢",
        "desc": "Таранов препятствий/ран", "unit": "шт",
        "tiers": _build_tiers(5, lambda L: L, lambda L: 30000 * (1.95 ** (L - 1))),
    },
    "tail_whip": {
        "branch": "field", "name": "Tail Whip", "icon": "🪢",
        "desc": "Хвостом ломаешь препятствия позади", "unit": "вкл",
        "tiers": _build_tiers(5, lambda L: L, lambda L: 60000 * (1.80 ** (L - 1))),
    },
    "skin_density": {
        "branch": "field", "name": "Skin Density", "icon": "🌾",
        "desc": "Плотность спавна скинов (+%)", "unit": "%",
        "tiers": _build_tiers(10, lambda L: L * 10, lambda L: 7000 * (1.55 ** (L - 1))),
    },
    "map_cleaner": {
        "branch": "field", "name": "Map Cleaner", "icon": "🧹",
        "desc": "Шанс убрать препятствие при укусе (%)", "unit": "%",
        "tiers": _build_tiers(10, lambda L: L * 1.5, lambda L: 14000 * (1.65 ** (L - 1))),
    },
    "layout_memory": {
        "branch": "field", "name": "Layout Memory", "icon": "🧠",
        "desc": "Препятствия гаснут на твоём пути", "unit": "вкл",
        "tiers": _build_tiers(5, lambda L: L, lambda L: 80000 * (1.90 ** (L - 1))),
    },

    # ═════════ 🤖 LIFETIME (постоянные) ═════════
    "total_multiplier": {
        "branch": "lifetime", "name": "Total Multiplier", "icon": "✨",
        "desc": "Глобальный ×к ВСЕМ источникам монет", "unit": "×",
        "tiers": _build_tiers(40, lambda L: 1.03 ** L, lambda L: 8000 * (1.40 ** (L - 1))),
    },
    "daily_bonus": {
        "branch": "lifetime", "name": "Daily Bonus", "icon": "🎁",
        "desc": "+% к первому рану в день", "unit": "%",
        "tiers": _build_tiers(10, lambda L: L * 10, lambda L: 5000 * (1.55 ** (L - 1))),
    },
    "afk_cap_extender": {
        "branch": "lifetime", "name": "AFK Cap Extender", "icon": "🕰",
        "desc": "+часов к лимиту AFK-фарма", "unit": "ч",
        "tiers": _build_tiers(10, lambda L: 4 + L, lambda L: 50000 * (1.85 ** (L - 1))),
    },
    "afk_rate_boost": {
        "branch": "lifetime", "name": "AFK Rate Boost", "icon": "⏫",
        "desc": "+% к ставке всех AFK-змеек", "unit": "%",
        "tiers": _build_tiers(20, lambda L: L * 5, lambda L: 80000 * (1.75 ** (L - 1))),
    },
    "snake_xp_boost": {
        "branch": "lifetime", "name": "Snake XP Boost", "icon": "📚",
        "desc": "+% XP за всё", "unit": "%",
        "tiers": _build_tiers(10, lambda L: L * 10, lambda L: 12000 * (1.65 ** (L - 1))),
    },
    "skin_drop_plus": {
        "branch": "lifetime", "name": "Rarity Lift", "icon": "⬆️",
        "desc": "Шанс сдвинуть редкость скина вверх (%)", "unit": "%",
        "tiers": _build_tiers(10, lambda L: L * 1, lambda L: 22000 * (1.70 ** (L - 1))),
    },
    "universal_magnet": {
        "branch": "lifetime", "name": "Universal Magnet", "icon": "🌐",
        "desc": "×ко всем мультипликаторам апгрейдов", "unit": "×",
        "tiers": _build_tiers(10, lambda L: 1.01 ** L, lambda L: 100000 * (2.0 ** (L - 1))),
    },
}

# Branches metadata (display)
BRANCHES: list[dict] = [
    {"key": "body",       "name": "Тело",       "icon": "🐍", "color": "#5cc15c"},
    {"key": "greed",      "name": "Жадность",   "icon": "💰", "color": "#f5b042"},
    {"key": "movement",   "name": "Движение",   "icon": "⚡", "color": "#5aa9ff"},
    {"key": "perception", "name": "Восприятие", "icon": "🎯", "color": "#d32ce6"},
    {"key": "field",      "name": "Поле",       "icon": "🏞", "color": "#a988ff"},
    {"key": "lifetime",   "name": "Lifetime",   "icon": "✨", "color": "#ffd700"},
]


# ============================================================
# AFK FARM SNAKES — 7 species
# ============================================================
#
# Each has a base price (1st copy), base coin/min rate, max upgrade level (50),
# and per-level rate multiplier. Cost of next copy = base * (2 ** copies_owned).
# Upgrade cost for copy at lvl L = base_cost * (1.4 ** L).

AFK_SNAKES: list[dict] = [
    {"key": "garter",  "name": "🟢 Garter",         "icon": "🟢", "base_cost":          5_000, "base_rate":      5,
     "rate_mult": 1.10, "upgrade_cost_base":      1_000, "color": "#5cc15c"},
    {"key": "python",  "name": "🟡 Python",         "icon": "🟡", "base_cost":        100_000, "base_rate":     30,
     "rate_mult": 1.10, "upgrade_cost_base":     20_000, "color": "#f5b042"},
    {"key": "cobra",   "name": "🔵 Cobra",          "icon": "🔵", "base_cost":      2_500_000, "base_rate":    200,
     "rate_mult": 1.10, "upgrade_cost_base":    500_000, "color": "#5aa9ff"},
    {"key": "anaconda","name": "🟣 Anaconda",       "icon": "🟣", "base_cost":     75_000_000, "base_rate":  1_500,
     "rate_mult": 1.10, "upgrade_cost_base": 15_000_000, "color": "#a988ff"},
    {"key": "mamba",   "name": "🔴 Mamba",          "icon": "🔴", "base_cost":  2_500_000_000, "base_rate": 12_000,
     "rate_mult": 1.10, "upgrade_cost_base":500_000_000, "color": "#eb4b4b"},
    {"key": "hydra",   "name": "🌟 Hydra",          "icon": "🌟", "base_cost":100_000_000_000, "base_rate":100_000,
     "rate_mult": 1.10, "upgrade_cost_base": 20_000_000_000, "color": "#ffe85c"},
    {"key": "cosmic",  "name": "🌌 Cosmic Serpent", "icon": "🌌", "base_cost": 5_000_000_000_000, "base_rate":800_000,
     "rate_mult": 1.10, "upgrade_cost_base": 1_000_000_000_000, "color": "#7340c4"},
]
AFK_SNAKE_BY_KEY = {s["key"]: s for s in AFK_SNAKES}
AFK_SNAKE_MAX_LEVEL = 50


def afk_snake_buy_cost(snake_key: str, copies_owned: int) -> int:
    """Cost of buying the (copies_owned + 1)th copy. Doubles each."""
    s = AFK_SNAKE_BY_KEY[snake_key]
    return int(s["base_cost"] * (2 ** copies_owned))


def afk_snake_upgrade_cost(snake_key: str, current_level: int) -> int:
    """Cost of leveling a copy from `current_level` to `current_level + 1`."""
    s = AFK_SNAKE_BY_KEY[snake_key]
    return int(s["upgrade_cost_base"] * (1.4 ** current_level))


def afk_snake_rate(snake_key: str, level: int) -> float:
    """Coin/min rate for a single copy at given upgrade level."""
    s = AFK_SNAKE_BY_KEY[snake_key]
    return s["base_rate"] * (s["rate_mult"] ** level)


# Daily AFK cap by snake-game level (player-level, not upgrade level)
def daily_afk_cap_for(player_level: int) -> int:
    if player_level <= 10:    return      500_000
    if player_level <= 30:    return    2_500_000
    if player_level <= 50:    return    5_000_000
    if player_level <= 75:    return   50_000_000
    if player_level <= 100:   return  500_000_000
    return 5_000_000_000


# Offline cap (hours of AFK accumulation when player not logged in)
DEFAULT_OFFLINE_CAP_H = 4


# ============================================================
# COSMETIC SKINS — 10 visuals
# ============================================================

COSMETIC_SKINS: list[dict] = [
    {"key": "default",  "name": "Default Garter",  "price":            0, "rarity": "common",     "preview": "linear-gradient(90deg,#5cc15c,#3a8c3a)"},
    {"key": "cyber",    "name": "Cyber Mamba",     "price":       50_000, "rarity": "uncommon",   "preview": "linear-gradient(90deg,#00ffe1,#0084ff)"},
    {"key": "rainbow",  "name": "Rainbow",         "price":      200_000, "rarity": "rare",       "preview": "linear-gradient(90deg,#ff5757,#ffe85c,#5cc15c,#5aa9ff,#d32ce6)"},
    {"key": "dragon",   "name": "Dragon",          "price":    1_000_000, "rarity": "epic",       "preview": "linear-gradient(90deg,#b04a18,#ffd700,#b04a18)"},
    {"key": "electric", "name": "Electric",        "price":    5_000_000, "rarity": "epic",       "preview": "linear-gradient(90deg,#fffd6e,#5aa9ff,#fffd6e)"},
    {"key": "skull",    "name": "Skull Trail",     "price":   25_000_000, "rarity": "legendary",  "preview": "linear-gradient(90deg,#1a1a1a,#666,#1a1a1a)"},
    {"key": "phoenix",  "name": "Phoenix",         "price":  100_000_000, "rarity": "legendary",  "preview": "linear-gradient(90deg,#ff5500,#ffd700,#ffe066)"},
    {"key": "cosmic",   "name": "Cosmic Serpent",  "price": 1_000_000_000, "rarity": "mythic",    "preview": "linear-gradient(90deg,#0a0a14,#7340c4,#1a1a44)"},
    {"key": "royal",    "name": "Royal Jubilee",   "price":10_000_000_000, "rarity": "mythic",    "preview": "linear-gradient(90deg,#ffd700,#fff5b8,#ffd700)"},
    {"key": "universe", "name": "ВСЕЛЕННАЯ",       "price":100_000_000_000, "rarity": "ultralegendary", "preview": "linear-gradient(90deg,#00ffff,#ff00ff,#ffff00,#00ffff)"},
]
COSMETIC_SKIN_BY_KEY = {s["key"]: s for s in COSMETIC_SKINS}


# ============================================================
# MAPS — 8 unlock-by-level
# ============================================================

MAPS: list[dict] = [
    {"key": "park",       "name": "🌱 Парк",            "unlock_lvl":   1, "size": 15, "obstacles": 0,  "moving": 0, "theme": "#1a3a1a"},
    {"key": "forest",     "name": "🌳 Лес",             "unlock_lvl":   5, "size": 17, "obstacles": 5,  "moving": 0, "theme": "#0e2710"},
    {"key": "lab",        "name": "🧪 Лаборатория",     "unlock_lvl":  10, "size": 18, "obstacles": 4,  "moving": 4, "theme": "#0e1a2a"},
    {"key": "city",       "name": "🏙 Город",           "unlock_lvl":  20, "size": 20, "obstacles": 10, "moving": 4, "theme": "#161616"},
    {"key": "casino",     "name": "🎰 Casino Floor",    "unlock_lvl":  30, "size": 22, "obstacles": 8,  "moving": 6, "theme": "#1f0f1a"},
    {"key": "darkweb",    "name": "💀 Dark Web",        "unlock_lvl":  50, "size": 25, "obstacles": 12, "moving": 8, "theme": "#000000"},
    {"key": "cosmic",     "name": "🌌 Cosmic",          "unlock_lvl":  75, "size": 28, "obstacles": 14, "moving":10, "theme": "#0a0a14"},
    {"key": "endgame",    "name": "🐲 Endgame",         "unlock_lvl": 100, "size": 35, "obstacles": 20, "moving":12, "theme": "#1a0a0a"},
]
MAP_BY_KEY = {m["key"]: m for m in MAPS}


# ============================================================
# GAME MODES — unlocked by level
# ============================================================

MODES: list[dict] = [
    {"key": "classic",   "name": "Classic",     "unlock_lvl":  1, "duration_sec": 0,   "desc": "Без таймера"},
    {"key": "time_trial","name": "Time Trial",  "unlock_lvl":  5, "duration_sec": 60,  "desc": "60 секунд"},
    {"key": "survival",  "name": "Survival",    "unlock_lvl": 15, "duration_sec": 0,   "desc": "Препятствий +20% / минуту"},
    {"key": "hunt",      "name": "Hunt",        "unlock_lvl": 30, "duration_sec":120,  "desc": "Скушай 3 mythic за 2 мин"},
]


# ============================================================
# LEVEL / XP
# ============================================================

def xp_needed_for(level: int) -> int:
    """XP threshold to reach `level + 1` from level 1 cumulative."""
    if level < 1:
        return 0
    return int(100 * (level ** 1.6))


def level_for_xp(xp: int) -> int:
    """Reverse: max level achievable with given total XP."""
    if xp < 0:
        return 1
    lvl = 1
    while xp >= xp_needed_for(lvl):
        lvl += 1
        if lvl > 200:
            break
    return lvl


# ============================================================
# DB / SCHEMA
# ============================================================

async def ensure_schema() -> None:
    sql_path = Path(__file__).parent.parent / "db" / "migration_snake.sql"
    if not sql_path.exists():
        log.warning("snake migration SQL missing")
        return
    sql = sql_path.read_text(encoding="utf-8")
    async with pool().acquire() as conn:
        await conn.execute(sql)
    log.info("snake schema ensured")


async def ensure_user(tg_id: int) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "insert into snake_users (tg_id) values ($1) on conflict do nothing",
            tg_id,
        )


def _parse_jsonb(val) -> Any:
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        try: return json.loads(val)
        except Exception: return None
    return None


# ============================================================
# AFK TICK — runs lazily on every state fetch
# ============================================================

async def _tick_afk(tg_id: int) -> int:
    """Accumulate AFK farm income since last tick. Returns coins gained."""
    now = datetime.now(timezone.utc)
    today = now.date()
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select level, last_afk_tick_at, daily_afk_earned, daily_afk_day, "
                "afk_snakes, upgrades from snake_users where tg_id = $1 for update",
                tg_id,
            )
            if row is None:
                return 0
            afk_snakes = _parse_jsonb(row["afk_snakes"]) or {}
            upgrades = _parse_jsonb(row["upgrades"]) or {}

            # Total coin/min from all owned AFK snakes
            total_rate_per_min = 0.0
            for key, levels in afk_snakes.items():
                if key not in AFK_SNAKE_BY_KEY:
                    continue
                if not isinstance(levels, list):
                    continue
                for lvl in levels:
                    try:
                        total_rate_per_min += afk_snake_rate(key, int(lvl or 0))
                    except Exception:
                        pass

            # AFK Rate Boost upgrade
            rate_boost_lvl = int(upgrades.get("afk_rate_boost", 0))
            if rate_boost_lvl > 0:
                total_rate_per_min *= (1 + rate_boost_lvl * 0.05)

            # Universal Magnet
            um_lvl = int(upgrades.get("universal_magnet", 0))
            if um_lvl > 0:
                total_rate_per_min *= (1.01 ** um_lvl)

            if total_rate_per_min <= 0:
                # Still update last_tick if missing so future ticks don't claim huge ranges
                if row["last_afk_tick_at"] is None:
                    await conn.execute(
                        "update snake_users set last_afk_tick_at = $2 where tg_id = $1",
                        tg_id, now,
                    )
                return 0

            # Offline cap (4h base + AFK Cap Extender)
            cap_extender_lvl = int(upgrades.get("afk_cap_extender", 0))
            offline_cap_h = DEFAULT_OFFLINE_CAP_H + cap_extender_lvl
            offline_cap_sec = offline_cap_h * 3600

            last_tick = row["last_afk_tick_at"]
            elapsed = 0.0 if last_tick is None else (now - last_tick).total_seconds()
            elapsed = min(elapsed, offline_cap_sec)

            gross = int(total_rate_per_min * (elapsed / 60.0))
            if gross <= 0:
                return 0

            # Daily cap by player level
            daily_cap = daily_afk_cap_for(int(row["level"]))
            daily_today = 0 if row["daily_afk_day"] != today else int(row["daily_afk_earned"] or 0)
            cap_left = max(0, daily_cap - daily_today)
            credited = min(gross, cap_left)

            if credited > 0:
                # Credit to economy_users.balance directly
                await conn.execute(
                    "update economy_users set balance = balance + $2, "
                    "total_earned = total_earned + $2 where tg_id = $1",
                    tg_id, credited,
                )
                await conn.execute(
                    "update snake_users set "
                    "  coins_lifetime = coins_lifetime + $2, "
                    "  daily_afk_earned = $3, daily_afk_day = $4, "
                    "  last_afk_tick_at = $5 "
                    "where tg_id = $1",
                    tg_id, credited, daily_today + credited, today, now,
                )
                # Audit + transaction log (best-effort)
                try:
                    new_bal_row = await conn.fetchrow(
                        "select balance from economy_users where tg_id = $1", tg_id,
                    )
                    new_bal = int(new_bal_row["balance"]) if new_bal_row else 0
                    await conn.execute(
                        "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                        "values ($1, $2, 'snake_afk', $3, $4)",
                        tg_id, credited, f"afk_tick_{int(elapsed)}s", new_bal,
                    )
                except Exception:
                    pass
            else:
                # Daily cap hit — still advance tick to "now" so we don't get stuck
                await conn.execute(
                    "update snake_users set last_afk_tick_at = $2 where tg_id = $1",
                    tg_id, now,
                )
    return credited


async def afk_loop() -> None:
    """Background scheduler: tick every snake_user once a minute so coins
    accumulate even without active state polls. Cheap (one query per user)."""
    import asyncio
    while True:
        try:
            await asyncio.sleep(60)
            async with pool().acquire() as conn:
                rows = await conn.fetch(
                    "select tg_id from snake_users "
                    "where afk_snakes <> '{}'::jsonb"
                )
            for r in rows:
                try:
                    await _tick_afk(int(r["tg_id"]))
                except Exception:
                    log.debug("afk tick failed for tg_id=%s", r["tg_id"])
        except Exception:
            log.exception("snake afk_loop tick failed")


# ============================================================
# READ STATE
# ============================================================

async def get_state(tg_id: int) -> dict:
    await ensure_user(tg_id)
    afk_gained = await _tick_afk(tg_id)
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "select * from snake_users where tg_id = $1", tg_id,
        )
        # Also pull current casino balance so the client can render a live balance
        # display (AFK farm credits this in _tick_afk above; without it the UI
        # would have to make a second /api/me call to learn what changed).
        bal_row = await conn.fetchrow(
            "select balance from economy_users where tg_id = $1", tg_id,
        )
    if row is None:
        return {}

    upgrades = _parse_jsonb(row["upgrades"]) or {}
    afk_snakes = _parse_jsonb(row["afk_snakes"]) or {}
    owned_skins = _parse_jsonb(row["owned_skins"]) or ["default"]
    unlocked_maps = _parse_jsonb(row["unlocked_maps"]) or ["park"]
    achievements = _parse_jsonb(row["achievements"]) or []

    # XP/level — recompute level from xp in case formula changed
    cur_xp = int(row["xp"] or 0)
    cur_lvl = level_for_xp(cur_xp)
    cur_level_xp = xp_needed_for(cur_lvl - 1) if cur_lvl > 1 else 0
    next_level_xp = xp_needed_for(cur_lvl)

    # Total AFK rate display
    total_afk_rate = 0.0
    for key, levels in afk_snakes.items():
        if key in AFK_SNAKE_BY_KEY and isinstance(levels, list):
            for lvl in levels:
                try:
                    total_afk_rate += afk_snake_rate(key, int(lvl or 0))
                except Exception:
                    pass
    rate_boost_lvl = int(upgrades.get("afk_rate_boost", 0))
    if rate_boost_lvl > 0:
        total_afk_rate *= (1 + rate_boost_lvl * 0.05)
    um_lvl = int(upgrades.get("universal_magnet", 0))
    if um_lvl > 0:
        total_afk_rate *= (1.01 ** um_lvl)

    # Pre-computed run-wide coin multiplier — client multiplies every eat
    # popup by this so the live counter reflects what will actually be
    # credited at run end (no more "20K shown, 34K paid" surprise).
    greed_mult     = 1 + int(upgrades.get("greed_boost", 0)) * 0.02
    total_mult     = 1.03 ** int(upgrades.get("total_multiplier", 0))
    universal_mult = 1.01 ** int(upgrades.get("universal_magnet", 0))
    today          = datetime.now(timezone.utc).date()
    last_run_at    = row.get("last_run_at") if hasattr(row, "get") else None
    is_first_today = (last_run_at is None) or (last_run_at.date() < today)
    daily_bonus_lvl = int(upgrades.get("daily_bonus", 0))
    daily_bonus_mult = (1 + daily_bonus_lvl * 0.10) if (is_first_today and daily_bonus_lvl > 0) else 1.0
    coin_mult = round(greed_mult * total_mult * universal_mult * daily_bonus_mult, 4)

    return {
        "tg_id":             int(row["tg_id"]),
        "level":             cur_lvl,
        "xp":                cur_xp,
        "current_level_xp":  cur_level_xp,
        "next_level_xp":     next_level_xp,
        "balance":           int(bal_row["balance"]) if bal_row else 0,
        "coins_lifetime":    int(row["coins_lifetime"]),
        "runs_count":        int(row["runs_count"]),
        "total_skins_eaten": int(row["total_skins_eaten"]),
        "best_run_coins":    int(row["best_run_coins"]),
        "best_run_length":   int(row["best_run_length"]),
        "current_skin_id":   row["current_skin_id"],
        "owned_skins":       owned_skins,
        "current_map_id":    row["current_map_id"],
        "unlocked_maps":     unlocked_maps,
        "upgrades":          upgrades,
        "afk_snakes":        afk_snakes,
        "afk_rate_per_min":  round(total_afk_rate, 2),
        "afk_cap_today":     daily_afk_cap_for(cur_lvl),
        "daily_afk_earned":  int(row["daily_afk_earned"] or 0) if row["daily_afk_day"] == datetime.now(timezone.utc).date() else 0,
        "afk_just_gained":   int(afk_gained),
        "achievements":      achievements,
        "coin_mult":         coin_mult,
        "is_first_today":    is_first_today,
    }


async def get_config() -> dict:
    """Static config served once to client."""
    return {
        "rarities":  RARITIES,
        "branches":  BRANCHES,
        "upgrades":  [
            {
                "key": k,
                "branch": v["branch"], "name": v["name"], "icon": v["icon"],
                "desc": v["desc"], "unit": v["unit"],
                "tiers": v["tiers"],
                "max_level": len(v["tiers"]),
            } for k, v in UPGRADE_DEFS.items()
        ],
        "afk_snakes": AFK_SNAKES,
        "afk_snake_max_level": AFK_SNAKE_MAX_LEVEL,
        "skins":     COSMETIC_SKINS,
        "maps":      MAPS,
        "modes":     MODES,
        "max_coins_per_second": MAX_COINS_PER_SECOND,
    }


# ============================================================
# RUN — record a finished run from client (server validates)
# ============================================================

async def record_run(
    tg_id: int,
    rarity_counts: dict[str, int],     # eg {"consumer": 8, "milspec": 2, ...}
    duration_sec: int,
    length: int,
    mode: str,
    map_id: str,
    died_to: str,
    coins_earned: int = 0,             # client's per-eat sum (lucky/crit/combo/streak/treasure already applied)
) -> dict:
    """Validate + apply a run result. Returns coins_credited + new state summary."""
    await ensure_user(tg_id)

    # === Validate inputs ===
    if duration_sec < 0 or duration_sec > 7200:        # 2h sanity ceiling
        return {"ok": False, "error": "Invalid duration"}
    if not isinstance(rarity_counts, dict):
        return {"ok": False, "error": "Bad rarity_counts"}
    if mode not in {m["key"] for m in MODES}:
        return {"ok": False, "error": "Bad mode"}
    if map_id not in MAP_BY_KEY:
        return {"ok": False, "error": "Bad map"}

    # Sanitize counts
    cleaned: dict[str, int] = {}
    skins_eaten = 0
    for r in RARITIES:
        n = max(0, int(rarity_counts.get(r["key"], 0) or 0))
        cleaned[r["key"]] = n
        skins_eaten += n
    if skins_eaten > 5_000:
        return {"ok": False, "error": "Suspicious skin count"}

    # === Compute coins (server authoritative) ===
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "select level, xp, runs_count, best_run_coins, best_run_length, "
            "       last_run_at, upgrades, current_map_id "
            "from snake_users where tg_id = $1 for update", tg_id,
        )
    upgrades = _parse_jsonb((row or {}).get("upgrades")) or {}

    greed_lvl = int(upgrades.get("greed_boost", 0))
    greed_mult = 1 + greed_lvl * 0.02   # +2%/lvl, max +100%

    lucky_lvl = int(upgrades.get("lucky_strike", 0))
    lucky_p = lucky_lvl * 0.02

    crit_lvl = int(upgrades.get("critical_bite", 0))
    crit_p = crit_lvl * 0.005

    total_mult_lvl = int(upgrades.get("total_multiplier", 0))
    total_mult = (1.03 ** total_mult_lvl) if total_mult_lvl > 0 else 1.0

    um_lvl = int(upgrades.get("universal_magnet", 0))
    um_mult = (1.01 ** um_lvl) if um_lvl > 0 else 1.0

    # Daily Bonus — applies to first run of the day
    daily_bonus_lvl = int(upgrades.get("daily_bonus", 0))
    daily_bonus_mult = 1.0
    today = datetime.now(timezone.utc).date()
    last_run_at = (row or {}).get("last_run_at")
    is_first_today = (last_run_at is None) or (last_run_at.date() < today)
    if is_first_today and daily_bonus_lvl > 0:
        daily_bonus_mult = 1 + daily_bonus_lvl * 0.10

    # XP по факту скушанных скинов
    xp_total = 0
    for r in RARITIES:
        n = cleaned[r["key"]]
        if n > 0:
            xp_total += r["xp"] * n

    # === Coin reward ===
    # Strategy: trust the client's per-eat sum (which already factored in lucky,
    # crit, combo, streak, treasure_pulse — all visible in the popup numbers
    # the user saw during play). Validate with an absolute upper bound, then
    # add server-only run-wide multipliers (greed, total, magnet, daily_bonus).
    #
    # Why: previously the server recomputed using statistical averages and
    # ignored combo/streak/treasure_pulse, leading to ~25-40% underpayment vs
    # what the client showed. Now the player gets credited what they earned.
    client_coins = max(0, int(coins_earned or 0))

    # Anti-cheat upper bound: theoretically MAXIMUM possible per skin = coin_max
    # x lucky(x2) x crit(x10) x combo(x4) x streak(x3) x treasure(x2) ≈ x480.
    # Sum across all eaten skins. We use a generous cap to allow legitimate
    # runs while rejecting blatantly inflated reports.
    max_possible = 0.0
    for r in RARITIES:
        n = cleaned[r["key"]]
        if n <= 0:
            continue
        # Per-skin theoretical ceiling with everything proccing simultaneously.
        # Even with all luck procs this is statistically unreachable in practice.
        per_skin_max = r["coin_max"] * 2 * 10 * 4 * 3 * 2  # x480
        max_possible += per_skin_max * n
    max_possible = int(max_possible)

    if client_coins > max_possible:
        # Suspicious — fall back to server-side average calc as a safe default
        avg_coins = 0.0
        for r in RARITIES:
            n = cleaned[r["key"]]
            if n > 0:
                avg_v = (r["coin_min"] + r["coin_max"]) / 2.0
                avg_coins += avg_v * n * (1 + lucky_p) * (1 + crit_p * 9)
        coins = int(avg_coins)
    else:
        # Trust client. If client sent 0 (older clients without coins_earned
        # field), fall back to server-avg so they still get something.
        if client_coins == 0:
            avg_coins = 0.0
            for r in RARITIES:
                n = cleaned[r["key"]]
                if n > 0:
                    avg_v = (r["coin_min"] + r["coin_max"]) / 2.0
                    avg_coins += avg_v * n * (1 + lucky_p) * (1 + crit_p * 9)
            coins = int(avg_coins)
        else:
            coins = client_coins

    # Apply RUN-WIDE multipliers (NOT applied client-side):
    coins = int(coins * greed_mult * total_mult * um_mult * daily_bonus_mult)

    # Recovery upgrade — adds back % of run earnings as a death bonus.
    # Description: "% от заработка возвращается после смерти" → flat reward
    # on top of computed coins. 5%/lvl → +50% at lvl 10.
    recovery_lvl = int(upgrades.get("recovery", 0))
    if recovery_lvl > 0 and died_to != "manual":
        coins = int(coins * (1 + recovery_lvl * 0.05))

    # Anti-cheat: max coins per second
    max_allowed = int(MAX_COINS_PER_SECOND * max(1, duration_sec))
    if coins > max_allowed:
        coins = max_allowed

    # XP multiplier
    xp_mult_lvl = int(upgrades.get("snake_xp_boost", 0))
    if xp_mult_lvl > 0:
        xp_total = int(xp_total * (1 + xp_mult_lvl * 0.10))

    # === Persist ===
    async with pool().acquire() as conn:
        async with conn.transaction():
            # Credit coins to economy
            new_bal_row = await conn.fetchrow(
                "update economy_users set balance = balance + $2, "
                "total_earned = total_earned + $2 where tg_id = $1 returning balance",
                tg_id, coins,
            )
            new_bal = int(new_bal_row["balance"]) if new_bal_row else 0
            try:
                await conn.execute(
                    "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                    "values ($1, $2, 'snake_run', $3, $4)",
                    tg_id, coins, f"snake_{mode}_{map_id}_skins{skins_eaten}", new_bal,
                )
            except Exception:
                pass

            # Update snake_users
            cur_xp = int((row or {}).get("xp", 0)) + xp_total
            cur_best_coins = max(int((row or {}).get("best_run_coins", 0)), coins)
            cur_best_len = max(int((row or {}).get("best_run_length", 0)), length)
            await conn.execute(
                """
                update snake_users set
                  xp = $2,
                  level = $3,
                  coins_lifetime = coins_lifetime + $4,
                  runs_count = runs_count + 1,
                  total_skins_eaten = total_skins_eaten + $5,
                  best_run_coins = $6,
                  best_run_length = $7,
                  last_run_at = $8
                where tg_id = $1
                """,
                tg_id, cur_xp, level_for_xp(cur_xp), coins, skins_eaten,
                cur_best_coins, cur_best_len, datetime.now(timezone.utc),
            )

            # Insert into history (rolling — keep last 100)
            await conn.execute(
                """
                insert into snake_runs (user_id, coins, length, skins_eaten, duration_sec, mode, map_id, died_to)
                values ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                tg_id, coins, length, skins_eaten, duration_sec, mode, map_id, died_to,
            )
            # Trim history to last 100
            await conn.execute(
                """
                delete from snake_runs where user_id = $1 and id not in (
                    select id from snake_runs where user_id = $1
                    order by created_at desc limit 100
                )
                """,
                tg_id,
            )

    # Audit (best-effort)
    try:
        from app.economy import audit as _audit
        await _audit.log_bet(
            tg_id, "snake", bet=0, win=coins, net=coins,
            details={
                "mode": mode, "map": map_id, "duration_sec": duration_sec,
                "length": length, "skins_eaten": skins_eaten,
                "by_rarity": cleaned, "died_to": died_to,
            },
            balance_after=new_bal,
        )
    except Exception:
        pass

    # Snake achievements (best-effort, won't block run)
    new_lvl = level_for_xp(cur_xp)
    achievements: list[dict] = []
    try:
        from app.economy import retention as _ret
        # Per-run checks
        run_ach = await _ret.check_achievements_after_action(tg_id, "snake_run", {
            "runs": int((row or {}).get("runs_count", 0)) + 1,
            "coins_this_run": coins,
            "length": length,
            "skins_eaten": skins_eaten,
            "by_rarity": cleaned,
            "lifetime": int((row or {}).get("coins_lifetime", 0)) + coins,
        })
        achievements.extend(run_ach)
        # Snake-level-up check (if level changed)
        old_lvl = int((row or {}).get("level", 1))
        if new_lvl > old_lvl:
            lvl_ach = await _ret.check_achievements_after_action(tg_id, "snake_level_up", {
                "level": new_lvl,
            })
            achievements.extend(lvl_ach)
    except Exception as e:
        log.debug("snake achievements check failed: %s", e)

    return {
        "ok": True,
        "coins_credited": coins,
        "xp_gained": xp_total,
        "skins_eaten": skins_eaten,
        "new_balance": new_bal,
        "new_xp": cur_xp,
        "new_level": new_lvl,
        "is_first_today": is_first_today,
        "daily_bonus_applied": daily_bonus_mult > 1,
        "achievements": achievements,
    }


# ============================================================
# UPGRADES
# ============================================================

async def buy_upgrade(tg_id: int, key: str) -> dict:
    if key not in UPGRADE_DEFS:
        return {"ok": False, "error": "Unknown upgrade"}
    cfg = UPGRADE_DEFS[key]
    tiers = cfg["tiers"]
    max_lvl = len(tiers)
    async with pool().acquire() as conn:
        async with conn.transaction():
            srow = await conn.fetchrow(
                "select upgrades from snake_users where tg_id = $1 for update", tg_id,
            )
            if srow is None:
                return {"ok": False, "error": "No state"}
            ups = _parse_jsonb(srow["upgrades"]) or {}
            cur = int(ups.get(key, 0))
            if cur >= max_lvl:
                return {"ok": False, "error": "Max level"}
            _, new_effect, cost = tiers[cur]   # tiers indexed by level-1
            erow = await conn.fetchrow(
                "select balance from economy_users where tg_id = $1 for update", tg_id,
            )
            if erow is None or int(erow["balance"]) < cost:
                return {"ok": False, "error": "Не хватает монет", "cost": cost}
            new_bal_row = await conn.fetchrow(
                "update economy_users set balance = balance - $2, total_spent = total_spent + $2 "
                "where tg_id = $1 returning balance",
                tg_id, cost,
            )
            new_bal = int(new_bal_row["balance"])
            ups[key] = cur + 1
            await conn.execute(
                "update snake_users set upgrades = $2::jsonb where tg_id = $1",
                tg_id, json.dumps(ups),
            )
            await conn.execute(
                "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                "values ($1, $2, 'snake_upgrade', $3, $4)",
                tg_id, -cost, f"upgrade_{key}_lvl{cur+1}", new_bal,
            )
    return {
        "ok": True,
        "key": key,
        "new_level": cur + 1,
        "effect": new_effect,
        "cost": cost,
        "new_balance": new_bal,
    }


# ============================================================
# AFK SNAKES
# ============================================================

async def buy_afk_snake(tg_id: int, snake_key: str) -> dict:
    if snake_key not in AFK_SNAKE_BY_KEY:
        return {"ok": False, "error": "Unknown snake"}
    async with pool().acquire() as conn:
        async with conn.transaction():
            srow = await conn.fetchrow(
                "select afk_snakes from snake_users where tg_id = $1 for update", tg_id,
            )
            if srow is None:
                return {"ok": False, "error": "No state"}
            sn = _parse_jsonb(srow["afk_snakes"]) or {}
            owned = sn.get(snake_key, [])
            if not isinstance(owned, list):
                owned = []
            cost = afk_snake_buy_cost(snake_key, len(owned))
            erow = await conn.fetchrow(
                "select balance from economy_users where tg_id = $1 for update", tg_id,
            )
            if erow is None or int(erow["balance"]) < cost:
                return {"ok": False, "error": "Не хватает монет", "cost": cost}
            new_bal_row = await conn.fetchrow(
                "update economy_users set balance = balance - $2, total_spent = total_spent + $2 "
                "where tg_id = $1 returning balance",
                tg_id, cost,
            )
            new_bal = int(new_bal_row["balance"])
            owned.append(0)            # new copy at level 0
            sn[snake_key] = owned
            await conn.execute(
                "update snake_users set afk_snakes = $2::jsonb where tg_id = $1",
                tg_id, json.dumps(sn),
            )
            await conn.execute(
                "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                "values ($1, $2, 'snake_afk_buy', $3, $4)",
                tg_id, -cost, f"buy_{snake_key}_copy{len(owned)}", new_bal,
            )

    # Achievements (best-effort)
    achievements: list[dict] = []
    try:
        from app.economy import retention as _ret
        types_owned = sum(1 for k, v in sn.items() if k in AFK_SNAKE_BY_KEY and isinstance(v, list) and len(v) > 0)
        achievements = await _ret.check_achievements_after_action(tg_id, "snake_afk_buy", {
            "total_owned_types": types_owned,
        })
    except Exception as e:
        log.debug("snake afk_buy achievements check failed: %s", e)

    return {"ok": True, "snake_key": snake_key, "copies": len(owned), "cost": cost,
            "new_balance": new_bal, "achievements": achievements}


async def upgrade_afk_snake(tg_id: int, snake_key: str, copy_idx: int) -> dict:
    if snake_key not in AFK_SNAKE_BY_KEY:
        return {"ok": False, "error": "Unknown snake"}
    async with pool().acquire() as conn:
        async with conn.transaction():
            srow = await conn.fetchrow(
                "select afk_snakes from snake_users where tg_id = $1 for update", tg_id,
            )
            if srow is None:
                return {"ok": False, "error": "No state"}
            sn = _parse_jsonb(srow["afk_snakes"]) or {}
            owned = sn.get(snake_key, [])
            if not isinstance(owned, list) or copy_idx < 0 or copy_idx >= len(owned):
                return {"ok": False, "error": "No such copy"}
            cur_lvl = int(owned[copy_idx])
            if cur_lvl >= AFK_SNAKE_MAX_LEVEL:
                return {"ok": False, "error": "Max level"}
            cost = afk_snake_upgrade_cost(snake_key, cur_lvl)
            erow = await conn.fetchrow(
                "select balance from economy_users where tg_id = $1 for update", tg_id,
            )
            if erow is None or int(erow["balance"]) < cost:
                return {"ok": False, "error": "Не хватает монет", "cost": cost}
            new_bal_row = await conn.fetchrow(
                "update economy_users set balance = balance - $2, total_spent = total_spent + $2 "
                "where tg_id = $1 returning balance",
                tg_id, cost,
            )
            new_bal = int(new_bal_row["balance"])
            owned[copy_idx] = cur_lvl + 1
            sn[snake_key] = owned
            await conn.execute(
                "update snake_users set afk_snakes = $2::jsonb where tg_id = $1",
                tg_id, json.dumps(sn),
            )
    return {"ok": True, "snake_key": snake_key, "copy_idx": copy_idx,
            "new_level": cur_lvl + 1, "cost": cost, "new_balance": new_bal}


# ============================================================
# COSMETIC SKINS + MAPS
# ============================================================

async def buy_skin(tg_id: int, skin_key: str) -> dict:
    if skin_key not in COSMETIC_SKIN_BY_KEY:
        return {"ok": False, "error": "Unknown skin"}
    sk = COSMETIC_SKIN_BY_KEY[skin_key]
    cost = int(sk["price"])
    async with pool().acquire() as conn:
        async with conn.transaction():
            srow = await conn.fetchrow(
                "select owned_skins from snake_users where tg_id = $1 for update", tg_id,
            )
            if srow is None:
                return {"ok": False, "error": "No state"}
            owned = _parse_jsonb(srow["owned_skins"]) or ["default"]
            if skin_key in owned:
                return {"ok": False, "error": "Already owned"}
            if cost > 0:
                erow = await conn.fetchrow(
                    "select balance from economy_users where tg_id = $1 for update", tg_id,
                )
                if erow is None or int(erow["balance"]) < cost:
                    return {"ok": False, "error": "Не хватает монет", "cost": cost}
                await conn.execute(
                    "update economy_users set balance = balance - $2, total_spent = total_spent + $2 "
                    "where tg_id = $1",
                    tg_id, cost,
                )
            owned.append(skin_key)
            await conn.execute(
                "update snake_users set owned_skins = $2::jsonb where tg_id = $1",
                tg_id, json.dumps(owned),
            )
    return {"ok": True, "skin_key": skin_key, "cost": cost}


async def equip_skin(tg_id: int, skin_key: str) -> dict:
    async with pool().acquire() as conn:
        srow = await conn.fetchrow(
            "select owned_skins from snake_users where tg_id = $1", tg_id,
        )
        if srow is None:
            return {"ok": False, "error": "No state"}
        owned = _parse_jsonb(srow["owned_skins"]) or ["default"]
        if skin_key not in owned:
            return {"ok": False, "error": "Not owned"}
        await conn.execute(
            "update snake_users set current_skin_id = $2 where tg_id = $1",
            tg_id, skin_key,
        )
    return {"ok": True, "current_skin_id": skin_key}


async def unlock_map(tg_id: int, map_id: str) -> dict:
    """Maps unlock automatically when player level meets the map's threshold;
    this endpoint just records that we showed the unlock to the player."""
    if map_id not in MAP_BY_KEY:
        return {"ok": False, "error": "Unknown map"}
    m = MAP_BY_KEY[map_id]
    async with pool().acquire() as conn:
        srow = await conn.fetchrow(
            "select level, unlocked_maps from snake_users where tg_id = $1", tg_id,
        )
        if srow is None:
            return {"ok": False, "error": "No state"}
        if int(srow["level"]) < m["unlock_lvl"]:
            return {"ok": False, "error": "Need higher level", "need_lvl": m["unlock_lvl"]}
        unlocked = _parse_jsonb(srow["unlocked_maps"]) or ["park"]
        if map_id not in unlocked:
            unlocked.append(map_id)
            await conn.execute(
                "update snake_users set unlocked_maps = $2::jsonb where tg_id = $1",
                tg_id, json.dumps(unlocked),
            )
    return {"ok": True, "map_id": map_id}


async def select_map(tg_id: int, map_id: str) -> dict:
    if map_id not in MAP_BY_KEY:
        return {"ok": False, "error": "Unknown map"}
    async with pool().acquire() as conn:
        srow = await conn.fetchrow(
            "select unlocked_maps from snake_users where tg_id = $1", tg_id,
        )
        if srow is None:
            return {"ok": False, "error": "No state"}
        unlocked = _parse_jsonb(srow["unlocked_maps"]) or ["park"]
        if map_id not in unlocked:
            return {"ok": False, "error": "Map not unlocked"}
        await conn.execute(
            "update snake_users set current_map_id = $2 where tg_id = $1",
            tg_id, map_id,
        )
    return {"ok": True, "current_map_id": map_id}


# ============================================================
# LEADERBOARD
# ============================================================

async def leaderboard(period: str = "all", limit: int = 20) -> list[dict]:
    """`period` = 'all' (best lifetime coins) or 'week' (best run coins this week)."""
    async with pool().acquire() as conn:
        if period == "week":
            since = datetime.now(timezone.utc) - timedelta(days=7)
            rows = await conn.fetch(
                """
                select r.user_id, max(r.coins) as best_coins, count(*) as runs,
                       u.username, u.first_name
                from snake_runs r
                left join users u on u.tg_id = r.user_id
                where r.created_at >= $1
                group by r.user_id, u.username, u.first_name
                order by best_coins desc nulls last
                limit $2
                """, since, limit,
            )
            return [
                {
                    "tg_id": int(r["user_id"]),
                    "username": r["username"],
                    "first_name": r["first_name"],
                    "best_coins": int(r["best_coins"] or 0),
                    "runs": int(r["runs"] or 0),
                }
                for r in rows
            ]
        # all-time (lifetime coins)
        rows = await conn.fetch(
            """
            select s.tg_id, s.coins_lifetime, s.runs_count, s.level,
                   s.best_run_coins, s.current_skin_id,
                   u.username, u.first_name
            from snake_users s
            left join users u on u.tg_id = s.tg_id
            order by s.coins_lifetime desc
            limit $1
            """, limit,
        )
        return [
            {
                "tg_id": int(r["tg_id"]),
                "username": r["username"],
                "first_name": r["first_name"],
                "level": int(r["level"]),
                "coins_lifetime": int(r["coins_lifetime"]),
                "runs": int(r["runs_count"]),
                "best_coins": int(r["best_run_coins"]),
                "skin": r["current_skin_id"],
            }
            for r in rows
        ]
