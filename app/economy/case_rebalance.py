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
    # Source-of-truth weights for RIP. Knives now 1% (was 3%), covert pulled
    # down 3pp into restricted. House edge ≈ 45%.
    "rip": {
        "max_items": 80,
        "min_base_price": 0,
        "rarity_weights": {
            "restricted":       0.37,  # ↑ from 0.32 — more loss outcomes
            "classified":       0.40,  # unchanged
            "covert":           0.22,  # ↓ from 0.25
            "exceedingly_rare": 0.01,  # ↓ from 0.03 — knives now 1 in 100
        },
    },
}


async def _trim_case(case_key: str, cfg: dict) -> bool:
    """Idempotent: rebuilds loot_pool fresh from the CATALOG (not from the
    case's current stale pool), so re-running with different thresholds
    restores items that earlier aggressive filters removed. Returns True if
    pool actually changed."""
    max_items = int(cfg.get("max_items") or 80)
    min_price = int(cfg.get("min_base_price") or 0)
    # Prefer explicit weights from cfg — the DB copy may have been corrupted by an
    # earlier rebalance run that dropped empty rarities.
    forced_weights = cfg.get("rarity_weights")

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
        rarity_weights = dict(forced_weights) if forced_weights else dict(loot_pool.get("rarity_weights", {}))
        rarities_allowed = cfg.get("rarities") or list(rarity_weights.keys())

        # Rebuild pool fresh from catalog (don't trust current by_rarity — might
        # be stale from a prior rebalance with different thresholds).
        catalog_rows = await conn.fetch(
            "select id, rarity, base_price "
            "from economy_skins_catalog "
            "where active and rarity = any($1::text[]) and base_price >= $2 "
            "order by base_price desc",
            rarities_allowed, min_price,
        )
        ids_by_rarity: dict[str, list[tuple[int, int]]] = {}  # rarity -> [(id, price), ...]
        for r in catalog_rows:
            ids_by_rarity.setdefault(r["rarity"], []).append((int(r["id"]), int(r["base_price"])))

        # Allocate quotas proportional to rarity weights (only rarities with items)
        active_weights = {k: v for k, v in rarity_weights.items() if ids_by_rarity.get(k)}
        total_w = sum(active_weights.values()) or 1
        trimmed: dict[str, list[int]] = {}
        for rarity, weight in active_weights.items():
            quota = max(3, round(max_items * (weight / total_w)))
            items = ids_by_rarity[rarity]  # already sorted by price desc
            trimmed[rarity] = [i for i, _ in items[:min(quota, len(items))]]

        new_total = sum(len(v) for v in trimmed.values())
        new_weights = {k: v for k, v in rarity_weights.items() if trimmed.get(k)}

        # Idempotency: compare with current state
        by_rarity_cur = dict(loot_pool.get("by_rarity", {}))
        current_total = sum(len(v) for v in by_rarity_cur.values())
        same = (
            current_total == new_total
            and all(set(by_rarity_cur.get(k, [])) == set(trimmed.get(k, []))
                    for k in set(by_rarity_cur) | set(trimmed))
        )
        if same:
            log.info("case rebalance: %s already at target (%d), skip", case_key, new_total)
            return False

        new_loot = {"by_rarity": trimmed, "rarity_weights": new_weights}
        await conn.execute(
            "update economy_cases set loot_pool = $2::jsonb where id = $1",
            int(case["id"]), json.dumps(new_loot),
        )
    # Find the cheapest item kept (for logging)
    cheapest = 0
    for rarity, ids in trimmed.items():
        for iid in ids:
            for catid, p in ids_by_rarity.get(rarity, []):
                if catid == iid:
                    if cheapest == 0 or p < cheapest:
                        cheapest = p
                    break
    per_rarity = {k: len(v) for k, v in trimmed.items()}
    log.info(
        "case rebalance: %s: %d → %d items (min_price %d, cheapest kept %d, per-rarity %s)",
        case_key, current_total, new_total, min_price, cheapest, per_rarity,
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
