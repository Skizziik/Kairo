"""Gear system — equipment bought for coins, equipped into 7 slots, providing
passive buffs to Forge + casino mechanics. Survives prestige resets.

Slots: helmet, armor, boots, gloves, ring, amulet, drone (7 total, 1 item each).
Rarities: common, uncommon, rare, epic, legendary, mythic (+their CS-style colors).
Catalog size: 42 items (7 slots × 6 rarities).

Affix effect types (all percentages unless noted):
  dmg             +% click damage
  particles       +% particle reward per weapon break
  crit            +% flat crit chance
  crit_dmg        +% crit multiplier (on top of base x3)
  afk             +% AFK bot rate
  tier_luck       +% flat tier luck
  st_hunter       +% flat ST spawn chance
  coin_gain       +% coins when exchanging particles → coins
  sell_bonus      +% coins when selling skins from inventory
  case_discount   -% off case buy price
  afk_cap         +% daily AFK particle cap
  offline_hours   +N flat hours of offline cap (absolute, not %)

Equipped-affix sum is denormalized onto forge_users.gear_affixes (JSONB) so
hit/tick paths don't need extra queries.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app.db.client import pool

log = logging.getLogger(__name__)


# ============================================================
# RARITY
# ============================================================

RARITY_ORDER = ["common", "uncommon", "rare", "epic", "legendary", "mythic", "ultralegendary"]
RARITY_LABEL_RU = {
    "common": "Обычный",
    "uncommon": "Необычный",
    "rare": "Редкий",
    "epic": "Эпический",
    "legendary": "Легендарный",
    "mythic": "Мифический",
    "ultralegendary": "УЛЬТРАЛЕГЕНДАРНЫЙ",
}
RARITY_COLOR = {
    "common":         "#b0c3d9",
    "uncommon":       "#5e98d9",
    "rare":           "#4b69ff",
    "epic":           "#8847ff",
    "legendary":      "#d32ce6",
    "mythic":         "#eb4b4b",
    "ultralegendary": "#ffd700",  # gold
}
RARITY_MULT = {"common": 1, "uncommon": 2, "rare": 4, "epic": 10, "legendary": 25, "mythic": 75, "ultralegendary": 200}

SLOT_ORDER = ["helmet", "armor", "boots", "gloves", "ring", "amulet", "drone"]
SLOT_LABEL_RU = {
    "helmet": "🪖 Шлем",
    "armor":  "🛡 Броня",
    "boots":  "🥾 Ботинки",
    "gloves": "🧤 Перчатки",
    "ring":   "💍 Кольцо",
    "amulet": "🧿 Амулет",
    "drone":  "🛸 Дрон",
}

# Base price per slot at common rarity (coins)
SLOT_BASE_PRICE = {
    "helmet": 500,
    "armor":  800,
    "boots":  300,
    "gloves": 300,
    "ring":   400,
    "amulet": 600,
    "drone":  1000,
}

SELL_FRACTION = 0.5  # resale of gear = 50% of buy price


def _price(slot: str, rarity: str) -> int:
    return SLOT_BASE_PRICE[slot] * RARITY_MULT[rarity]

def _sell_price(slot: str, rarity: str) -> int:
    return int(_price(slot, rarity) * SELL_FRACTION)


# ============================================================
# CATALOG — 42 items
# ============================================================
# Each item: key, name, slot, rarity, icon (emoji), affixes dict.

def _mk(key: str, name: str, slot: str, rarity: str, icon: str, price_override: int | None = None, **affixes) -> dict:
    price = price_override if price_override is not None else _price(slot, rarity)
    return {
        "key": key,
        "name": name,
        "slot": slot,
        "rarity": rarity,
        "rarity_label": RARITY_LABEL_RU[rarity],
        "rarity_color": RARITY_COLOR[rarity],
        "icon": icon,
        "price": price,
        "sell_price": int(price * SELL_FRACTION),
        "affixes": affixes,
    }


CATALOG: dict[str, dict] = {}

def _add(item: dict) -> None:
    CATALOG[item["key"]] = item


# --- 🪖 HELMETS (targeting → crit + tier_luck + st_hunter) ---
_add(_mk("helmet_cheap_cap",      "Дешёвая каска",        "helmet", "common",    "⛑️", crit=3))
_add(_mk("helmet_kevlar",         "Кевларовый шлем",      "helmet", "uncommon",  "🪖", crit=5, tier_luck=2))
_add(_mk("helmet_swat",           "Тактический шлем SWAT","helmet", "rare",      "🎩", crit=8, tier_luck=3))
_add(_mk("helmet_phoenix",        "Феникс Скалл",         "helmet", "epic",      "💀", crit=10, tier_luck=5, st_hunter=2))
_add(_mk("helmet_inquisitor",     "Чёрный Инквизитор",    "helmet", "legendary", "🎭", crit=15, tier_luck=8, st_hunter=4))
_add(_mk("helmet_kairo_crown",    "Корона RIP",           "helmet", "mythic",    "👑", crit=20, tier_luck=12, st_hunter=7))

# --- 🛡 ARMORS (durability → particles + afk_cap + offline_hours) ---
_add(_mk("armor_telogreyka",      "Ватник",               "armor",  "common",    "🧥", particles=3))
_add(_mk("armor_kevlar_vest",     "Кевлар-жилет",         "armor",  "uncommon",  "🦺", particles=5, afk_cap=2))
_add(_mk("armor_tactical",        "Тактический жилет",    "armor",  "rare",      "🛡️", particles=8, afk_cap=4))
_add(_mk("armor_exo",             "Экзо-скелет",          "armor",  "epic",      "🤖", particles=12, afk_cap=6, offline_hours=1))
_add(_mk("armor_dragon",          "Драконий доспех",      "armor",  "legendary", "🐲", particles=17, afk_cap=10, offline_hours=2))
_add(_mk("armor_aegis",           "Эгида",                "armor",  "mythic",    "🏛️", particles=25, afk_cap=15, offline_hours=4))

# --- 🥾 BOOTS (speed → afk + afk_cap) ---
_add(_mk("boots_krossy",          "Старые кроссы",        "boots",  "common",    "👟", afk=3))
_add(_mk("boots_tactical",        "Тактические ботинки",  "boots",  "uncommon",  "🥾", afk=5))
_add(_mk("boots_ranger",          "Лёгкие рейнджера",     "boots",  "rare",      "🥿", afk=8, afk_cap=2))
_add(_mk("boots_rocket",          "Сапоги-ракеты",        "boots",  "epic",      "🚀", afk=12, afk_cap=4))
_add(_mk("boots_mercury",         "Меркурианские",        "boots",  "legendary", "⚡", afk=16, afk_cap=7))
_add(_mk("boots_hermes",          "Крылатые сандалии",    "boots",  "mythic",    "🪽", afk=22, afk_cap=10, offline_hours=1))

# --- 🧤 GLOVES (hands → dmg + crit_dmg) ---
_add(_mk("gloves_work",           "Рабочие перчатки",     "gloves", "common",    "🧤", dmg=3))
_add(_mk("gloves_biker",          "Байкерские",           "gloves", "uncommon",  "🏍️", dmg=5))
_add(_mk("gloves_sniper",         "Снайперские тактич.",  "gloves", "rare",      "🎯", dmg=8, crit_dmg=2))
_add(_mk("gloves_exo",             "Экзо-перчатки",        "gloves", "epic",      "🦾", dmg=12, crit_dmg=4))
_add(_mk("gloves_bloody",         "Кровавые перчатки",    "gloves", "legendary", "🩸", dmg=16, crit_dmg=7))
_add(_mk("gloves_mjolnir",        "Руки Молота",          "gloves", "mythic",    "⚒️", dmg=22, crit_dmg=10))

# 🔨 ULTRALEGENDARY ARTIFACT — МОЛОТ ИГОРЯ (instant-break Forge weapons + +1000% boss damage)
_add(_mk("gloves_igor_hammer",    "🔨 МОЛОТ ИГОРЯ",       "gloves", "ultralegendary", "🔨",
         price_override=5_000_000, instant_break=1, boss_dmg=1000))

# ============================================================
# 🛡 BOSS-HUNTER GEAR — special items for boss raids
# Each slot has 1-2 boss-themed mythic items, plus 1 ultralegendary
# ============================================================

# --- 🪖 HELMETS ---
_add(_mk("helmet_avenger",        "🪖 Шлем Мстителя",     "helmet", "mythic", "🦾",
         price_override=180_000, boss_dmg=50, crit=8, tier_luck=5))
_add(_mk("helmet_dragon_crown",   "👑 Корона Дракона",    "helmet", "ultralegendary", "🐉",
         price_override=2_500_000, boss_dmg=120, boss_crit=25, crit=15, tier_luck=10))

# --- 🛡 ARMORS ---
_add(_mk("armor_paladin",         "🛡 Латы Паладина",     "armor",  "mythic", "⚔️",
         price_override=220_000, boss_dmg=45, particles=15, afk_cap=10))
_add(_mk("armor_voidplate",       "🌌 Доспех Бездны",     "armor",  "ultralegendary", "🌑",
         price_override=3_200_000, boss_dmg=150, particles=25, afk_cap=20, offline_hours=3))

# --- 🥾 BOOTS ---
_add(_mk("boots_hunter",          "🥾 Сапоги Охотника",   "boots",  "mythic", "🦶",
         price_override=120_000, boss_dmg=35, afk=15, afk_cap=8))
_add(_mk("boots_zeus",            "⚡ Сандалии Зевса",     "boots",  "ultralegendary", "⚡",
         price_override=2_000_000, boss_dmg=100, afk=30, afk_cap=15, offline_hours=2))

# --- 🧤 GLOVES ---
_add(_mk("gloves_witch",          "🧤 Перчатки Колдуна",  "gloves", "mythic", "🧙",
         price_override=200_000, boss_dmg=60, boss_crit=15, dmg=15))
_add(_mk("gloves_beast_claws",    "🐺 Когти Зверя",       "gloves", "mythic", "🦴",
         price_override=350_000, boss_dmg=80, dmg=20, crit_dmg=8))

# --- 💍 RINGS ---
_add(_mk("ring_hunter_seal",      "💍 Печать Охотника",   "ring",   "mythic", "🦅",
         price_override=150_000, boss_dmg=40, coin_gain=10, sell_bonus=5))
_add(_mk("ring_kairo_eye",        "👁 Око Кайро",         "ring",   "ultralegendary", "👁",
         price_override=2_800_000, boss_dmg=130, boss_crit=20, tier_luck=15, coin_gain=20))

# --- 🧿 AMULETS ---
_add(_mk("amulet_rage",           "🔥 Амулет Ярости",     "amulet", "mythic", "🔥",
         price_override=280_000, boss_dmg=55, boss_crit=18, particles=10))
_add(_mk("amulet_lifesteal",      "🩸 Кулон Жажды",        "amulet", "ultralegendary", "🩸",
         price_override=2_400_000, boss_dmg=110, boss_crit=20, particles=20, st_hunter=5))

# --- 🛸 DRONES ---
_add(_mk("drone_killer",          "🛸 Дрон-Убийца",       "drone",  "mythic", "💀",
         price_override=250_000, boss_dmg=50, afk=12, afk_cap=10))
_add(_mk("drone_artillery",       "💣 Дрон-Артиллерия",   "drone",  "ultralegendary", "🎯",
         price_override=2_700_000, boss_dmg=140, afk=30, afk_cap=18, offline_hours=2))

# --- 💍 RINGS (magic → tier_luck + coin_gain + sell_bonus) ---
_add(_mk("ring_luck",             "Кольцо удачи",         "ring",   "common",    "💍", tier_luck=2))
_add(_mk("ring_silver",           "Серебряное кольцо",    "ring",   "uncommon",  "⚪", tier_luck=4, coin_gain=3))
_add(_mk("ring_trader",           "Кольцо торгаша",       "ring",   "rare",      "💎", tier_luck=6, coin_gain=6, sell_bonus=3))
_add(_mk("ring_roulette",         "Кольцо рулетки",       "ring",   "epic",      "🎰", tier_luck=8, coin_gain=10, sell_bonus=5))
_add(_mk("ring_midas",            "Кольцо Мидаса",        "ring",   "legendary", "🌟", tier_luck=10, coin_gain=15, sell_bonus=8))
_add(_mk("ring_singularity",      "Сингулярность",        "ring",   "mythic",    "⚛️", tier_luck=15, coin_gain=20, sell_bonus=12))

# --- 🧿 AMULETS (spirit → particles + st_hunter + case_discount) ---
_add(_mk("amulet_beads",          "Чётки",                "amulet", "common",    "📿", particles=3))
_add(_mk("amulet_oberegh",        "Оберег",               "amulet", "uncommon",  "🧿", particles=5, st_hunter=2))
_add(_mk("amulet_guardian",       "Амулет стража",        "amulet", "rare",      "⚜️", particles=7, st_hunter=4))
_add(_mk("amulet_void",           "Талисман пустоты",     "amulet", "epic",      "🌀", particles=10, st_hunter=6, case_discount=2))
_add(_mk("amulet_demon_heart",    "Сердце демона",        "amulet", "legendary", "❤️‍🔥", particles=15, st_hunter=8, case_discount=4))
_add(_mk("amulet_kairo_soul",     "Душа Кайро",           "amulet", "mythic",    "💠", particles=20, st_hunter=12, case_discount=7))

# --- 🛸 DRONES (companion → afk + afk_cap + offline_hours) ---
_add(_mk("drone_scout",           "Дрон-разведчик",       "drone",  "common",    "🛰️", afk=2))
_add(_mk("drone_mk1",             "Боевой дрон Mk1",      "drone",  "uncommon",  "🚁", afk=4, afk_cap=3))
_add(_mk("drone_sniper",          "Снайперский дрон",     "drone",  "rare",      "🎯", afk=6, afk_cap=6))
_add(_mk("drone_autonomous",      "Автономный дрон",      "drone",  "epic",      "🛸", afk=9, afk_cap=10, offline_hours=1))
_add(_mk("drone_swarm",           "Рой-дрон",             "drone",  "legendary", "🌪️", afk=12, afk_cap=15, offline_hours=2))
_add(_mk("drone_titan",           "Титан-дрон",           "drone",  "mythic",    "🛩️", afk=18, afk_cap=20, offline_hours=4))


# ============================================================
# SCHEMA BOOTSTRAP
# ============================================================

async def ensure_schema() -> None:
    sql_path = Path(__file__).parent.parent / "db" / "migration_gear.sql"
    if not sql_path.exists():
        log.warning("gear migration SQL missing: %s", sql_path)
        return
    sql = sql_path.read_text(encoding="utf-8")
    async with pool().acquire() as conn:
        await conn.execute(sql)
    log.info("gear schema ensured")


# ============================================================
# AFFIX ACCUMULATION
# ============================================================

def _sum_affixes(items: list[dict]) -> dict:
    totals: dict[str, float] = {}
    for it in items:
        for k, v in it.get("affixes", {}).items():
            totals[k] = totals.get(k, 0) + v
    return {k: round(v, 2) for k, v in totals.items()}


async def recalc_equipped_affixes(tg_id: int, conn=None) -> dict:
    """Pull currently equipped items, sum their affixes, denormalize the result
    onto forge_users.gear_affixes. Called whenever equip/unequip/buy-auto-equip/sell
    mutates equipment. Returns the final affix totals."""
    async def _work(c):
        rows = await c.fetch(
            "select item_key from gear_inventory where tg_id = $1 and equipped",
            tg_id,
        )
        owned_equipped = [CATALOG[r["item_key"]] for r in rows if r["item_key"] in CATALOG]
        totals = _sum_affixes(owned_equipped)
        await c.execute(
            "update forge_users set gear_affixes = $2::jsonb where tg_id = $1",
            tg_id, json.dumps(totals),
        )
        return totals
    if conn is not None:
        return await _work(conn)
    async with pool().acquire() as c:
        return await _work(c)


async def get_affixes(tg_id: int) -> dict:
    """Fast-read equipped affix totals from the denormalized column."""
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "select gear_affixes from forge_users where tg_id = $1",
            tg_id,
        )
    if row is None or row["gear_affixes"] is None:
        return {}
    val = row["gear_affixes"]
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except Exception:
            return {}
    return dict(val)


def affix_as_list(item: dict) -> list[tuple[str, float]]:
    """Sorted list of (affix_key, value) for display."""
    return sorted(item["affixes"].items())


# ============================================================
# SHOP / INVENTORY
# ============================================================

AFFIX_LABEL = {
    "dmg":           ("+{v}% ⚒ урон", "dmg"),
    "particles":     ("+{v}% ⚙ particles", "particles"),
    "crit":          ("+{v}% 🎯 крит", "crit"),
    "crit_dmg":      ("+{v}% 💥 сила крита", "crit_dmg"),
    "afk":           ("+{v}% 🤖 AFK", "afk"),
    "tier_luck":     ("+{v}% 🔮 tier-luck", "tier_luck"),
    "st_hunter":     ("+{v}% 🏷 ST-шанс", "st_hunter"),
    "coin_gain":     ("+{v}% 💰 коины за обмен", "coin_gain"),
    "sell_bonus":    ("+{v}% 💵 цена продажи скинов", "sell_bonus"),
    "case_discount": ("-{v}% 🎁 цена кейсов", "case_discount"),
    "afk_cap":       ("+{v}% 📦 AFK-cap", "afk_cap"),
    "offline_hours": ("+{v}ч ⏰ offline", "offline_hours"),
    "instant_break": ("💥 МОМЕНТАЛЬНЫЙ РАЗЛОМ оружия", "instant_break"),
    "boss_dmg":      ("+{v}% 🛡 урон по боссам", "boss_dmg"),
    "boss_crit":     ("+{v}% 🎯 крит по боссам", "boss_crit"),
}


def _display_item(it: dict) -> dict:
    """UI-friendly representation."""
    affix_list = []
    for k, v in sorted(it["affixes"].items()):
        label_tpl, _ = AFFIX_LABEL.get(k, ("+{v} " + k, k))
        affix_list.append({"key": k, "value": v, "label": label_tpl.format(v=v)})
    return {
        "key": it["key"],
        "name": it["name"],
        "slot": it["slot"],
        "slot_label": SLOT_LABEL_RU[it["slot"]],
        "rarity": it["rarity"],
        "rarity_label": it["rarity_label"],
        "rarity_color": it["rarity_color"],
        "icon": it["icon"],
        "price": it["price"],
        "sell_price": it["sell_price"],
        "affixes": affix_list,
    }


async def get_shop(tg_id: int) -> dict:
    """Return catalog grouped by slot, each item decorated with 'owned' flag."""
    async with pool().acquire() as conn:
        owned_rows = await conn.fetch(
            "select distinct item_key from gear_inventory where tg_id = $1",
            tg_id,
        )
    owned_keys = {r["item_key"] for r in owned_rows}

    shop_by_slot: dict[str, list[dict]] = {s: [] for s in SLOT_ORDER}
    for it in CATALOG.values():
        disp = _display_item(it)
        disp["owned"] = it["key"] in owned_keys
        shop_by_slot[it["slot"]].append(disp)

    # Sort items within slot by rarity order ascending (common first, mythic last)
    for slot_key in shop_by_slot:
        shop_by_slot[slot_key].sort(key=lambda x: RARITY_ORDER.index(x["rarity"]))

    return {
        "slots": [
            {"key": s, "label": SLOT_LABEL_RU[s], "items": shop_by_slot[s]}
            for s in SLOT_ORDER
        ],
    }


async def get_inventory(tg_id: int) -> dict:
    """Owned items + current equipped set."""
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "select id, item_key, slot, equipped, acquired_at from gear_inventory "
            "where tg_id = $1 order by acquired_at desc",
            tg_id,
        )
    items = []
    equipped_by_slot: dict[str, dict] = {}
    for r in rows:
        if r["item_key"] not in CATALOG:
            continue
        cat = CATALOG[r["item_key"]]
        disp = _display_item(cat)
        disp["inv_id"] = int(r["id"])
        disp["equipped"] = bool(r["equipped"])
        items.append(disp)
        if r["equipped"]:
            equipped_by_slot[r["slot"]] = disp

    equipped_arr = []
    for s in SLOT_ORDER:
        equipped_arr.append({
            "slot": s,
            "label": SLOT_LABEL_RU[s],
            "item": equipped_by_slot.get(s),
        })

    affixes_totals = await get_affixes(tg_id)

    return {
        "items": items,
        "equipped": equipped_arr,
        "affix_totals": [
            {"key": k, "value": v, "label": AFFIX_LABEL.get(k, ("+{v} " + k, k))[0].format(v=v)}
            for k, v in sorted(affixes_totals.items())
        ],
    }


# ============================================================
# ACTIONS
# ============================================================

async def buy(tg_id: int, item_key: str) -> dict:
    if item_key not in CATALOG:
        return {"ok": False, "error": "Нет такого предмета"}
    item = CATALOG[item_key]
    # Apply case_discount on GEAR? No, gear buys pay full price — discount is for CASES.
    price = item["price"]

    async with pool().acquire() as conn:
        async with conn.transaction():
            u = await conn.fetchrow(
                "select balance from economy_users where tg_id = $1 for update",
                tg_id,
            )
            if u is None or int(u["balance"]) < price:
                return {"ok": False, "error": "Недостаточно монет", "price": price}
            await conn.execute(
                "update economy_users set balance = balance - $2, total_spent = total_spent + $2 "
                "where tg_id = $1",
                tg_id, price,
            )
            await conn.execute(
                "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                "values ($1, $2, 'gear_buy', $3, $4)",
                tg_id, -price, f"{item['name']} ({item['rarity_label']})",
                int(u["balance"]) - price,
            )
            # Insert item, auto-equip if that slot is currently empty
            has_equipped = await conn.fetchval(
                "select 1 from gear_inventory where tg_id = $1 and slot = $2 and equipped",
                tg_id, item["slot"],
            )
            equip = not has_equipped
            await conn.execute(
                "insert into gear_inventory (tg_id, item_key, slot, equipped) values ($1, $2, $3, $4)",
                tg_id, item_key, item["slot"], equip,
            )
            if equip:
                # Recalc affixes inside the same txn
                await recalc_equipped_affixes(tg_id, conn=conn)

    return {
        "ok": True,
        "item_key": item_key,
        "auto_equipped": equip,
        "new_balance": int(u["balance"]) - price,
    }


async def equip(tg_id: int, inv_id: int) -> dict:
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select slot, equipped from gear_inventory where id = $1 and tg_id = $2",
                inv_id, tg_id,
            )
            if row is None:
                return {"ok": False, "error": "Нет предмета"}
            if row["equipped"]:
                return {"ok": False, "error": "Уже надет"}
            # Unequip whatever is in that slot
            await conn.execute(
                "update gear_inventory set equipped = false "
                "where tg_id = $1 and slot = $2 and equipped",
                tg_id, row["slot"],
            )
            await conn.execute(
                "update gear_inventory set equipped = true where id = $1", inv_id,
            )
            await recalc_equipped_affixes(tg_id, conn=conn)
    return {"ok": True}


async def unequip(tg_id: int, inv_id: int) -> dict:
    async with pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "update gear_inventory set equipped = false "
                "where id = $1 and tg_id = $2",
                inv_id, tg_id,
            )
            await recalc_equipped_affixes(tg_id, conn=conn)
    return {"ok": True}


async def sell(tg_id: int, inv_id: int) -> dict:
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select item_key, slot, equipped from gear_inventory "
                "where id = $1 and tg_id = $2",
                inv_id, tg_id,
            )
            if row is None:
                return {"ok": False, "error": "Нет предмета"}
            if row["item_key"] not in CATALOG:
                # Clean up orphan row
                await conn.execute("delete from gear_inventory where id = $1", inv_id)
                return {"ok": False, "error": "Сломанный предмет, удалён"}
            item = CATALOG[row["item_key"]]
            refund = item["sell_price"]
            await conn.execute("delete from gear_inventory where id = $1", inv_id)
            await conn.execute(
                "update economy_users set balance = balance + $2, total_earned = total_earned + $2 "
                "where tg_id = $1",
                tg_id, refund,
            )
            new_bal = await conn.fetchval(
                "select balance from economy_users where tg_id = $1", tg_id,
            )
            await conn.execute(
                "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                "values ($1, $2, 'gear_sell', $3, $4)",
                tg_id, refund, f"{item['name']} ({item['rarity_label']})", int(new_bal),
            )
            if row["equipped"]:
                await recalc_equipped_affixes(tg_id, conn=conn)
    return {"ok": True, "refund": refund, "new_balance": int(new_bal)}
