"""Populate economy_skins_catalog and economy_cases from CSGO-API.

Run once after deploy via /seed_economy admin command.
Safe to re-run — skips if catalog already populated.
"""
from __future__ import annotations

import logging
import random
from collections import defaultdict

import httpx

from app.db.client import pool

log = logging.getLogger(__name__)

CSGO_API_SKINS_URL = "https://raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api/en/skins.json"

# Rarity normalization — CSGO-API returns various names, we map to our canonical.
RARITY_MAP = {
    "Consumer Grade": "consumer",
    "Industrial Grade": "industrial",
    "Mil-Spec Grade": "mil-spec",
    "Restricted": "restricted",
    "Classified": "classified",
    "Covert": "covert",
    "Extraordinary": "exceedingly_rare",
    "Contraband": "exceedingly_rare",
}

RARITY_COLOR = {
    "consumer": "#B0C3D9",
    "industrial": "#5E98D9",
    "mil-spec": "#4B69FF",
    "restricted": "#8847FF",
    "classified": "#D32CE6",
    "covert": "#EB4B4B",
    "exceedingly_rare": "#E4AE39",
}

# Median price per tier in coins. Calibrated so standard-weighted case opens
# have ~50% expected return (classic house edge).
PRICE_ANCHOR = {
    "consumer": 8,
    "industrial": 25,
    "mil-spec": 50,
    "restricted": 250,
    "classified": 1200,
    "covert": 5500,
    "exceedingly_rare": 35000,
}

# Weapon categories for filtering case loot pools.
RIFLES = {"AK-47", "M4A4", "M4A1-S", "Galil AR", "FAMAS", "AUG", "SG 553"}
SNIPERS = {"AWP", "SSG 08", "SCAR-20", "G3SG1"}
PISTOLS = {"Desert Eagle", "USP-S", "P2000", "Glock-18", "CZ75-Auto", "Five-SeveN",
           "Tec-9", "P250", "R8 Revolver", "Dual Berettas"}
SMGS = {"MAC-10", "MP9", "MP7", "MP5-SD", "P90", "PP-Bizon", "UMP-45"}
HEAVY = {"Nova", "XM1014", "MAG-7", "Sawed-Off", "Negev", "M249"}
KNIVES_GLOVES_MARKER = "★"  # CSGO-API marks knives/gloves with star


async def _fetch_skins() -> list[dict]:
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as c:
        r = await c.get(CSGO_API_SKINS_URL)
        r.raise_for_status()
    return r.json()


def _rarity_of(skin: dict) -> str | None:
    r = (skin.get("rarity") or {}).get("name") or ""
    return RARITY_MAP.get(r)


def _is_vanilla(skin: dict) -> bool:
    # Vanilla knives have no pattern — skip, they have skin_name "Vanilla"
    return (skin.get("pattern") or {}).get("name") == "Vanilla"


def _is_knife_or_glove(skin: dict) -> bool:
    name = skin.get("name") or ""
    return name.startswith(KNIVES_GLOVES_MARKER) or "Knife" in name or "Gloves" in name


def _price_for(rarity: str, is_knife_glove: bool) -> int:
    anchor = PRICE_ANCHOR[rarity]
    if is_knife_glove:
        anchor = max(anchor, PRICE_ANCHOR["exceedingly_rare"])
    lo = int(anchor * 0.5)
    hi = int(anchor * 2.0)
    return random.randint(lo, hi)


async def _catalog_count() -> int:
    async with pool().acquire() as conn:
        return int(await conn.fetchval("select count(*) from economy_skins_catalog") or 0)


