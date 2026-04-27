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
    try:
        await ensure_dragon_log()
    except Exception as e:
        log.warning("dragon_log seed failed: %s", e)
    # Custom prices set by the operator (idempotent — only updates when DB differs)
    for key, price in [
        ("masha_yu_know",   199),
        ("melkiy",          299),
        ("knife_or_nothing", 399),
        ("igor_king_of_mid", 399),
        ("lera_golova",     1299),
        ("rip",             3999),
    ]:
        try:
            await ensure_case_price(key, price)
        except Exception as e:
            log.warning("price update %s failed: %s", key, e)


# ============================================================
# Generic price-fix helper: bring an existing case's price to a target.
# Idempotent — only runs an UPDATE if current price differs.
# ============================================================

async def ensure_case_price(key: str, target_price: int) -> None:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "select id, price from economy_cases where key = $1", key,
        )
        if row is None:
            return
        if int(row["price"]) == int(target_price):
            return
        await conn.execute(
            "update economy_cases set price = $2 where id = $1",
            int(row["id"]), int(target_price),
        )
        log.info("case %s: price %s -> %s", key, row["price"], target_price)


# ============================================================
# "НОЖ ИЛИ НИЧЕГО" — handcrafted 2-item lottery case
# ============================================================

KNIFE_OR_NOTHING_KEY = "knife_or_nothing"
KNIFE_OR_NOTHING_PRICE = 399
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
            log.info("knife_or_nothing: created")
        else:
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


# ============================================================
# DRAGON LORE CASE — 30% AWP Dragon Lore, 70% cheap weapon trash
# ============================================================

DRAGON_LOG_KEY = "dragon_log"
DRAGON_LOG_PRICE = 49_999
DRAGON_LOG_IMAGE = (
    "https://community.akamai.steamstatic.com/economy/image/"
    "i0CoZ81Ui0m-9KwlBY1L_18myuGuq1wfhWSaZgMttyVfPaERSR0Wqmu7LAocGJKz2lu_XsnXwtmkJjSU"
    "91dh8bj35VTqVBP4io_frnAVvfb6aqduc_TFVjTCxbx05OU4S3jilE9w4DzRnImtIy2Sa1JzDJEhRPlK7EcO4U8gfA"
)


async def ensure_dragon_log() -> None:
    """30/70 lottery: 30% AWP Dragon Lore, 70% cheapest weapon. If catalog has
    no Dragon Lore exactly, try fuzzy (ILIKE), then fall back to top covert AWP."""
    async with pool().acquire() as conn:
        # Strict: exact name match
        dragon = await conn.fetchrow(
            "select id, full_name, rarity, base_price from economy_skins_catalog "
            "where weapon = 'AWP' and skin_name = 'Dragon Lore' and active limit 1",
        )
        # Fuzzy: maybe stored as 'Дракон лора' or with extra text
        if dragon is None:
            dragon = await conn.fetchrow(
                "select id, full_name, rarity, base_price from economy_skins_catalog "
                "where weapon = 'AWP' and active and (skin_name ilike '%dragon%' or skin_name ilike '%лор%') "
                "order by base_price desc limit 1",
            )
        # Last resort: top covert AWP (might be Asiimov or whatever else)
        if dragon is None:
            dragon = await conn.fetchrow(
                "select id, full_name, rarity, base_price from economy_skins_catalog "
                "where weapon = 'AWP' and active and rarity = 'covert' "
                "order by base_price desc limit 1",
            )
        if dragon is not None:
            log.info("dragon_log: using skin id=%d name='%s' rarity=%s base=%d",
                     int(dragon["id"]), dragon["full_name"], dragon["rarity"], int(dragon["base_price"]))
            # Bump price to 100k so the case feels rewarding (was ~9k → reward ~9k from 50k case = always loss)
            DRAGON_TARGET_BASE = 100_000
            if int(dragon["base_price"]) < DRAGON_TARGET_BASE:
                await conn.execute(
                    "update economy_skins_catalog set base_price = $2 where id = $1",
                    int(dragon["id"]), DRAGON_TARGET_BASE,
                )
                log.info("dragon_log: bumped skin id=%d base_price → %d", int(dragon["id"]), DRAGON_TARGET_BASE)
        cheap = await conn.fetchrow(
            "select id, rarity, base_price from economy_skins_catalog "
            "where category = 'weapon' and active "
            "order by base_price asc limit 1",
        )
        if dragon is None or cheap is None:
            log.warning("dragon_log: catalog missing required skins, skip")
            return

        dragon_rarity = dragon["rarity"]
        cheap_rarity = cheap["rarity"]
        if dragon_rarity == cheap_rarity:
            log.warning("dragon_log: dragon and cheap share rarity %s, skipping", dragon_rarity)
            return

        loot_pool = {
            "by_rarity": {
                dragon_rarity: [int(dragon["id"])],
                cheap_rarity:  [int(cheap["id"])],
            },
            "rarity_weights": {
                dragon_rarity: 0.30,
                cheap_rarity:  0.70,
            },
        }

        existing = await conn.fetchrow(
            "select id, loot_pool, price from economy_cases where key = $1",
            DRAGON_LOG_KEY,
        )
        description = (
            f"30% — AWP Dragon Lore (база {int(dragon['base_price']):,} ⚙). "
            f"70% — мусор (база {int(cheap['base_price']):,} ⚙). "
            "За 49 999 — рулетка элиты."
        ).replace(",", " ")

        if existing is None:
            await conn.execute(
                """
                insert into economy_cases (key, name, description, price, image_url, loot_pool, stat_trak_chance)
                values ($1, $2, $3, $4, $5, $6::jsonb, $7)
                """,
                DRAGON_LOG_KEY,
                "🐲 Dragon Lore",
                description,
                DRAGON_LOG_PRICE,
                DRAGON_LOG_IMAGE,
                json.dumps(loot_pool),
                0.05,
            )
            log.info("dragon_log: created (dragon=%d, cheap=%d)",
                     int(dragon["id"]), int(cheap["id"]))
        else:
            cur_pool = existing["loot_pool"]
            if isinstance(cur_pool, str):
                cur_pool = json.loads(cur_pool)
            if cur_pool == loot_pool and int(existing["price"]) == DRAGON_LOG_PRICE:
                return
            await conn.execute(
                """
                update economy_cases set
                    name = $2, description = $3, price = $4, image_url = $5,
                    loot_pool = $6::jsonb, stat_trak_chance = $7
                where id = $1
                """,
                int(existing["id"]),
                "🐲 Dragon Lore",
                description,
                DRAGON_LOG_PRICE,
                DRAGON_LOG_IMAGE,
                json.dumps(loot_pool),
                0.05,
            )
            log.info("dragon_log: updated")
