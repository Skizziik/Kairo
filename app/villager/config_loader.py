"""Loads JSON configs once at startup. Hot-reload supported via reload()."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_CONFIG_DIR = Path(__file__).parent / "config"


def _load(name: str) -> dict:
    return json.loads((_CONFIG_DIR / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def buildings() -> dict:
    return _load("buildings.json")


@lru_cache(maxsize=1)
def resources() -> dict:
    return _load("resources.json")


@lru_cache(maxsize=1)
def quests() -> dict:
    return _load("quests.json")


def reload() -> None:
    buildings.cache_clear()
    resources.cache_clear()
    quests.cache_clear()


MAP_SIZE = (16, 16)
TILE_SIZE = 128
OFFLINE_CAP_HOURS = 24
OFFLINE_EFFICIENCY = 0.5
ONLINE_THRESHOLD_SECONDS = 300
DEFAULT_BUILDER_SLOTS = 1