def _curate(all_skins: list[dict]) -> list[dict]:
    """Pick ~300 skins across rarities, favoring knowns and skipping vanilla/junk."""
    by_rarity: dict[str, list[dict]] = defaultdict(list)
    for s in all_skins:
        if _is_vanilla(s):
            continue
        if not (s.get("image") or "").startswith("https://"):
            continue
        r = _rarity_of(s)
        if r is None:
            continue
        if not s.get("weapon") or not (s["weapon"].get("name")):
            # knives/gloves have weapon too but some edge cases
            if not s.get("name"):
                continue
        by_rarity[r].append(s)

    # Pick counts per rarity (more for common, fewer for epic)
    caps = {
        "consumer": 30,
        "industrial": 40,
        "mil-spec": 60,
        "restricted": 60,
        "classified": 50,
        "covert": 40,
        "exceedingly_rare": 30,
    }
    out: list[dict] = []
    for rarity, cap in caps.items():
        pool_r = by_rarity.get(rarity, [])
        random.shuffle(pool_r)
        out.extend(pool_r[:cap])
    return out


async def _insert_catalog(skins: list[dict]) -> int:
    inserted = 0
    async with pool().acquire() as conn:
        async with conn.transaction():
            for s in skins:
                rarity = _rarity_of(s)
                if not rarity:
                    continue
                weapon = (s.get("weapon") or {}).get("name") or "Special"
                skin_pattern = (s.get("pattern") or {}).get("name") or s.get("name") or "Skin"
                full_name = s.get("name") or f"{weapon} | {skin_pattern}"
                is_kg = _is_knife_or_glove(s)
                category = "knife" if (" Knife" in (s.get("name") or "") or s.get("name", "").startswith(KNIVES_GLOVES_MARKER)) else ("gloves" if "Gloves" in (s.get("name") or "") else "weapon")
                wears = s.get("wears") or []
                min_float = min((w.get("min", 0.0) for w in wears), default=0.0)
                max_float = max((w.get("max", 1.0) for w in wears), default=1.0)
                key = (s.get("id") or full_name).lower().replace(" ", "_").replace("|", "").replace("-", "_")[:80]
                try:
                    await conn.execute(
                        """
                        insert into economy_skins_catalog
                          (key, weapon, skin_name, full_name, rarity, rarity_color, category,
                           min_float, max_float, image_url, base_price, stat_trak_available)
                        values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                        on conflict (key) do nothing
                        """,
                        key,
                        weapon,
                        skin_pattern,
                        full_name,
                        rarity,
                        RARITY_COLOR[rarity],
                        category,
                        float(min_float),
                        float(max_float),
                        s["image"],
                        _price_for(rarity, is_kg),
                        bool(s.get("stattrak", True)) and category != "gloves",
                    )
                    inserted += 1
                except Exception:
                    log.exception("insert failed for %s", full_name)
    return inserted


# Real CS2 case PNGs from Steam CDN — used as decorative box art for our custom cases.
CASE_IMAGE_REVOLUTION = "https://community.akamai.steamstatic.com/economy/image/i0CoZ81Ui0m-9KwlBY1L_18myuGuq1wfhWSaZgMttyVfPaERSR0Wqmu7LAocGJKz2lu_XsnXwtmkJjSU91dh8bj35VTqVBP4io_frnAVvfb6aqduc_TFVjTCxbx05OU4S3jilE9w4DzRnImtIy2Sa1JzDJEhRPlK7EcO4U8gfA"
CASE_IMAGE_DREAMS = "https://community.akamai.steamstatic.com/economy/image/i0CoZ81Ui0m-9KwlBY1L_18myuGuq1wfhWSaZgMttyVfPaERSR0Wqmu7LAocGJKz2lu_XsnXwtmkJjSU91dh8bj35VTqVBP4io_frnIV7Kb5OaU-JqfHDzXFle0u4LY8Gy_kkRgisGzcm4v4J3vDOAQmDMdyRvlK7EcmeCU3yw"
CASE_IMAGE_PRISMA2 = "https://community.akamai.steamstatic.com/economy/image/i0CoZ81Ui0m-9KwlBY1L_18myuGuq1wfhWSaZgMttyVfPaERSR0Wqmu7LAocGJKz2lu_XsnXwtmkJjSU91dh8bj35VTqVBP4io_fr3cV6vT9avBvefWWDDGTxbZ14rhsTX7qkE90sDiHwt2pdC-TblJ2DsB1QPlK7Ee9riHKAA"
CASE_IMAGE_SNAKEBITE = "https://community.akamai.steamstatic.com/economy/image/i0CoZ81Ui0m-9KwlBY1L_18myuGuq1wfhWSaZgMttyVfPaERSR0Wqmu7LAocGJKz2lu_XsnXwtmkJjSU91dh8bj35VTqVBP4io_fr3oVvvT4bfI4dvTLCGTCmLl16ec7TX_mk08k42iHwtqscy-WPVUmCZJ4R_lK7Ed8Q6OYtw"
CASE_IMAGE_KILOWATT = "https://community.akamai.steamstatic.com/economy/image/i0CoZ81Ui0m-9KwlBY1L_18myuGuq1wfhWSaZgMttyVfPaERSR0Wqmu7LAocGJKz2lu_XsnXwtmkJjSU91dh8bj35VTqVBP4io_frnEVvqf_a6VoIfGSXz7Hlbwg57QwSS_mxhl15jiGyN37c3_GZw91W8BwRflK7EfKsa2sfw"

