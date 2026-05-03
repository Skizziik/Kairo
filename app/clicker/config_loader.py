"""Loads JSON configs once at startup. Hot-reload via reload()."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_CONFIG_DIR = Path(__file__).parent / "config"


def _load(name: str):
    return json.loads((_CONFIG_DIR / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def weapons() -> list:
    return _load("weapons.json")


@lru_cache(maxsize=1)
def mercs() -> list:
    return _load("mercs.json")


@lru_cache(maxsize=1)
def locations() -> list:
    return _load("locations.json")


@lru_cache(maxsize=1)
def bosses() -> list:
    return _load("bosses.json")


@lru_cache(maxsize=1)
def chests() -> dict:
    return _load("chests.json")


@lru_cache(maxsize=1)
def artifacts() -> list:
    return _load("artifacts.json")


@lru_cache(maxsize=1)
def mythics() -> list:
    return _load("mythics.json")


@lru_cache(maxsize=1)
def crit_luck() -> dict:
    return _load("crit_luck.json")


@lru_cache(maxsize=1)
def businesses() -> list:
    return _load("businesses.json")


@lru_cache(maxsize=1)
def resources_meta() -> dict:
    return _load("resources_meta.json")


def reload() -> None:
    for fn in (weapons, mercs, locations, bosses, chests, artifacts, mythics, crit_luck, businesses, resources_meta):
        fn.cache_clear()


# ---------- gameplay constants -----------------------------------------

# Business idle/upgrade.
BUSINESS_IDLE_GROWTH = 1.08      # production per_sec × this^level
BUSINESS_COST_GROWTH = 1.15      # upgrade cost × this^level
BUSINESS_IDLE_CAP_HOURS = 8      # max accumulation while away

LEVEL_TIME_NORMAL = 30        # seconds to kill normal enemy
LEVEL_TIME_BOSS = 40          # seconds to kill boss
HP_BASE = 10
HP_GROWTH = 1.55
HP_BOSS_MULT = 7.5
COIN_DROP_RATIO = 0.18
BOSS_COIN_MULT = 5
BOSS_CHEST_DROP_BASE = 0.05
COST_GROWTH = 1.15
DAMAGE_PER_LEVEL = 0.20       # +20% damage per upgrade level
CHECKPOINT_EVERY = 10
CASECOINS_RATE_SECONDS = 600  # 1 ⌬ per 10 minutes
CASECOINS_DAILY_CAP = 60
PRESTIGE_GLORY_DIVISOR = 20
PRESTIGE_GLORY_EXP = 1.5
ARTIFACT_SLOT_BASE = 2
ARTIFACT_SLOT_MAX = 6
RARITY_ORDER = ["common", "uncommon", "rare", "epic", "legendary", "mythic"]


def boss_for_level(level: int) -> dict | None:
    for b in bosses():
        if b["level"] == level:
            return b
    return None


def location_for_level(level: int) -> dict:
    locs = locations()
    if level > 75:
        # Endgame: wraps through the 15 locations again with +20% difficulty.
        idx = ((level - 1) % 75) // 5
    else:
        idx = (level - 1) // 5
    return locs[min(idx, len(locs) - 1)]


def enemy_for_level(level: int, seed: int = 0) -> str:
    """Pick a deterministic enemy sprite path for a given level + variant seed."""
    loc = location_for_level(level)
    enemies_in_loc = loc.get("enemies") or []
    if not enemies_in_loc:
        return ""
    return enemies_in_loc[(level + seed) % len(enemies_in_loc)]
