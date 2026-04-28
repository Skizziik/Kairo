"""Boss raids — endgame Forge content.

10 story bosses with scaling HP, then endless mode (HP × 1.5 per next tier).
Bosses persist HP between sessions — chip away over days. Each click attacks
the boss; damage scales from your forge upgrades, prestige, and gear's
boss_dmg affix. First-time kill of a tier unlocks the next, every kill grants
coins + trophies.
"""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db.client import pool
from app.economy import prestige as _prestige

log = logging.getLogger(__name__)


# ============================================================
# 10 STORY BOSSES + ENDLESS
# ============================================================

# Each boss: (tier, name, lore, icon, hp, coin_reward_base)
BOSSES: list[dict] = [
    {"tier": 1,  "name": "Карманный Лудоман",   "icon": "🪙", "hp": 50_000,        "coin_reward": 500,    "lore": "Кидает все коины в crash и orgазмирует на x1.05"},
    {"tier": 2,  "name": "Игорёк-АФК-Дед",       "icon": "💤", "hp": 200_000,       "coin_reward": 1_500,  "lore": "Стоит в спавне, ловит фраги ради экспы"},
    {"tier": 3,  "name": "Кейс-Маньяк",          "icon": "🎁", "hp": 700_000,       "coin_reward": 5_000,  "lore": "Выбил Glock | Сухая Пустыня и плачет"},
    {"tier": 4,  "name": "Ножевой Задрот",       "icon": "🔪", "hp": 2_500_000,     "coin_reward": 15_000, "lore": "Каждое прокачивание оружия — нервный тик"},
    {"tier": 5,  "name": "Скам-Форсер",          "icon": "💸", "hp": 8_000_000,     "coin_reward": 50_000, "lore": "Покупает крафт, выкидывает к деду в дилерскую"},
    {"tier": 6,  "name": "Тильт-Машина",         "icon": "🔥", "hp": 25_000_000,    "coin_reward": 150_000, "lore": "Ливает с 12-12 с фрейзой 'все рандомы'"},
    {"tier": 7,  "name": "AFK-Демон",            "icon": "🤖", "hp": 80_000_000,    "coin_reward": 400_000, "lore": "Бот, который не спит и не ест"},
    {"tier": 8,  "name": "Прокрастинатор",       "icon": "⏰", "hp": 250_000_000,   "coin_reward": 1_000_000, "lore": "Завтра точно начнёт качать прицел"},
    {"tier": 9,  "name": "Глобал-Эло",           "icon": "🏆", "hp": 1_000_000_000, "coin_reward": 3_000_000, "lore": "AWP в каждой руке, пулька в каждом таргете"},
    {"tier": 10, "name": "👑 Кайро-Финал",       "icon": "👑", "hp": 5_000_000_000, "coin_reward": 12_000_000, "lore": "Сам Кайро. Хохочет в твою тильт-сессию"},
    {"tier": 11, "name": "Главный Босс RIP",     "icon": "👑", "hp": 10_000_000_000, "coin_reward": 25_000_000, "image_url": "/img/rip-boss.png", "is_hero": True, "badge_on_kill": "rip_crown", "lore": "Сам RIP. Корона, очки, керамбит, M9 — кликер CS2. Жми по картинке, пока не сдохнет."},
]


# ============================================================
# BADGES — permanent flair shown next to nickname in leaderboard
# ============================================================

BADGES: dict[str, dict] = {
    "rip_crown": {
        "key":    "rip_crown",
        "name":   "Корона RIP",
        "icon":   "👑",
        "desc":   "Завалил Главного Босса (t11) — мифическая редкость",
        "rarity": "mythic",
    },
}


def badge_info(key: str) -> dict | None:
    return BADGES.get(key)


BOSS_REGEN_BASE_SEC = 30   # Base regen timeout (T1 = 30s without tap = HP resets)
BOSS_REGEN_PER_TIER = 4    # +4s per tier (T10 = 30 + 36 = 66s)


def boss_regen_seconds(tier: int) -> int:
    """How many idle seconds before this boss regens to full HP."""
    return BOSS_REGEN_BASE_SEC + max(0, tier - 1) * BOSS_REGEN_PER_TIER


