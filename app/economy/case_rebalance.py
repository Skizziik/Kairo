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
    # min_base_price 500 filters out true garbage (consumer Glocks at base 100)
    # but keeps cheap restricted/classified so there's real loss variance.
    "rip": {"max_items": 80, "min_base_price": 500},
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
    # Ensure special handcrafted cases exist
    try:
        await ensure_knife_or_nothing()
    except Exception as e:
        log.warning("knife_or_nothing seed failed: %s", e)


# ============================================================
# "НОЖ ИЛИ НИЧЕГО" — handcrafted 2-item lottery case
# ============================================================

KNIFE_OR_NOTHING_KEY = "knife_or_nothing"
KNIFE_OR_NOTHING_PRICE = 299
KNIFE_OR_NOTHING_IMAGE = (
    "https://community.akamai.steamstatic.com/economy/image/"
    "i0CoZ81Ui0m-9KwlBY1L_18myuGuq1wfhWSaZgMttyVfPaERSR0Wqmu7LAocGJKz2lu_XsnXwtmkJjSU"
    "91dh8bj35VTqVBP4io_frnEVvqf_a6VoIfGSXz7Hlbwg57QwSS_mxhl15jiGyN37c3_GZw91W8BwRflK7EfKsa2sfw"
)


async def ensure_knife_or_nothing() -> None:
    """Idempotent seeder for the 'НОЖ ИЛИ НИЧЕГО' case.
    Pool = exactly 2 skins: top-priced knife (2%) + cheapest weapon (98%)."""
    async with pool().acquire() as conn:
        # Pick the most expensive knife (base_price) available in the catalog
        knife = await conn.fetchrow(
            "select id, rarity, base_price from economy_skins_catalog "
            "where category = 'knife' and active order by base_price desc limit 1",
        )
        # Pick the cheapest weapon (any weapon category, lowest base_price)
        cheap = await conn.fetchrow(
            "select id, rarity, base_price from economy_skins_catalog "
            "where category = 'weapon' and active order by base_price asc limit 1",
        )
        if knife is None or cheap is None:
            log.warning("knife_or_nothing: catalog missing knife or cheap weapon, skip")
            return

        knife_rarity = knife["rarity"]
        cheap_rarity = cheap["rarity"]
        if knife_rarity == cheap_rarity:
            # Unlikely (knives = exceedingly_rare, cheapest = consumer/mil-spec),
            # but guard against both mapping to the same bucket — would break probabilities.
            log.warning("knife_or_nothing: knife and cheap share rarity %s, skipping seed", knife_rarity)
            return

        loot_pool = {
            "by_rarity": {
                knife_rarity: [int(knife["id"])],
                cheap_rarity: [int(cheap["id"])],
            },
            "rarity_weights": {
                knife_rarity: 0.02,
                cheap_rarity: 0.98,
            },
        }

        existing = await conn.fetchrow(
            "select id, loot_pool, price from economy_cases where key = $1",
            KNIFE_OR_NOTHING_KEY,
        )

        description = (
            f"2% нож (база {int(knife['base_price']):,} ⚙), 98% хуита (база {int(cheap['base_price']):,} ⚙). "
            "Скретч-офф: либо джекпот, либо в унитаз."
        ).replace(",", " ")

        if existing is None:
            await conn.execute(
                """
                insert into economy_cases (key, name, description, price, image_url, loot_pool, stat_trak_chance)
                values ($1, $2, $3, $4, $5, $6::jsonb, $7)
                """,
                KNIFE_OR_NOTHING_KEY,
                "🔪 Нож или ничего",
                description,
                KNIFE_OR_NOTHING_PRICE,
                KNIFE_OR_NOTHING_IMAGE,
                json.dumps(loot_pool),
                0.0,
            )
            log.info("knife_or_nothing: created (knife=%d base=%d, cheap=%d base=%d)",
                     int(knife["id"]), int(knife["base_price"]),
                     int(cheap["id"]), int(cheap["base_price"]))
        else:
            # Idempotency: only update if pool/price/description differ
            cur_pool = existing["loot_pool"]
            if isinstance(cur_pool, str):
                cur_pool = json.loads(cur_pool)
            if cur_pool == loot_pool and int(existing["price"]) == KNIFE_OR_NOTHING_PRICE:
                return
            await conn.execute(
                """
                update economy_cases set
                    name = $2, description = $3, price = $4, image_url = $5,
                    loot_pool = $6::jsonb, stat_trak_chance = $7
                where id = $1
                """,
                int(existing["id"]),
                "🔪 Нож или ничего",
                description,
                KNIFE_OR_NOTHING_PRICE,
                KNIFE_OR_NOTHING_IMAGE,
                json.dumps(loot_pool),
                0.0,
            )
            log.info("knife_or_nothing: updated")
