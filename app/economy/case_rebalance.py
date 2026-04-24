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


# Per-case config.
#   max_items:       total pool size (proportional to rarity weights)
#   min_base_price:  exclude any skin with base_price < this (hard floor on shittiness)
# Cases not listed are left untouched.
CASE_CONFIG: dict[str, dict] = {
    "rip": {"max_items": 80, "min_base_price": 2500},
}


async def _trim_case(case_key: str, cfg: dict) -> bool:
    """Idempotent: rewrites loot_pool to match cfg. Returns True if changed."""
    max_items = int(cfg.get("max_items") or 80)
    min_price = int(cfg.get("min_base_price") or 0)

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

        # Fetch base_prices to apply min_price filter + sort
        all_ids = [int(i) for ids in by_rarity.values() for i in ids]
        if not all_ids:
            return False
        rows = await conn.fetch(
            "select id, base_price from economy_skins_catalog where id = any($1::int[])",
            all_ids,
        )
        price_by_id = {int(r["id"]): int(r["base_price"]) for r in rows}

        # Apply price floor
        filtered = {}
        for rarity, ids in by_rarity.items():
            qualifying = [int(i) for i in ids if price_by_id.get(int(i), 0) >= min_price]
            if qualifying:
                filtered[rarity] = qualifying

        # Allocate quota per rarity proportional to weights (using filtered pool only)
        active_weights = {k: v for k, v in rarity_weights.items() if filtered.get(k)}
        total_w = sum(active_weights.values()) or 1
        trimmed: dict[str, list[int]] = {}
        for rarity, weight in active_weights.items():
            ids = filtered[rarity]
            quota = max(3, round(max_items * (weight / total_w)))
            sorted_ids = sorted(ids, key=lambda i: price_by_id.get(int(i), 0), reverse=True)
            trimmed[rarity] = sorted_ids[:min(quota, len(sorted_ids))]

        new_total = sum(len(v) for v in trimmed.values())
        new_weights = {k: v for k, v in rarity_weights.items() if trimmed.get(k)}

        current_total = sum(len(v) for v in by_rarity.values())
        # Idempotency check: same pool? If ids/counts already match, skip write.
        same = (
            current_total == new_total
            and all(set(by_rarity.get(k, [])) == set(trimmed.get(k, [])) for k in set(by_rarity) | set(trimmed))
        )
        if same:
            log.info("case rebalance: %s already at target (%d), skip", case_key, new_total)
            return False

        new_loot = {"by_rarity": trimmed, "rarity_weights": new_weights}
        await conn.execute(
            "update economy_cases set loot_pool = $2::jsonb where id = $1",
            int(case["id"]), json.dumps(new_loot),
        )
    cheapest = min(
        (price_by_id[i] for ids in trimmed.values() for i in ids),
        default=0,
    )
    log.info(
        "case rebalance: %s: %d → %d items (min_price %d, actual cheapest %d)",
        case_key, current_total, new_total, min_price, cheapest,
    )
    return True


async def rebalance_all() -> None:
    for key, cfg in CASE_CONFIG.items():
        try:
            await _trim_case(key, cfg)
        except Exception as e:
            log.warning("case rebalance for %s failed: %s", key, e)