# Per-tier kill cooldown (in seconds). After killing a boss, the player can't
# attack THAT tier again until cooldown expires. Stops autoclicker farming.
BOSS_KILL_COOLDOWN: dict[int, int] = {
    1:  60,         # 1 min
    2:  180,        # 3 min
    3:  600,        # 10 min
    4:  1_800,      # 30 min
    5:  3_600,      # 1 hour
    6:  7_200,      # 2 hours
    7:  14_400,     # 4 hours
    8:  28_800,     # 8 hours
    9:  57_600,     # 16 hours
    10: 86_400,     # 24 hours
    11: 172_800,    # 48 hours — final boss, slow respawn
}
BOSS_KILL_COOLDOWN_ENDLESS = 86_400  # 24h for any tier above 11


def boss_kill_cooldown_seconds(tier: int) -> int:
    return BOSS_KILL_COOLDOWN.get(tier, BOSS_KILL_COOLDOWN_ENDLESS)


def boss_for_tier(tier: int) -> dict:
    """Return boss config for any tier. Tiers 1-11 are story; >11 is endless mode.
    Endless scaling references Кайро-Финал (BOSSES[9]), not RIP, so it doesn't
    explode from the trillion-tier doubling."""
    if 1 <= tier <= 11:
        return BOSSES[tier - 1]
    base = BOSSES[9]   # scale from t10 = Кайро-Финал
    levels_above = tier - 11
    hp = int(base["hp"] * (1.5 ** levels_above))
    coin = int(base["coin_reward"] * (1.4 ** levels_above))
    return {
        "tier": tier,
        "name": f"♾ Endless #{levels_above}",
        "icon": "♾",
        "hp": hp,
        "coin_reward": coin,
        "lore": f"Бесконечный режим. Тир {levels_above} после RIP.",
    }


# ============================================================
# BOSS HUNTER — prestige branches (jeton-bought permanent bonuses)
# ============================================================

# These are exposed via _prestige module (we register them there for unified UI).
BOSS_HUNTER_BRANCHES = {
    "boss_dmg": {
        "name": "🛡 Сила охотника",
        "desc": "+10% урон по боссам за уровень",
        "max_level": 25,
        "cost_fn": lambda L: 1 + (L - 1) // 3,
        "effect_per_level": 0.10,
        "unit": "% boss-dmg",
    },
    "boss_crit": {
        "name": "🎯 Точка слабости",
        "desc": "+1% шанс крита по боссам",
        "max_level": 30,
        "cost_fn": lambda L: 1 + (L - 1) // 4,
        "effect_per_level": 1.0,
        "unit": "% boss-crit",
    },
    "boss_coin": {
        "name": "💰 Боевой трофей",
        "desc": "+5% коинов с убийства боссов",
        "max_level": 20,
        "cost_fn": lambda L: 1 + (L - 1) // 3,
        "effect_per_level": 0.05,
        "unit": "% coin reward",
    },
    "boss_double": {
        "name": "⚡ Двойной удар",
        "desc": "Шанс двойного урона за тап",
        "max_level": 15,
        "cost_fn": lambda L: 1 + (L - 1) // 2,
        "effect_per_level": 1.0,
        "unit": "% double-hit",
    },
    "boss_pierce": {
        "name": "🩸 Кровопускание",
        "desc": "Боссы спавнятся с -1% HP за уровень",
        "max_level": 10,
        "cost_fn": lambda L: 2 + (L - 1) // 2,
        "effect_per_level": 1.0,
        "unit": "% HP discount",
    },
    "boss_megahit": {
        "name": "💥 Мега-удар",
        "desc": "Каждый 25-й удар наносит ×10",
        "max_level": 10,
        "cost_fn": lambda L: 2 + (L - 1) // 2,
        "effect_per_level": 1.0,  # reduces interval: 25 - lvl (min 15)
        "unit": "удар",
    },
}

_BOSS_HUNTER_COL = {
    "boss_dmg":    "boss_dmg_lvl",
    "boss_crit":   "boss_crit_lvl",
    "boss_coin":   "boss_coin_lvl",
    "boss_double": "boss_double_lvl",
    "boss_pierce": "boss_pierce_lvl",
    "boss_megahit": "boss_megahit_lvl",
}