# Case definitions — lookup items by weapon category at insert time.
CASE_DEFS = [
    {
        "key": "igor_king_of_mid",
        "name": "Игорь — Король Мида",
        "description": "Для королей мид-контроля: AK, M4 и прочие рабочие лошадки. Шанс выбить что-то крутое.",
        "price": 300,
        "weapon_set": RIFLES,
        "rarity_weights": {"mil-spec": 0.60, "restricted": 0.28, "classified": 0.09, "covert": 0.028, "exceedingly_rare": 0.002},
        "image_url": CASE_IMAGE_REVOLUTION,
    },
    {
        "key": "lera_golova",
        "name": "Лера голова",
        "description": "Ничего лишнего — только AWP и SSG для тех кто целит в голову.",
        "price": 750,
        "weapon_set": SNIPERS,
        "rarity_weights": {"mil-spec": 0.50, "restricted": 0.30, "classified": 0.13, "covert": 0.06, "exceedingly_rare": 0.01},
        "image_url": CASE_IMAGE_DREAMS,
    },
    {
        "key": "masha_yu_know",
        "name": "Маша, ю ноу?",
        "description": "Пистольники: Deagle, USP, Five-SeveN и прочие красавцы. Cheap entry, но с сюрпризами.",
        "price": 150,
        "weapon_set": PISTOLS,
        "rarity_weights": {"mil-spec": 0.70, "restricted": 0.22, "classified": 0.06, "covert": 0.018, "exceedingly_rare": 0.002},
        "image_url": CASE_IMAGE_PRISMA2,
    },
    {
        "key": "melkiy",
        "name": "Мелкий",
        "description": "SMG и дробаши — оружие для тех кто не любит думать. MAC-10, MP9, Nova и вся банда.",
        "price": 200,
        "weapon_set": SMGS | HEAVY,
        "rarity_weights": {"mil-spec": 0.65, "restricted": 0.25, "classified": 0.08, "covert": 0.018, "exceedingly_rare": 0.002},
        "image_url": CASE_IMAGE_SNAKEBITE,
    },
    {
        "key": "rip",
        "name": "RIP",
        "description": "Легенда для легенд. Только топ-рарности + шанс выбить нож. Цена кусается.",
        "price": 5000,
        "weapon_set": None,  # all weapons allowed
        "rarity_weights": {"restricted": 0.40, "classified": 0.35, "covert": 0.20, "exceedingly_rare": 0.05},
        "image_url": CASE_IMAGE_KILOWATT,
    },
]


