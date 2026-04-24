"""One-shot, idempotent case-pool rebalancer run at startup.

Purpose: cap the total number of skins in each case's loot_pool to a sane
visual/gameplay size (default 80 for flagship cases), distributed proportional
to rarity weights, picking top-priced skins within each rarity so the pool
still feels premium.

Safe to run on every deploy — it re-computes the expected trimmed pool and
writes only if the current DB state differs.
"""
from __future__ import annotations

import json
import logging

from app.db.client import pool

log = logging.getLogger(__name__)


# Per-case overrides. Cases not listed are left untouched.
CASE_MAX_ITEMS: dict[str, int] = {
    "rip": 80,
}


async def _trim_case(case_key: str, max_items: int) -> bool:
    """Returns True if case was trimmed/changed."""
    async with pool().acquire() as conn:
        case = await conn.fetchrow(
            "select id, loot_pool from economy_cases where key = $1", case_key,
        )
        if case is None:
            log.info("case rebalance: case %s not found, skip", case_key)
            return False

        loot_pool = case["loot_pool"]
        if isinstance(loot_pool, str):
            loot_pool = json.loads(loot_pool)
        by_rarity = dict(loot_pool.get("by_rarity", {}))
        rarity_weights = dict(loot_pool.get("rarity_weights", {}))

        current_total = sum(len(v) for v in by_rarity.values())
        if current_total <= max_items:
            log.info("case rebalance: %s already %d <= %d items, skip",
                     case_key, current_total, max_items)
            return False

        # Fetch base_prices for sorting
        all_ids = [int(i) for ids in by_rarity.values() for i in ids]
        rows = await conn.fetch(
            "select id, base_price from economy_skins_catalog where id = any($1::int[])",
            all_ids,
        )
        price_by_id = {int(r["id"]): int(r["base_price"]) for r in rows}

        # Allocate quota per rarity proportional to weights
        total_w = sum(rarity_weights.values()) or 1
        trimmed: dict[str, list[int]] = {}
        for rarity, weight in rarity_weights.items():
            ids = by_rarity.get(rarity, [])
            if not ids:
                continue
            quota = max(3, round(max_items * (weight / total_w)))  # at least 3 per rarity
            sorted_ids = sorted(ids, key=lambda i: price_by_id.get(int(i), 0), reverse=True)
            trimmed[rarity] = [int(x) for x in sorted_ids[:quota]]

        new_total = sum(len(v) for v in trimmed.values())
        # Only those rarities that kept items matter; filter weights too.
        new_weights = {k: v for k, v in rarity_weights.items() if trimmed.get(k)}

        new_loot = {"by_rarity": trimmed, "rarity_weights": new_weights}
        await conn.execute(
            "update economy_cases set loot_pool = $2::jsonb where id = $1",
            int(case["id"]), json.dumps(new_loot),
        )
    log.info("case rebalance: %s trimmed %d → %d items", case_key, current_total, new_total)
    return True


async def rebalance_all() -> None:
    for key, max_items in CASE_MAX_ITEMS.items():
        try:
            await _trim_case(key, max_items)
        except Exception as e:
            log.warning("case rebalance for %s failed: %s", key, e)