# ============================================================
# SCHEMA + STATE
# ============================================================

async def ensure_schema() -> None:
    sql_path = Path(__file__).parent.parent / "db" / "migration_bosses.sql"
    if not sql_path.exists():
        log.warning("boss migration SQL missing")
        return
    sql = sql_path.read_text(encoding="utf-8")
    async with pool().acquire() as conn:
        await conn.execute(sql)
    log.info("boss schema ensured")


def _parse_gear(raw) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw) or {}
        except Exception:
            return {}
    return {}


def _compute_max_hp(tier: int, pierce_lvl: int) -> int:
    boss = boss_for_tier(tier)
    pierce_pct = pierce_lvl * 1.0  # 1% per level
    pierce_pct = min(pierce_pct, 50)  # safety cap
    return int(boss["hp"] * (1 - pierce_pct / 100))


async def _get_or_init_tier_hp(conn, tg_id: int, tier: int, max_hp: int) -> tuple[int, datetime | None, datetime | None]:
    """Read persistent HP + last_attack + cooldown_until. Apply regen if idle.
    Returns (current_hp, last_attack_at, cooldown_until)."""
    row = await conn.fetchrow(
        "select current_hp, last_attack_at, cooldown_until from boss_progress where tg_id = $1 and tier = $2",
        tg_id, tier,
    )
    if row is None:
        await conn.execute(
            "insert into boss_progress (tg_id, tier, current_hp) values ($1, $2, $3) on conflict do nothing",
            tg_id, tier, max_hp,
        )
        return max_hp, None, None
    cur_hp = int(row["current_hp"])
    last = row["last_attack_at"]
    cd = row["cooldown_until"]
    if last is not None and cur_hp < max_hp:
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        regen_sec = boss_regen_seconds(tier)
        if elapsed >= regen_sec:
            cur_hp = max_hp
            await conn.execute(
                "update boss_progress set current_hp = $3, last_attack_at = null where tg_id = $1 and tier = $2",
                tg_id, tier, max_hp,
            )
            last = None
    return cur_hp, last, cd