async def _insert_cases() -> int:
    async with pool().acquire() as conn:
        # Prefetch catalog ids grouped by weapon + rarity
        rows = await conn.fetch(
            "select id, weapon, rarity, category from economy_skins_catalog where active"
        )
        by_weapon_rarity: dict[tuple[str, str], list[int]] = defaultdict(list)
        knife_glove_by_rarity: dict[str, list[int]] = defaultdict(list)
        for r in rows:
            by_weapon_rarity[(r["weapon"], r["rarity"])].append(r["id"])
            if r["category"] in ("knife", "gloves"):
                knife_glove_by_rarity[r["rarity"]].append(r["id"])

        inserted = 0
        for case in CASE_DEFS:
            # Build pool per rarity
            pool_by_rarity: dict[str, list[int]] = {}
            if case["weapon_set"] is None:
                # All weapons, use every rarity from catalog
                for (weapon, rarity), ids in by_weapon_rarity.items():
                    pool_by_rarity.setdefault(rarity, []).extend(ids)
                # Add knives/gloves for exceedingly_rare
                for rarity, ids in knife_glove_by_rarity.items():
                    pool_by_rarity.setdefault("exceedingly_rare", []).extend(ids)
            else:
                for (weapon, rarity), ids in by_weapon_rarity.items():
                    if weapon in case["weapon_set"]:
                        pool_by_rarity.setdefault(rarity, []).extend(ids)
                # For each non-RIP case, add a few knives/gloves to exceedingly_rare
                for rarity, ids in knife_glove_by_rarity.items():
                    pool_by_rarity.setdefault("exceedingly_rare", []).extend(ids[:5])

            # Filter out empty rarity buckets
            rarity_weights = {k: v for k, v in case["rarity_weights"].items() if pool_by_rarity.get(k)}
            if not rarity_weights:
                log.warning("case %s has no matching items, skipping", case["key"])
                continue

            loot_pool = {
                "by_rarity": pool_by_rarity,
                "rarity_weights": rarity_weights,
            }
            import json as _json
            await conn.execute(
                """
                insert into economy_cases (key, name, description, price, image_url, loot_pool, stat_trak_chance)
                values ($1, $2, $3, $4, $5, $6::jsonb, $7)
                on conflict (key) do update set
                    name = excluded.name,
                    description = excluded.description,
                    price = excluded.price,
                    image_url = excluded.image_url,
                    loot_pool = excluded.loot_pool,
                    stat_trak_chance = excluded.stat_trak_chance
                """,
                case["key"],
                case["name"],
                case["description"],
                int(case["price"]),
                case.get("image_url"),
                _json.dumps(loot_pool),
                0.05,
            )
            inserted += 1
    return inserted


async def _reprice_catalog() -> int:
    """Recompute base_price for existing catalog entries to match current PRICE_ANCHOR."""
    updated = 0
    async with pool().acquire() as conn:
        rows = await conn.fetch("select id, rarity, category from economy_skins_catalog")
        for r in rows:
            rarity = r["rarity"]
            is_kg = r["category"] in ("knife", "gloves")
            new_price = _price_for(rarity, is_kg)
            await conn.execute(
                "update economy_skins_catalog set base_price = $2 where id = $1",
                int(r["id"]), new_price,
            )
            updated += 1
    return updated


async def run_seed(force: bool = False) -> dict:
    existing = await _catalog_count()
    if existing > 0 and not force:
        # Idempotent mode: keep skins but refresh case definitions (names/images/loot)
        # and rebalance catalog prices to current PRICE_ANCHOR.
        log.info("catalog already seeded (%d items); updating cases + reprice", existing)
        repriced = await _reprice_catalog()
        cases = await _insert_cases()
        return {"status": "refreshed", "catalog_size": existing, "cases": cases, "repriced": repriced}
    log.info("fetching CSGO-API skins...")
    all_skins = await _fetch_skins()
    log.info("got %d skins from api", len(all_skins))
    picked = _curate(all_skins)
    log.info("curated to %d skins", len(picked))
    inserted = await _insert_catalog(picked)
    cases = await _insert_cases()
    return {"status": "seeded", "catalog_size": inserted, "cases": cases}