async def get_state(tg_id: int) -> dict:
    """Return currently SELECTED boss state + list of all unlocked tiers for picker."""
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "select boss_selected_tier, boss_total_kills, boss_max_tier, boss_endless_kills, "
            "boss_dmg_lvl, boss_crit_lvl, boss_coin_lvl, boss_double_lvl, boss_pierce_lvl, boss_megahit_lvl, "
            "damage_level, crit_level, crit_power_level, luck_level, "
            "hammer_power_lvl, dust_magic_lvl, sharpen_lvl, "
            "gear_affixes, total_clicks, jetons "
            "from forge_users where tg_id = $1",
            tg_id,
        )
        if row is None:
            return {"unlocked": False}

        max_tier = int(row["boss_max_tier"] or 1)
        sel_tier = int(row["boss_selected_tier"] or 1)
        # Clamp selection to available range: 1..max_tier
        if sel_tier > max_tier:
            sel_tier = max_tier
            await conn.execute("update forge_users set boss_selected_tier = $2 where tg_id = $1", tg_id, sel_tier)

        pierce_lvl = int(row["boss_pierce_lvl"] or 0)
        boss = boss_for_tier(sel_tier)
        max_hp = _compute_max_hp(sel_tier, pierce_lvl)
        cur_hp, last_attack, cooldown_until = await _get_or_init_tier_hp(conn, tg_id, sel_tier, max_hp)
        if cur_hp <= 0 or cur_hp > max_hp:
            cur_hp = max_hp

        # Build unlocked tier list (for picker)
        progress_rows = await conn.fetch(
            "select tier, current_hp, kills, cooldown_until from boss_progress where tg_id = $1 and tier <= $2",
            tg_id, max_tier,
        )
        now_dt = datetime.now(timezone.utc)
        progress_map = {}
        for r in progress_rows:
            cd_left = 0
            if r["cooldown_until"] is not None:
                left = (r["cooldown_until"] - now_dt).total_seconds()
                if left > 0:
                    cd_left = int(left)
            progress_map[int(r["tier"])] = {
                "hp": int(r["current_hp"]),
                "kills": int(r["kills"]),
                "cooldown_left": cd_left,
            }

    # Regen countdown for selected tier
    regen_sec = boss_regen_seconds(sel_tier)
    seconds_until_regen = None
    if last_attack is not None and cur_hp < max_hp:
        elapsed = (datetime.now(timezone.utc) - last_attack).total_seconds()
        seconds_until_regen = max(0, int(regen_sec - elapsed))

    # Kill cooldown for selected tier
    cd_left_sec = 0
    if cooldown_until is not None:
        left = (cooldown_until - datetime.now(timezone.utc)).total_seconds()
        if left > 0:
            cd_left_sec = int(left)

    tiers_info = []
    for t in range(1, max_tier + 1):
        b = boss_for_tier(t)
        b_max = _compute_max_hp(t, pierce_lvl)
        p = progress_map.get(t, {"hp": b_max, "kills": 0, "cooldown_left": 0})
        tiers_info.append({
            "tier": t,
            "name": b["name"],
            "icon": b["icon"],
            "max_hp": b_max,
            "hp": p["hp"],
            "kills": p["kills"],
            "coin_reward": int(b["coin_reward"]),
            "selected": t == sel_tier,
            "cooldown_left": p.get("cooldown_left", 0),
            "cooldown_total": boss_kill_cooldown_seconds(t),
            "image_url": b.get("image_url"),
            "is_hero": bool(b.get("is_hero", False)),
        })

    dmg_per_hit = _preview_damage(row)
    return {
        "unlocked": True,
        "selected_tier": sel_tier,
        "max_tier": max_tier,
        "tier": sel_tier,
        "name": boss["name"],
        "icon": boss["icon"],
        "lore": boss["lore"],
        "image_url": boss.get("image_url"),
        "is_hero": bool(boss.get("is_hero", False)),
        "hp": cur_hp,
        "max_hp": max_hp,
        "coin_reward": int(boss["coin_reward"]),
        "total_kills": int(row["boss_total_kills"]),
        "endless_kills": int(row["boss_endless_kills"]),
        "preview_dmg": dmg_per_hit,
        "jetons": int(row["jetons"] or 0),
        "tiers": tiers_info,
        "regen_total_sec": regen_sec,
        "regen_seconds_left": seconds_until_regen,
        "cooldown_seconds_left": cd_left_sec,
        "cooldown_total_sec": boss_kill_cooldown_seconds(sel_tier),
        "boss_levels": {
            "boss_dmg":    int(row["boss_dmg_lvl"] or 0),
            "boss_crit":   int(row["boss_crit_lvl"] or 0),
            "boss_coin":   int(row["boss_coin_lvl"] or 0),
            "boss_double": int(row["boss_double_lvl"] or 0),
            "boss_pierce": int(row["boss_pierce_lvl"] or 0),
            "boss_megahit": int(row["boss_megahit_lvl"] or 0),
        },
    }


async def select_tier(tg_id: int, tier: int) -> dict:
    """Switch which boss the player is fighting. Must be ≤ max_tier_reached."""
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "select boss_max_tier from forge_users where tg_id = $1 for update", tg_id,
        )
        if row is None:
            return {"ok": False, "error": "No state"}
        max_tier = int(row["boss_max_tier"] or 1)
        if tier < 1 or tier > max_tier:
            return {"ok": False, "error": f"Tier {tier} ещё не открыт (макс {max_tier})"}
        await conn.execute("update forge_users set boss_selected_tier = $2 where tg_id = $1", tg_id, tier)
    return {"ok": True, "selected_tier": tier}


def _preview_damage(row) -> int:
    """Preview damage per click against boss (before crit/double/megahit randomness)."""
    from app.economy.forge import damage_at, crit_multiplier_at
    dmg_lvl = int(row["damage_level"])
    crit_power_lvl = int(row["crit_power_level"] or 0)
    base_dmg = damage_at(dmg_lvl)

    # Prestige
    hp_pow_mult = _prestige.hammer_power_mult(int(row["hammer_power_lvl"] or 0))
    boss_dmg_lvl = int(row["boss_dmg_lvl"] or 0)
    boss_dmg_mult = 1.0 + boss_dmg_lvl * 0.10  # 10% per level

    # Gear
    gear = _parse_gear(row["gear_affixes"])
    gear_dmg_mult = 1.0 + float(gear.get("dmg", 0)) / 100
    gear_boss_dmg_mult = 1.0 + float(gear.get("boss_dmg", 0)) / 100

    # Avg damage including crit
    crit_chance = 0.20  # rough avg
    crit_mult = crit_multiplier_at(crit_power_lvl)
    avg_dmg = base_dmg * (1 - crit_chance + crit_chance * crit_mult)

    final = avg_dmg * hp_pow_mult * boss_dmg_mult * gear_dmg_mult * gear_boss_dmg_mult
    return max(1, int(final))


# ============================================================
# ATTACK
# ============================================================

async def attack(tg_id: int, taps: int = 1) -> dict:
    """Tap-attacks against the SELECTED boss. Multiple kills allowed in one batch
    (e.g., overkill on weak boss when player has high damage)."""
    if taps <= 0 or taps > 50:
        return {"ok": False, "error": "Invalid taps"}

    from app.economy.forge import damage_at, crit_chance_at, crit_multiplier_at

    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select boss_selected_tier, boss_total_kills, boss_max_tier, boss_endless_kills, "
                "boss_dmg_lvl, boss_crit_lvl, boss_coin_lvl, boss_double_lvl, boss_pierce_lvl, boss_megahit_lvl, "
                "damage_level, crit_level, crit_power_level, "
                "hammer_power_lvl, sharpen_lvl, "
                "gear_affixes, total_clicks, boss_attack_count "
                "from forge_users where tg_id = $1 for update",
                tg_id,
            )
            if row is None:
                return {"ok": False, "error": "Forge not opened"}

            tier = int(row["boss_selected_tier"] or 1)
            pierce_lvl = int(row["boss_pierce_lvl"] or 0)
            max_hp = _compute_max_hp(tier, pierce_lvl)
            cur_hp, _last_attack, cd_until = await _get_or_init_tier_hp(conn, tg_id, tier, max_hp)
            if cur_hp > max_hp:
                cur_hp = max_hp
            now_ts = datetime.now(timezone.utc)

            # Cooldown check — boss is "asleep" after recent kill
            if cd_until is not None and cd_until > now_ts:
                left_sec = int((cd_until - now_ts).total_seconds())
                return {
                    "ok": False,
                    "error": "cooldown",
                    "cooldown_left": left_sec,
                    "boss_after": {
                        "tier": tier,
                        "hp": cur_hp,
                        "max_hp": max_hp,
                        "name": boss_for_tier(tier)["name"],
                        "icon": boss_for_tier(tier)["icon"],
                    },
                }

            # Damage components
            dmg_lvl = int(row["damage_level"])
            crit_lvl = int(row["crit_level"])
            crit_power_lvl = int(row["crit_power_level"] or 0)
            base_dmg = damage_at(dmg_lvl)

            hp_pow_mult = _prestige.hammer_power_mult(int(row["hammer_power_lvl"] or 0))
            sharpen_flat = _prestige.sharpen_flat_crit(int(row["sharpen_lvl"] or 0))
            boss_dmg_lvl = int(row["boss_dmg_lvl"] or 0)
            boss_crit_lvl = int(row["boss_crit_lvl"] or 0)
            boss_coin_lvl = int(row["boss_coin_lvl"] or 0)
            boss_double_lvl = int(row["boss_double_lvl"] or 0)
            boss_megahit_lvl = int(row["boss_megahit_lvl"] or 0)

            boss_dmg_mult = 1.0 + boss_dmg_lvl * 0.10
            boss_crit_pct = crit_chance_at(crit_lvl) + sharpen_flat + boss_crit_lvl
            double_pct = boss_double_lvl * 1.0
            megahit_interval = max(15, 25 - boss_megahit_lvl)

            gear = _parse_gear(row["gear_affixes"])
            gear_dmg_mult = 1.0 + float(gear.get("dmg", 0)) / 100
            gear_boss_dmg_mult = 1.0 + float(gear.get("boss_dmg", 0)) / 100
            gear_crit_dmg_mult = 1.0 + float(gear.get("crit_dmg", 0)) / 100
            crit_mult_base = crit_multiplier_at(crit_power_lvl) * gear_crit_dmg_mult

            # Boss-specific counter (NOT global total_clicks) — so forge grinding
            # doesn't pre-rotate the megahit cycle on this tab.
            boss_clicks = int(row["boss_attack_count"] or 0)
            kills = []
            crits = 0
            doubles = 0
            megahits = 0
            total_dmg = 0
            coin_reward_total = 0
            current_tier = tier  # we're fighting THIS tier; killing it doesn't auto-advance

            for i in range(taps):
                hit_idx = boss_clicks + i + 1
                is_crit = random.uniform(0, 100) < boss_crit_pct
                effective_crit = crit_mult_base if is_crit else 1.0
                hit_dmg = int(base_dmg * effective_crit * hp_pow_mult * boss_dmg_mult * gear_dmg_mult * gear_boss_dmg_mult)
                if is_crit:
                    crits += 1
                if double_pct > 0 and random.uniform(0, 100) < double_pct:
                    hit_dmg *= 2
                    doubles += 1
                if hit_idx % megahit_interval == 0 and boss_megahit_lvl > 0:
                    hit_dmg *= 10
                    megahits += 1

                cur_hp -= hit_dmg
                total_dmg += hit_dmg

                if cur_hp <= 0:
                    cur_boss = boss_for_tier(current_tier)
                    coin_pct = 1.0 + boss_coin_lvl * 0.05
                    coin_pay = int(cur_boss["coin_reward"] * coin_pct)
                    coin_reward_total += coin_pay
                    kills.append({
                        "tier": current_tier,
                        "name": cur_boss["name"],
                        "icon": cur_boss["icon"],
                        "coin_reward": coin_pay,
                    })
                    # Refill same tier (player can keep grinding, doesn't auto-advance)
                    cur_hp = max_hp

            # Persist HP + last_attack timestamp; if killed, set cooldown_until
            kill_cd_until = None
            if len(kills) > 0:
                cd_sec = boss_kill_cooldown_seconds(current_tier)
                kill_cd_until = now_ts + timedelta(seconds=cd_sec)
            await conn.execute(
                "update boss_progress set current_hp = $3, kills = kills + $4, "
                "last_attack_at = $5, cooldown_until = coalesce($6, cooldown_until) "
                "where tg_id = $1 and tier = $2",
                tg_id, current_tier, max(0, cur_hp), len(kills), now_ts, kill_cd_until,
            )

            # Track max_tier reached + auto-advance selection
            new_max_tier = int(row["boss_max_tier"] or 1)
            tier_unlocked = None
            if kills and current_tier >= new_max_tier:
                # Player just killed a boss at their highest known tier — unlock next
                new_max_tier = current_tier + 1
                tier_unlocked = new_max_tier
                # Init progress row for next tier
                next_max_hp = _compute_max_hp(new_max_tier, pierce_lvl)
                await conn.execute(
                    "insert into boss_progress (tg_id, tier, current_hp) values ($1, $2, $3) "
                    "on conflict do nothing",
                    tg_id, new_max_tier, next_max_hp,
                )

            new_total_kills = int(row["boss_total_kills"]) + len(kills)
            new_endless_kills = int(row["boss_endless_kills"]) + sum(1 for k in kills if k["tier"] > 11)

            await conn.execute(
                "update forge_users set "
                "  boss_total_kills = $2, "
                "  boss_max_tier = $3, "
                "  boss_endless_kills = $4, "
                "  total_clicks = total_clicks + $5, "
                "  boss_attack_count = boss_attack_count + $5 "
                "where tg_id = $1",
                tg_id, new_total_kills, new_max_tier, new_endless_kills, taps,
            )

            new_bal = None
            if coin_reward_total > 0:
                bal_row = await conn.fetchrow(
                    "update economy_users set balance = balance + $2, total_earned = total_earned + $2 "
                    "where tg_id = $1 returning balance",
                    tg_id, coin_reward_total,
                )
                new_bal = int(bal_row["balance"]) if bal_row else None
                await conn.execute(
                    "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                    "values ($1, $2, 'boss', $3, $4)",
                    tg_id, coin_reward_total,
                    f"boss_kill_x{len(kills)}_tier_{current_tier}",
                    new_bal,
                )

            # Award permanent badge if any killed boss has `badge_on_kill` set.
            # Idempotent: jsonb array, only added if absent. First kill grants it;
            # subsequent kills are no-op.
            new_badges_granted: list[str] = []
            for k in kills:
                cfg_b = boss_for_tier(int(k["tier"]))
                badge_key = cfg_b.get("badge_on_kill")
                if not badge_key:
                    continue
                # Check current badges and append only if missing
                cur_badges_row = await conn.fetchrow(
                    "select badges from economy_users where tg_id = $1", tg_id,
                )
                cur_list = []
                if cur_badges_row and cur_badges_row["badges"]:
                    raw = cur_badges_row["badges"]
                    cur_list = raw if isinstance(raw, list) else json.loads(raw)
                if badge_key in cur_list:
                    continue
                cur_list.append(badge_key)
                await conn.execute(
                    "update economy_users set badges = $2::jsonb where tg_id = $1",
                    tg_id, json.dumps(cur_list),
                )
                new_badges_granted.append(badge_key)

    # Tax accrual on boss-kill rewards (positive income)
    if coin_reward_total > 0:
        try:
            from app.economy import tax as _tax
            await _tax.accrue_tax(tg_id, coin_reward_total, "boss_kill")
        except Exception:
            pass

    badges_payload = [BADGES[k] for k in new_badges_granted if k in BADGES]
    return {
        "ok": True,
        "applied_taps": taps,
        "total_dmg": total_dmg,
        "crits": crits,
        "doubles": doubles,
        "megahits": megahits,
        "kills": kills,
        "boss_after": {
            "tier": current_tier,
            "hp": max(0, cur_hp),
            "max_hp": max_hp,
            "name": boss_for_tier(current_tier)["name"],
            "icon": boss_for_tier(current_tier)["icon"],
        },
        "tier_unlocked": tier_unlocked,
        "coin_reward": coin_reward_total,
        "new_balance": new_bal,
        "badges_unlocked": badges_payload,
        "regen_total_sec": boss_regen_seconds(current_tier),
        "cooldown_total_sec": boss_kill_cooldown_seconds(current_tier) if kills else 0,
        "cooldown_left_sec": int((kill_cd_until - now_ts).total_seconds()) if kill_cd_until else 0,
    }


# ============================================================
# BUY BOSS-HUNTER PRESTIGE UPGRADE
# ============================================================

async def buy_boss_upgrade(tg_id: int, branch: str) -> dict:
    if branch not in BOSS_HUNTER_BRANCHES:
        return {"ok": False, "error": "Unknown boss-hunter branch"}
    cfg = BOSS_HUNTER_BRANCHES[branch]
    col = _BOSS_HUNTER_COL[branch]
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                f"select jetons, {col} as lvl from forge_users where tg_id = $1 for update",
                tg_id,
            )
            if row is None:
                return {"ok": False, "error": "No state"}
            lvl = int(row["lvl"] or 0)
            if lvl >= cfg["max_level"]:
                return {"ok": False, "error": "Уже макс"}
            cost = cfg["cost_fn"](lvl + 1)
            if int(row["jetons"] or 0) < cost:
                return {"ok": False, "error": "Недостаточно жетонов", "cost": cost}
            await conn.execute(
                f"update forge_users set jetons = jetons - $2, {col} = {col} + 1 where tg_id = $1",
                tg_id, cost,
            )
    return {
        "ok": True,
        "branch": branch,
        "new_level": lvl + 1,
        "cost": cost,
        "new_jetons": int(row["jetons"]) - cost,
    }


def get_branches_info() -> list[dict]:
    """Return boss-hunter prestige branches for UI display."""
    out = []
    for key, cfg in BOSS_HUNTER_BRANCHES.items():
        out.append({
            "key": key,
            "name": cfg["name"],
            "desc": cfg["desc"],
            "max_level": cfg["max_level"],
            "effect_per_level": cfg["effect_per_level"],
            "unit": cfg["unit"],
        })
    return out
