"""Core CS:Clicker game logic. All numbers server-side.

Flow:
  - Player taps  → /api/clicker/tap with N taps + window. Server applies damage,
    checks crits, drops coins, advances level on kill.
  - /state       → full snapshot for HUD.
  - /upgrade     → buy upgrade level (idempotent atomic).
  - /chest/open  → roll loot from chest.
  - /artifact/equip — slot management.
  - /prestige    — reset for ★ glory.
"""
from __future__ import annotations

import json
import logging
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.db.client import pool
from app.clicker import config_loader as cfg

log = logging.getLogger(__name__)


# ---------- helpers ---------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_jsonb(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return None
    return None


def _hp_for_level(level: int) -> Decimal:
    base = Decimal(cfg.HP_BASE) * (Decimal(str(cfg.HP_GROWTH)) ** level)
    boss = cfg.boss_for_level(level)
    if boss:
        base = base * Decimal(str(cfg.HP_BOSS_MULT))
    return base.quantize(Decimal("1"))


def _coin_drop(hp: Decimal, luck: Decimal) -> Decimal:
    base = hp * Decimal(str(cfg.COIN_DROP_RATIO)) * (Decimal(1) + (luck / Decimal(100)))
    return base.quantize(Decimal("1"))


def _is_boss_level(level: int) -> bool:
    return cfg.boss_for_level(level) is not None


def _level_timer_seconds(level: int) -> int:
    return cfg.LEVEL_TIME_BOSS if _is_boss_level(level) else cfg.LEVEL_TIME_NORMAL


def _upgrade_cost(base_cost: int | float, level: int) -> Decimal:
    return (Decimal(str(base_cost)) * (Decimal(str(cfg.COST_GROWTH)) ** level)).quantize(Decimal("1"))


def _upgrade_damage(base_dmg: int | float, level: int) -> Decimal:
    """+20% per level inside one slot. Level 0 = base, level 1 = base*1.2..."""
    return Decimal(str(base_dmg)) * (Decimal(1) + Decimal(str(cfg.DAMAGE_PER_LEVEL)) * level)


def _business_def(business_id: str) -> dict | None:
    for b in cfg.businesses():
        if b["id"] == business_id:
            return b
    return None


def _business_branch_levels(upg_rows, business_id: str) -> dict[str, int]:
    """Map branch_id → owned level for a given business."""
    out: dict[str, int] = {}
    prefix = f"bt_{business_id}_"
    for u in upg_rows:
        if u["kind"] == "business_branch" and u["slot_id"].startswith(prefix):
            branch = u["slot_id"][len(prefix):]
            out[branch] = int(u["level"])
    return out


def _business_branch_pcts(business_id: str, branch_levels: dict[str, int]) -> dict[str, float]:
    """Sum branch effects for a business → {effect_key: total_pct}."""
    totals: dict[str, float] = {}
    branches = cfg.business_tree().get(business_id, [])
    for b in branches:
        lvl = branch_levels.get(b["id"], 0)
        if lvl <= 0:
            continue
        eff = b.get("effect")
        if not eff:
            continue
        totals[eff] = totals.get(eff, 0.0) + float(b.get("per_level", 0)) * lvl
    return totals


def _business_idle_per_sec(business_def: dict, level: int, branch_pcts: dict[str, float] | None = None) -> Decimal:
    """Idle production rate scales by BUSINESS_IDLE_GROWTH^level + branch idle/all bonus."""
    base = Decimal(str(business_def["base_idle_per_sec"]))
    rate = base * (Decimal(str(cfg.BUSINESS_IDLE_GROWTH)) ** level)
    if branch_pcts:
        bonus = Decimal(str(branch_pcts.get("idle_pct", 0))) + Decimal(str(branch_pcts.get("all_yield_pct", 0)))
        rate = rate * (Decimal(1) + bonus / Decimal(100))
        # Crit-yield branch: chance/2 average bonus modeled as straight ×1.5 mult per "pct".
        # crit_yield_pct = N → average idle bumped by N/100 (rare-drop crit on idle ticks).
        crit_avg = Decimal(str(branch_pcts.get("crit_yield_pct", 0))) / Decimal(100)
        rate = rate * (Decimal(1) + crit_avg)
        # rare_drop_pct: averages a ×5 yield burst — model as +rare/20 mean uplift.
        rare = Decimal(str(branch_pcts.get("rare_drop_pct", 0))) / Decimal(20)
        rate = rate * (Decimal(1) + rare)
    return rate


def _business_tap_yield(business_def: dict, level: int, branch_pcts: dict[str, float] | None = None) -> Decimal:
    """Tap yield also scales mildly with level + branch tap/all bonus.
    Includes tap_crit_x_pct (Premium-холодильник Pepsi-style) — chance for ×2 modeled as average uplift."""
    base = Decimal(str(business_def["base_tap_yield"]))
    yld = base * (Decimal(str(cfg.BUSINESS_IDLE_GROWTH)) ** level)
    if branch_pcts:
        bonus = Decimal(str(branch_pcts.get("tap_pct", 0))) + Decimal(str(branch_pcts.get("all_yield_pct", 0)))
        yld = yld * (Decimal(1) + bonus / Decimal(100))
        # tap_crit_x_pct = N% chance for ×2 → average +N% per tap.
        tap_crit = Decimal(str(branch_pcts.get("tap_crit_x_pct", 0))) / Decimal(100)
        yld = yld * (Decimal(1) + tap_crit)
    return yld


def _business_consumption_per_sec(business_def: dict, level: int, branch_pcts: dict[str, float] | None = None) -> dict[str, Decimal]:
    """Idle consumption is PROPORTIONAL to own production rate.
    consumption_per_unit defines how much of each input is needed per 1 unit of output.
    So scaling production via level/branches automatically scales consumption — you can't
    pump tier 5 without keeping the upstream chain alive.
    consumption_red_pct branch reduces consumption (capped at 90%).

    Legacy fallback: if a config still uses idle_consumption_per_sec (absolute), respect it."""
    ratios = business_def.get("consumption_per_unit") or {}
    legacy = business_def.get("idle_consumption_per_sec") or {}
    if not ratios and not legacy:
        return {}

    red = Decimal(0)
    if branch_pcts:
        red = Decimal(str(branch_pcts.get("consumption_red_pct", 0)))
    if red > Decimal(90):
        red = Decimal(90)
    mult = (Decimal(100) - red) / Decimal(100)

    if ratios:
        # Proportional model: consumption[r] = production_rate × ratio[r] × (1 − red%).
        prod_rate = _business_idle_per_sec(business_def, level, branch_pcts)
        return {res: (prod_rate * Decimal(str(ratio)) * mult) for res, ratio in ratios.items()}

    # Legacy absolute model (kept for forward-compat in case any business sticks with it).
    growth = Decimal("1.075") ** level
    return {res: (Decimal(str(rate)) * growth * mult) for res, rate in legacy.items()}


def _business_upgrade_cost(business_def: dict, current_level: int) -> Decimal:
    base = Decimal(str(business_def["base_upgrade_cost"]))
    return (base * (Decimal(str(cfg.BUSINESS_COST_GROWTH)) ** current_level)).quantize(Decimal("1"))


async def _business_level(conn, tg_id: int, business_id: str) -> int:
    row = await conn.fetchrow(
        """select level from clicker_upgrades where tg_id = $1 and kind = 'business' and slot_id = $2""",
        tg_id, business_id,
    )
    return int(row["level"]) if row else 0


def _prestige_node_def(node_id: str) -> dict | None:
    for n in cfg.prestige_tree():
        if n["id"] == node_id:
            return n
    return None


async def _prestige_levels(conn, tg_id: int) -> dict[str, int]:
    """Map node_id → owned level (default 0)."""
    rows = await conn.fetch(
        """select slot_id, level from clicker_upgrades
           where tg_id = $1 and kind = 'prestige_node'""",
        tg_id,
    )
    return {r["slot_id"]: int(r["level"]) for r in rows}


def _prestige_effects(levels: dict[str, int]) -> dict:
    """Sum all node effects according to owned level for each."""
    totals: dict[str, float] = {}
    flags: dict[str, bool] = {}
    for nid, lvl in levels.items():
        if lvl <= 0:
            continue
        node = _prestige_node_def(nid)
        if not node:
            continue
        eff = node.get("effect") or {}
        for k, v in eff.items():
            if isinstance(v, bool):
                if v:
                    flags[k] = True
            elif isinstance(v, (int, float)):
                totals[k] = totals.get(k, 0.0) + float(v) * lvl
    return {"pct": totals, "flags": flags}


async def _ensure_business_state(conn, tg_id: int, business_id: str, now: datetime) -> dict:
    row = await conn.fetchrow(
        "select * from clicker_businesses where tg_id = $1 and business_id = $2 for update",
        tg_id, business_id,
    )
    if row:
        return dict(row)
    await conn.execute(
        """insert into clicker_businesses (tg_id, business_id, last_idle_at, pending_amount)
           values ($1, $2, $3, 0) on conflict do nothing""",
        tg_id, business_id, now,
    )
    row = await conn.fetchrow(
        "select * from clicker_businesses where tg_id = $1 and business_id = $2",
        tg_id, business_id,
    )
    return dict(row)


# ---------- ensure_user / state --------------------------------------------


async def ensure_user(tg_id: int, **profile) -> dict:
    """Create row + initial combat state if first visit."""
    now = _now()
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                insert into clicker_users (tg_id, username, first_name, last_name, is_premium)
                values ($1, $2, $3, $4, $5)
                on conflict (tg_id) do update set
                    username = coalesce(excluded.username, clicker_users.username),
                    first_name = coalesce(excluded.first_name, clicker_users.first_name),
                    last_name = coalesce(excluded.last_name, clicker_users.last_name),
                    is_premium = excluded.is_premium,
                    last_seen_at = now()
                returning *, (xmax = 0) as inserted
                """,
                tg_id,
                profile.get("username"),
                profile.get("first_name"),
                profile.get("last_name"),
                bool(profile.get("is_premium", False)),
            )

            if row["inserted"]:
                # Seed starting weapon (slot 1, level 0 — i.e. just owned/active).
                await conn.execute(
                    """insert into clicker_upgrades (tg_id, kind, slot_id, level)
                       values ($1, 'weapon', 'weapon_01', 0)
                       on conflict do nothing""",
                    tg_id,
                )
                # Initial combat state for level 1.
                await _spawn_enemy_for_level(conn, tg_id, 1, now)

    return dict(row)


async def _spawn_enemy_for_level(conn, tg_id: int, level: int, now: datetime) -> dict:
    """Set the combat HP bar for the given level. Resets mechanic_state.
    Adds bonus seconds from artifacts (timer_extend_sec) + prestige (boss_timer_bonus)."""
    hp = _hp_for_level(level)
    is_boss = _is_boss_level(level)
    timer_s = _level_timer_seconds(level)
    # Artifact: Песочные Часы Major → +N sec.
    equipped = await _equipped_artifacts(conn, tg_id)
    art_flags = _artifact_flags(equipped)
    bonus = int(art_flags.get("timer_extend_sec", 0))
    # Prestige tree: boss_timer_bonus on bosses only.
    if is_boss:
        pt = _prestige_effects(await _prestige_levels(conn, tg_id))
        bonus += int(pt["pct"].get("boss_timer_bonus", 0))
    timer_ends_at = now + timedelta(seconds=timer_s + bonus)
    # Initial mechanic_state: timestamp anchored to spawn so first trigger is N sec later.
    initial_mech = {"spawned_at": now.isoformat(), "phases_triggered": [], "active_debuffs": []}
    await conn.execute(
        """insert into clicker_combat_state (tg_id, enemy_hp, enemy_max_hp, is_boss, timer_ends_at, mechanic_state, updated_at)
           values ($1, $2, $2, $3, $4, $5::jsonb, $6)
           on conflict (tg_id) do update set
             enemy_hp = excluded.enemy_hp,
             enemy_max_hp = excluded.enemy_max_hp,
             is_boss = excluded.is_boss,
             timer_ends_at = excluded.timer_ends_at,
             mechanic_state = excluded.mechanic_state,
             updated_at = excluded.updated_at""",
        tg_id, hp, is_boss, timer_ends_at, json.dumps(initial_mech), now,
    )
    return {"hp": hp, "max_hp": hp, "is_boss": is_boss, "timer_ends_at": timer_ends_at}


async def _process_boss_mechanics(conn, tg_id: int, level: int, combat_row,
                                   now: datetime, base_click_dmg: Decimal) -> dict:
    """Run periodic boss mechanics. Returns dict of events + mutations:
       {events: [...], hp_delta: Decimal, click_mult: float, auto_mult: float}.
    Mutates combat row (enemy_hp, timer_ends_at, mechanic_state) directly."""
    boss_def = cfg.boss_for_level(level)
    if not boss_def or not boss_def.get("mechanic"):
        return {"events": [], "click_mult": 1.0, "auto_mult": 1.0}

    mech = boss_def["mechanic"]
    state = _parse_jsonb(combat_row["mechanic_state"]) or {}
    state.setdefault("phases_triggered", [])
    state.setdefault("active_debuffs", [])
    events: list[dict] = []

    mech_type = mech.get("type")

    # Handle expiring debuffs first.
    new_debuffs = []
    for d in state["active_debuffs"]:
        ends_iso = d.get("ends_at")
        if ends_iso:
            ends = datetime.fromisoformat(ends_iso)
            if ends.tzinfo is None:
                ends = ends.replace(tzinfo=timezone.utc)
            if ends > now:
                new_debuffs.append(d)
    state["active_debuffs"] = new_debuffs

    click_mult = 1.0
    auto_mult = 1.0
    for d in state["active_debuffs"]:
        if d.get("kind") == "click_debuff":
            click_mult *= 1.0 - float(d.get("pct", 0)) / 100.0
        elif d.get("kind") == "silence_auto":
            auto_mult *= 0.0
        elif d.get("kind") == "shield":
            red = 1.0 - float(d.get("pct", 0)) / 100.0
            click_mult *= red
            auto_mult *= red

    # Periodic mechanics
    if mech_type in ("heal", "timer_drain", "click_debuff", "silence_auto", "shield"):
        last_iso = state.get("last_trigger_at") or state.get("spawned_at")
        last = datetime.fromisoformat(last_iso) if last_iso else now
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        interval = float(mech.get("interval_sec", 10))
        if (now - last).total_seconds() >= interval:
            state["last_trigger_at"] = now.isoformat()
            shout = mech.get("shout", "")
            if mech_type == "heal":
                heal = (Decimal(combat_row["enemy_max_hp"]) * Decimal(str(mech.get("amount_pct", 5))) / Decimal(100)).quantize(Decimal("1"))
                new_hp = min(Decimal(combat_row["enemy_hp"]) + heal, Decimal(combat_row["enemy_max_hp"]))
                await conn.execute(
                    "update clicker_combat_state set enemy_hp = $2 where tg_id = $1",
                    tg_id, new_hp,
                )
                combat_row = dict(combat_row)
                combat_row["enemy_hp"] = new_hp
                events.append({"type": "heal", "shout": shout, "amount": str(heal)})
            elif mech_type == "timer_drain":
                drain = int(mech.get("drain_sec", 5))
                old_ends = combat_row["timer_ends_at"]
                if old_ends and old_ends.tzinfo is None:
                    old_ends = old_ends.replace(tzinfo=timezone.utc)
                new_ends = old_ends - timedelta(seconds=drain) if old_ends else now
                await conn.execute(
                    "update clicker_combat_state set timer_ends_at = $2 where tg_id = $1",
                    tg_id, new_ends,
                )
                combat_row = dict(combat_row)
                combat_row["timer_ends_at"] = new_ends
                events.append({"type": "timer_drain", "shout": shout, "drained_sec": drain})
            elif mech_type == "click_debuff":
                pct = float(mech.get("debuff_pct", 25))
                duration = int(mech.get("duration_sec", 5))
                state["active_debuffs"].append({
                    "kind": "click_debuff",
                    "pct": pct,
                    "ends_at": (now + timedelta(seconds=duration)).isoformat(),
                })
                click_mult *= (1.0 - pct / 100.0)
                events.append({"type": "click_debuff", "shout": shout, "pct": pct, "duration_sec": duration})
            elif mech_type == "silence_auto":
                duration = int(mech.get("duration_sec", 3))
                state["active_debuffs"].append({
                    "kind": "silence_auto",
                    "ends_at": (now + timedelta(seconds=duration)).isoformat(),
                })
                auto_mult *= 0.0
                events.append({"type": "silence_auto", "shout": shout, "duration_sec": duration})
            elif mech_type == "shield":
                duration = int(mech.get("duration_sec", 4))
                pct = float(mech.get("reduce_pct", 70))
                state["active_debuffs"].append({
                    "kind": "shield",
                    "pct": pct,
                    "ends_at": (now + timedelta(seconds=duration)).isoformat(),
                })
                red = 1.0 - pct / 100.0
                click_mult *= red
                auto_mult *= red
                events.append({"type": "shield", "shout": shout, "pct": pct, "duration_sec": duration})

    # Phase-based heal (final bosses)
    if mech_type == "phase_heal":
        phases = mech.get("phases") or []
        cur_hp_pct = float(Decimal(combat_row["enemy_hp"]) / Decimal(combat_row["enemy_max_hp"]) * 100) if Decimal(combat_row["enemy_max_hp"]) > 0 else 100
        triggered: list = list(state.get("phases_triggered") or [])
        for idx, p in enumerate(phases):
            if idx in triggered:
                continue
            if cur_hp_pct <= float(p.get("hp_pct", 50)):
                heal = (Decimal(combat_row["enemy_max_hp"]) * Decimal(str(p.get("heal_pct", 10))) / Decimal(100)).quantize(Decimal("1"))
                new_hp = min(Decimal(combat_row["enemy_hp"]) + heal, Decimal(combat_row["enemy_max_hp"]))
                await conn.execute(
                    "update clicker_combat_state set enemy_hp = $2 where tg_id = $1",
                    tg_id, new_hp,
                )
                combat_row = dict(combat_row)
                combat_row["enemy_hp"] = new_hp
                triggered.append(idx)
                events.append({"type": "phase_heal", "shout": p.get("shout", ""), "amount": str(heal), "phase": idx + 1})
                break  # one phase per tap
        state["phases_triggered"] = triggered

    await conn.execute(
        "update clicker_combat_state set mechanic_state = $2::jsonb where tg_id = $1",
        tg_id, json.dumps(state),
    )

    return {"events": events, "click_mult": click_mult, "auto_mult": auto_mult, "combat_row": combat_row}


# ---------- damage / stat aggregation --------------------------------------


def _artifact_effects(equipped: list[dict]) -> dict[str, float]:
    """Sum up percentage effects from equipped artifacts/mythics."""
    totals: dict[str, float] = {}
    for inst in equipped:
        eff = inst.get("effect") or {}
        for k, v in eff.items():
            if isinstance(v, (int, float)):
                totals[k] = totals.get(k, 0.0) + float(v)
    return totals


async def _equipped_artifacts(conn, tg_id: int) -> list[dict]:
    """Return list of equipped artifacts/mythics with their effect dicts."""
    art_index = {a["id"]: a for a in cfg.artifacts()}
    mythic_index = {m["id"]: m for m in cfg.mythics()}
    rows = await conn.fetch(
        """select * from clicker_inventory
           where tg_id = $1 and equipped_slot is not null and consumed_at is null""",
        tg_id,
    )
    out = []
    for r in rows:
        if r["item_kind"] == "artifact":
            # item_id is "artifact_<NN>"; strip prefix.
            short = r["item_id"].replace("artifact_", "", 1)
            spec = art_index.get(short)
        elif r["item_kind"] == "mythic":
            short = r["item_id"].replace("mythic_", "", 1)
            spec = mythic_index.get(short)
        else:
            spec = None
        if spec:
            out.append({"effect": spec.get("effect", {}), "spec": spec})
    return out


def _artifact_flags(equipped: list[dict]) -> dict[str, bool | int]:
    """Collect non-pct artifact effects (flags + raw values)."""
    flags: dict[str, bool | int] = {}
    for inst in equipped:
        eff = inst.get("effect") or {}
        for k, v in eff.items():
            if isinstance(v, bool):
                if v:
                    flags[k] = True
            elif k in ("timer_extend_sec", "max_lots_bonus", "online_friend_max",
                       "prestige_casecoins_bonus") and isinstance(v, (int, float)):
                flags[k] = int(flags.get(k, 0)) + int(v)
    return flags


async def _recompute_stats(conn, tg_id: int) -> dict:
    """Recompute combat stats from upgrades + artifacts + prestige bonuses."""
    upg_rows = await conn.fetch(
        "select kind, slot_id, level from clicker_upgrades where tg_id = $1",
        tg_id,
    )
    weapons_index = {f"weapon_{w['id']}": w for w in cfg.weapons()}
    mercs_index = {f"merc_{m['id']}": m for m in cfg.mercs()}
    crit_luck_specs = cfg.crit_luck()
    crit_chance_index = {f"cc_{u['id']}": u for u in crit_luck_specs.get("crit_chance", [])}
    crit_dmg_index = {f"cd_{u['id']}": u for u in crit_luck_specs.get("crit_damage", [])}
    luck_index = {f"lk_{u['id']}": u for u in crit_luck_specs.get("luck", [])}

    click_dmg = Decimal(0)
    auto_dps = Decimal(0)
    crit_chance_pct = Decimal(0)
    crit_dmg_mult = Decimal(2)  # base ×2
    luck = Decimal(0)

    for u in upg_rows:
        kind = u["kind"]; slot = u["slot_id"]; lvl = int(u["level"])
        if kind == "weapon":
            spec = weapons_index.get(slot)
            if spec:
                click_dmg += _upgrade_damage(spec["base_dmg"], lvl)
        elif kind == "merc":
            spec = mercs_index.get(slot)
            if spec:
                auto_dps += _upgrade_damage(spec["base_dps"], lvl)
        elif kind == "crit_chance":
            spec = crit_chance_index.get(slot)
            if spec and lvl > 0:
                crit_chance_pct += Decimal(str(spec["per_level_pct"])) * lvl
        elif kind == "crit_damage":
            spec = crit_dmg_index.get(slot)
            if spec and lvl > 0:
                # crit damage adds % to the multiplier base of 2.0.
                # spec["per_level_pct"] is +5%/level → multiplier += 0.05*lvl.
                crit_dmg_mult += Decimal(str(spec["per_level_pct"])) * lvl / Decimal(100)
        elif kind == "luck":
            spec = luck_index.get(slot)
            if spec and lvl > 0:
                luck += Decimal(str(spec["per_level_pct"])) * lvl

    # Apply artifact/mythic percentage bonuses.
    equipped = await _equipped_artifacts(conn, tg_id)
    eff = _artifact_effects(equipped)
    art_flags = _artifact_flags(equipped)

    # Stream-Camera 4090 — online_friend_pct/max → flat all_dmg uplift (simplified, no friend graph).
    friend_max = int(art_flags.get("online_friend_max", 0))
    if friend_max > 0:
        eff["all_dmg_pct"] = float(eff.get("all_dmg_pct", 0)) + float(friend_max)

    # Apply prestige tree effects.
    pt = _prestige_effects(await _prestige_levels(conn, tg_id))
    pt_pct = pt["pct"]

    # Apply perma_buffs from chests.
    pb_row = await conn.fetchrow("select perma_buffs from clicker_users where tg_id = $1", tg_id)
    pb = _parse_jsonb(pb_row["perma_buffs"]) if pb_row else None
    pb = pb or {}

    def pct(name: str, base: Decimal) -> Decimal:
        return base * (Decimal(1) + Decimal(str(eff.get(name, 0))) / Decimal(100))

    click_dmg = pct("click_damage_pct", click_dmg)
    click_dmg = pct("all_dmg_pct", click_dmg)
    click_dmg = pct("all_income_pct", click_dmg)  # mild — affects damage too via Stickers HL3 lore
    # Prestige tree click bonus
    click_dmg = click_dmg * (Decimal(1) + Decimal(str(pt_pct.get("click_damage_pct", 0))) / Decimal(100))
    # Perma buffs from chests
    click_dmg = click_dmg * (Decimal(1) + Decimal(str(pb.get("click_damage_pct", 0))) / Decimal(100))
    click_dmg = click_dmg * (Decimal(1) + Decimal(str(pb.get("all_dmg_pct", 0))) / Decimal(100))
    auto_dps = pct("auto_dps_pct", auto_dps)
    auto_dps = pct("all_dmg_pct", auto_dps)
    auto_dps = auto_dps * (Decimal(1) + Decimal(str(pt_pct.get("auto_dps_pct", 0))) / Decimal(100))
    auto_dps = auto_dps * (Decimal(1) + Decimal(str(pt_pct.get("merc_dps_pct", 0))) / Decimal(100))
    auto_dps = auto_dps * (Decimal(1) + Decimal(str(pb.get("all_dmg_pct", 0))) / Decimal(100))
    crit_chance_pct = pct("crit_chance_pct", crit_chance_pct) if crit_chance_pct > 0 else crit_chance_pct
    crit_chance_pct += Decimal(str(eff.get("crit_chance_pct", 0)))
    crit_chance_pct += Decimal(str(pt_pct.get("crit_chance_pct", 0)))
    crit_chance_pct += Decimal(str(pb.get("crit_chance_pct", 0)))
    crit_dmg_mult += Decimal(str(eff.get("crit_dmg_pct", 0))) / Decimal(100)
    crit_dmg_mult += Decimal(str(pt_pct.get("crit_dmg_pct", 0))) / Decimal(100)
    luck = pct("luck_pct", luck)
    luck += Decimal(str(pt_pct.get("luck_pct", 0)))

    # Cap crit chance at 100%.
    if crit_chance_pct > 100:
        crit_chance_pct = Decimal(100)

    # HL3 mythic — click_dmg_quadratic: gentle exponential uplift (^1.05).
    if art_flags.get("click_dmg_quadratic") and click_dmg > 0:
        click_dmg = (click_dmg ** Decimal("1.05")).quantize(Decimal("1"))

    await conn.execute(
        """update clicker_users set
              click_damage = $2,
              auto_dps = $3,
              crit_chance = $4,
              crit_multiplier = $5,
              luck = $6
           where tg_id = $1""",
        tg_id, click_dmg, auto_dps, crit_chance_pct, crit_dmg_mult, luck,
    )

    return {
        "click_damage": click_dmg,
        "auto_dps": auto_dps,
        "crit_chance": crit_chance_pct,
        "crit_multiplier": crit_dmg_mult,
        "luck": luck,
    }


# ---------- tap / combat ---------------------------------------------------


async def tap(tg_id: int, taps: int, dt_ms: int) -> dict:
    """Apply N taps to current enemy. Includes auto-DPS for elapsed seconds.

    `taps` is rate-limited to TAP_RATE_BASE (5/sec) by default, or
    TAP_RATE_WITH_PERMIT (10/sec) if the player owns clicker_permit.
    `dt_ms` is the ms elapsed since the player's previous request — server uses
    its own time delta as the source of truth.
    """
    now = _now()
    taps = max(0, min(int(taps), 60))  # hard cap per request, then rate-cap below
    async with pool().acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow(
                "select * from clicker_users where tg_id = $1 for update", tg_id,
            )
            if not user:
                return {"ok": False, "error": "no_user"}
            if user["banned"]:
                return {"ok": False, "error": "banned"}

            # Rate-limit: STRICT 1-sec sliding window of TAP_RATE_BASE taps total.
            # Once the window is full, any further tap request is hard-rejected.
            # Permit removes the cap entirely (external auto-clickers OK).
            permit_lvl = await conn.fetchval(
                "select level from clicker_upgrades where tg_id = $1 and kind = 'permit' and slot_id = 'clicker_permit'",
                tg_id,
            )
            if not permit_lvl:
                try:
                    win_s = user["tap_window_start"]
                    win_c = int(user["tap_window_count"] or 0)
                except (KeyError, IndexError):
                    win_s, win_c = None, 0
                if win_s and win_s.tzinfo is None:
                    win_s = win_s.replace(tzinfo=timezone.utc)
                if win_s and (now - win_s).total_seconds() < 1.0:
                    remaining = max(0, cfg.TAP_RATE_BASE - win_c)
                    if remaining <= 0:
                        # Surface current state so the client can correct optimistic UI.
                        state = await _build_state(conn, tg_id)
                        return {"ok": False, "error": "rate_limit", "rate_cap": cfg.TAP_RATE_BASE,
                                "data": {"state": state}}
                    if taps > remaining:
                        taps = remaining
                    new_s, new_c = win_s, win_c + taps
                else:
                    taps = min(taps, cfg.TAP_RATE_BASE)
                    new_s, new_c = now, taps
                await conn.execute(
                    "update clicker_users set tap_window_start = $2, tap_window_count = $3 where tg_id = $1",
                    tg_id, new_s, new_c,
                )

            combat = await conn.fetchrow(
                "select * from clicker_combat_state where tg_id = $1 for update", tg_id,
            )
            if not combat:
                combat_dict = await _spawn_enemy_for_level(conn, tg_id, int(user["level"]), now)
                combat = await conn.fetchrow(
                    "select * from clicker_combat_state where tg_id = $1 for update", tg_id,
                )

            level = int(user["level"])
            click_dmg = Decimal(user["click_damage"])
            auto_dps = Decimal(user["auto_dps"])
            crit_chance = float(user["crit_chance"])
            crit_mult = Decimal(user["crit_multiplier"])
            luck = Decimal(user["luck"])

            # Time delta: bounded by server clock + max 5s window.
            last_combat = user["last_combat_at"]
            if last_combat is None:
                elapsed_s = 1.0
            else:
                if last_combat.tzinfo is None:
                    last_combat = last_combat.replace(tzinfo=timezone.utc)
                elapsed_s = max(0.0, min(5.0, (now - last_combat).total_seconds()))

            # Read equipped artifacts ONCE for tap-time effects.
            equipped_now = await _equipped_artifacts(conn, tg_id)
            art_flags_now = _artifact_flags(equipped_now)

            # Жетон Valve mythic — auto_cleanse_30s: clear active debuffs every 30s.
            if art_flags_now.get("auto_cleanse_30s") and combat["is_boss"]:
                try:
                    last_cleanse = user["last_cleanse_at"]
                except (KeyError, IndexError):
                    last_cleanse = None
                if last_cleanse and last_cleanse.tzinfo is None:
                    last_cleanse = last_cleanse.replace(tzinfo=timezone.utc)
                if not last_cleanse or (now - last_cleanse).total_seconds() >= 30:
                    state_jsonb = _parse_jsonb(combat["mechanic_state"]) or {}
                    if state_jsonb.get("active_debuffs"):
                        state_jsonb["active_debuffs"] = []
                        await conn.execute(
                            "update clicker_combat_state set mechanic_state = $2::jsonb where tg_id = $1",
                            tg_id, json.dumps(state_jsonb),
                        )
                        # Refresh combat row.
                        combat = await conn.fetchrow(
                            "select * from clicker_combat_state where tg_id = $1 for update", tg_id,
                        )
                    await conn.execute(
                        "update clicker_users set last_cleanse_at = $2 where tg_id = $1",
                        tg_id, now,
                    )

            # Process boss mechanics (only for boss enemies).
            mechanic_events: list[dict] = []
            click_mult = Decimal(1)
            auto_mult = Decimal(1)
            if combat["is_boss"]:
                mech_result = await _process_boss_mechanics(
                    conn, tg_id, level, combat, now, click_dmg,
                )
                mechanic_events = mech_result.get("events") or []
                click_mult = Decimal(str(mech_result.get("click_mult", 1.0)))
                auto_mult = Decimal(str(mech_result.get("auto_mult", 1.0)))
                # Refresh combat reference if mutated.
                new_combat = mech_result.get("combat_row")
                if new_combat:
                    combat = new_combat

            # Apply tap damage with crits.
            tap_damage = Decimal(0)
            crits = 0
            effective_click = click_dmg * click_mult
            for _ in range(taps):
                if random.random() * 100 < crit_chance:
                    tap_damage += effective_click * crit_mult
                    crits += 1
                else:
                    tap_damage += effective_click

            # Glove Case (every_50_taps_minichest): grant common chest at every 50-tap boundary crossed.
            mini_chests = 0
            if taps > 0 and art_flags_now.get("every_50_taps_minichest"):
                try:
                    cur_tc = int(user["tap_counter"] or 0)
                except (KeyError, IndexError, TypeError):
                    cur_tc = 0
                new_tc = cur_tc + taps
                mini_chests = (new_tc // 50) - (cur_tc // 50)
                if mini_chests > 0:
                    for _ in range(mini_chests):
                        await _grant_chest(conn, tg_id, "common")
                await conn.execute(
                    "update clicker_users set tap_counter = $2 where tg_id = $1",
                    tg_id, new_tc % 50,
                )

            # Apply auto-DPS for elapsed window.
            auto_damage = (auto_dps * auto_mult * Decimal(str(elapsed_s))).quantize(Decimal("1"))

            total_damage = tap_damage + auto_damage
            new_hp = Decimal(combat["enemy_hp"]) - total_damage
            killed = new_hp <= 0

            # Track total damage for BP-XP (1k dmg = 1 BP-XP).
            total_dmg_inc = total_damage if total_damage > 0 else Decimal(0)
            if total_dmg_inc > 0:
                await _add_bp_xp(conn, tg_id, total_dmg_inc, now)

            result: dict[str, Any] = {
                "tap_damage": str(tap_damage),
                "auto_damage": str(auto_damage),
                "crits": crits,
                "killed": False,
                "boss_mechanics": mechanic_events,
            }
            if mini_chests > 0:
                result["mini_chests"] = mini_chests

            if not killed:
                # Check timer expiry.
                timer_ends = combat["timer_ends_at"]
                if timer_ends and timer_ends.tzinfo is None:
                    timer_ends = timer_ends.replace(tzinfo=timezone.utc)
                if timer_ends and timer_ends <= now and combat["enemy_max_hp"] > 0:
                    # Timeout — respawn the SAME enemy at full HP, no level rollback.
                    await conn.execute(
                        """update clicker_users set last_combat_at = $2,
                              total_damage = total_damage + $3
                           where tg_id = $1""",
                        tg_id, now, total_dmg_inc,
                    )
                    await _spawn_enemy_for_level(conn, tg_id, level, now)
                    result["timeout"] = True
                    result["new_level"] = level
                    return await _wrap_state(conn, tg_id, result)

                await conn.execute(
                    """update clicker_combat_state set enemy_hp = $2, updated_at = $3
                       where tg_id = $1""",
                    tg_id, new_hp, now,
                )
                await conn.execute(
                    """update clicker_users set last_combat_at = $2,
                          total_damage = total_damage + $3
                       where tg_id = $1""",
                    tg_id, now, total_dmg_inc,
                )
                result["enemy_hp"] = str(new_hp)
                return await _wrap_state(conn, tg_id, result)

            # Killed!
            killed_hp = Decimal(combat["enemy_max_hp"])
            coin_reward = _coin_drop(killed_hp, luck)
            if combat["is_boss"]:
                coin_reward = coin_reward * cfg.BOSS_COIN_MULT

            chest_dropped: str | None = None
            artifact_dropped: dict | None = None

            if combat["is_boss"]:
                boss_def = cfg.boss_for_level(level)
                if boss_def:
                    # Drop chance: flat base + prestige Boss-Hunter bonus. Pure RNG, no pity.
                    pt_now = _prestige_effects(await _prestige_levels(conn, tg_id))
                    drop_chance = cfg.BOSS_CHEST_DROP_BASE + float(pt_now["pct"].get("boss_chest_drop_pct", 0)) / 100.0
                    if random.random() < drop_chance:
                        chest_dropped = boss_def["chest"]
                        await _grant_chest(conn, tg_id, chest_dropped)

                    # Direct gas chance on bosses lvl 30+ (rarest).
                    if level >= cfg.BOSS_GAS_LEVEL_THRESHOLD:
                        gas_chance = cfg.BOSS_GAS_DROP_CHANCE + float(pt_now["pct"].get("gas_drop_pct", 0)) / 100.0
                        if random.random() < gas_chance:
                            await conn.execute(
                                """insert into clicker_resources (tg_id, resource_type, amount) values ($1, 'gas', 1)
                                   on conflict (tg_id, resource_type) do update set amount = clicker_resources.amount + 1""",
                                tg_id,
                            )
                            result["gas_dropped"] = 1

                    if boss_def.get("guaranteed_artifact"):
                        a = _roll_artifact(boss_def["chest"])
                        if a:
                            await _grant_artifact(conn, tg_id, a, "artifact")
                            artifact_dropped = a
                    if boss_def.get("guaranteed_mythic"):
                        m = random.choice(cfg.mythics())
                        await _grant_artifact(conn, tg_id, m, "mythic")
                        artifact_dropped = m
                    # Кубик Steam mythic — boss_kill_free_common_chest.
                    if art_flags_now.get("boss_kill_free_common_chest"):
                        await _grant_chest(conn, tg_id, "common")
                        result["bonus_chest"] = "common"
                    await conn.execute(
                        "update clicker_users set bosses_killed = bosses_killed + 1 where tg_id = $1",
                        tg_id,
                    )

            # Manual level navigation: only auto-advance when player is at the
            # frontier (their personal max). Otherwise respawn the same enemy
            # so they can farm freely.
            cur_max = int(user["max_level"])
            if level >= cur_max:
                # Frontier kill — advance both `level` and `max_level`.
                new_level = level + 1
                new_max = new_level
                advanced = True
            else:
                # Farm kill — stay on the same level, respawn same tier enemy.
                new_level = level
                new_max = cur_max
                advanced = False

            new_checkpoint = int(user["checkpoint"])
            if level % cfg.CHECKPOINT_EVERY == 0 and level > new_checkpoint:
                new_checkpoint = level
            await conn.execute(
                """update clicker_users set
                      level = $2,
                      max_level = $3,
                      checkpoint = $4,
                      cash = cash + $5,
                      last_combat_at = $6,
                      total_damage = total_damage + $7
                   where tg_id = $1""",
                tg_id, new_level, new_max, new_checkpoint, coin_reward, now, total_dmg_inc,
            )
            await _spawn_enemy_for_level(conn, tg_id, new_level, now)

            result["killed"] = True
            result["coin_reward"] = str(coin_reward)
            result["was_boss"] = combat["is_boss"]
            result["new_level"] = new_level
            result["advanced"] = advanced
            if chest_dropped:
                result["chest_dropped"] = chest_dropped
            if artifact_dropped:
                result["artifact_dropped"] = artifact_dropped

            return await _wrap_state(conn, tg_id, result)


# ---------- chest / artifact roll mechanics ---------------------------------


def _roll_artifact(chest_tier: str) -> dict | None:
    spec = cfg.chests().get(chest_tier)
    if not spec:
        return None
    pool = spec["rolls"].get("artifact_pool") or []
    candidates = [a for a in cfg.artifacts() if a["rarity"] in pool]
    if not candidates:
        return None
    return random.choice(candidates)


async def _grant_chest(conn, tg_id: int, chest_tier: str) -> int:
    spec = cfg.chests().get(chest_tier)
    if not spec:
        return 0
    row = await conn.fetchrow(
        """insert into clicker_inventory (tg_id, item_kind, item_id, rarity)
           values ($1, 'chest', $2, $3)
           returning id""",
        tg_id, f"chest_{chest_tier}", chest_tier,
    )
    return int(row["id"])


async def _grant_artifact(conn, tg_id: int, artifact_def: dict, kind: str) -> int:
    rarity = artifact_def.get("rarity", "mythic" if kind == "mythic" else "common")
    item_id = f"{kind}_{artifact_def['id']}"
    row = await conn.fetchrow(
        """insert into clicker_inventory (tg_id, item_kind, item_id, rarity)
           values ($1, $2, $3, $4)
           returning id""",
        tg_id, kind, item_id, rarity,
    )
    return int(row["id"])


async def open_chest(tg_id: int, chest_inventory_id: int) -> dict:
    """Roll loot from a chest in inventory. Atomic, idempotent via consumed_at."""
    now = _now()
    async with pool().acquire() as conn:
        async with conn.transaction():
            chest = await conn.fetchrow(
                """select * from clicker_inventory where id = $1 and tg_id = $2 and item_kind = 'chest'
                   and consumed_at is null for update""",
                chest_inventory_id, tg_id,
            )
            if not chest:
                return {"ok": False, "error": "chest_not_found"}

            tier = chest["rarity"]
            spec = cfg.chests().get(tier)
            if not spec:
                return {"ok": False, "error": "bad_tier"}

            rolls = spec["rolls"]
            cash_min, cash_max = rolls["cash"]
            cash_drop = Decimal(random.randint(int(cash_min), int(cash_max)))

            cc_drop = 0
            if rolls.get("casecoins"):
                cc_min, cc_max = rolls["casecoins"]
                cc_drop = random.randint(int(cc_min), int(cc_max))

            artifact_drop = None
            if random.random() < float(rolls.get("artifact_chance", 0)):
                artifact_drop = _roll_artifact(tier)
                if artifact_drop:
                    await _grant_artifact(conn, tg_id, artifact_drop, "artifact")

            mythic_drop = None
            if rolls.get("mythic_item_chance") and random.random() < float(rolls["mythic_item_chance"]):
                mythic_drop = random.choice(cfg.mythics())
                await _grant_artifact(conn, tg_id, mythic_drop, "mythic")

            # Resource drops.
            resource_drops: dict[str, Decimal] = {}
            res_rolls = rolls.get("resources") or {}
            for res, span in res_rolls.items():
                lo, hi = int(span[0]), int(span[1])
                amt = random.randint(lo, hi)
                if amt > 0:
                    resource_drops[res] = Decimal(amt)

            # Rare gas drop.
            if random.random() < float(rolls.get("gas_chance", 0)):
                gas_span = rolls.get("gas_amount") or [1, 1]
                gas_amt = random.randint(int(gas_span[0]), int(gas_span[1]))
                if gas_amt > 0:
                    resource_drops["gas"] = resource_drops.get("gas", Decimal(0)) + Decimal(gas_amt)

            for res, amt in resource_drops.items():
                await conn.execute(
                    """insert into clicker_resources (tg_id, resource_type, amount) values ($1, $2, $3)
                       on conflict (tg_id, resource_type) do update set amount = clicker_resources.amount + excluded.amount""",
                    tg_id, res, amt,
                )

            # Permanent buffs from chests (Rare+).
            perma_drop = None
            perma_chance = float(rolls.get("perma_buff_chance", 1.0))  # default 1.0 if perma_buff exists
            if rolls.get("perma_buff") and random.random() < perma_chance:
                perma_drop = rolls["perma_buff"]
                # Read current perma_buffs and merge.
                cur = await conn.fetchrow(
                    "select perma_buffs from clicker_users where tg_id = $1", tg_id,
                )
                cur_buffs = _parse_jsonb(cur["perma_buffs"]) or {}
                for k, v in perma_drop.items():
                    cur_buffs[k] = float(cur_buffs.get(k, 0)) + float(v)
                await conn.execute(
                    "update clicker_users set perma_buffs = $2::jsonb where tg_id = $1",
                    tg_id, json.dumps(cur_buffs),
                )

            await conn.execute(
                """update clicker_inventory set consumed_at = $2 where id = $1""",
                chest_inventory_id, now,
            )
            await conn.execute(
                """update clicker_users set
                      cash = cash + $2,
                      casecoins = casecoins + $3,
                      chests_opened = chests_opened + 1
                   where tg_id = $1""",
                tg_id, cash_drop, cc_drop,
            )

            # Recompute stats if perma buff applied.
            if perma_drop:
                await _recompute_stats(conn, tg_id)

            await _log(conn, tg_id, "chest_opened", {
                "tier": tier, "cash": str(cash_drop), "casecoins": cc_drop,
                "artifact": artifact_drop["id"] if artifact_drop else None,
                "mythic": mythic_drop["id"] if mythic_drop else None,
                "resources": {k: str(v) for k, v in resource_drops.items()},
                "perma_buff": perma_drop,
            })

            return await _wrap_state(conn, tg_id, {
                "tier": tier,
                "cash": str(cash_drop),
                "casecoins": cc_drop,
                "artifact": artifact_drop,
                "mythic": mythic_drop,
                "resources": {k: str(v) for k, v in resource_drops.items()},
                "perma_buff": perma_drop,
            })


# ---------- upgrades --------------------------------------------------------


def _upgrade_spec(kind: str, slot_id: str) -> tuple[dict | None, int]:
    """Return (spec, max_level) for a given (kind, slot_id)."""
    if kind == "weapon":
        for w in cfg.weapons():
            if f"weapon_{w['id']}" == slot_id:
                return w, w.get("max_level", 50)
    elif kind == "merc":
        for m in cfg.mercs():
            if f"merc_{m['id']}" == slot_id:
                return m, m.get("max_level", 25)
    elif kind == "crit_chance":
        for u in cfg.crit_luck().get("crit_chance", []):
            if f"cc_{u['id']}" == slot_id:
                return u, u.get("max_level", 30)
    elif kind == "crit_damage":
        for u in cfg.crit_luck().get("crit_damage", []):
            if f"cd_{u['id']}" == slot_id:
                return u, u.get("max_level", 30)
    elif kind == "luck":
        for u in cfg.crit_luck().get("luck", []):
            if f"lk_{u['id']}" == slot_id:
                return u, u.get("max_level", 20)
    elif kind == "permit":
        for p in cfg.permits():
            if p["id"] == slot_id:
                return p, p.get("max_level", 1)
    return None, 0


async def buy_upgrade(tg_id: int, kind: str, slot_id: str, count: int = 1) -> dict:
    count = max(1, min(int(count), 25))  # bulk buy up to 25 levels
    async with pool().acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow(
                "select * from clicker_users where tg_id = $1 for update", tg_id,
            )
            if not user:
                return {"ok": False, "error": "no_user"}

            spec, max_level = _upgrade_spec(kind, slot_id)
            if not spec:
                return {"ok": False, "error": "unknown_upgrade"}

            unlock = int(spec.get("unlock_level", 1))
            if int(user["max_level"]) < unlock:
                return {"ok": False, "error": "locked", "unlock_level": unlock}

            # Permits: paid in casecoins (⌬), not cash. Always count=1 (max_level=1).
            if kind == "permit":
                cur_p = await conn.fetchrow(
                    """select level from clicker_upgrades where tg_id = $1 and kind = 'permit' and slot_id = $2 for update""",
                    tg_id, slot_id,
                )
                if cur_p and int(cur_p["level"]) >= int(max_level):
                    return {"ok": False, "error": "max_level"}
                cc_cost = int(spec.get("casecoin_cost", 0))
                if Decimal(user["casecoins"]) < Decimal(cc_cost):
                    return {"ok": False, "error": "not_enough_casecoins", "needed": str(cc_cost)}
                await conn.execute(
                    "update clicker_users set casecoins = casecoins - $2 where tg_id = $1",
                    tg_id, cc_cost,
                )
                if cur_p:
                    await conn.execute(
                        "update clicker_upgrades set level = level + 1 where tg_id = $1 and kind = 'permit' and slot_id = $2",
                        tg_id, slot_id,
                    )
                else:
                    await conn.execute(
                        "insert into clicker_upgrades (tg_id, kind, slot_id, level) values ($1, 'permit', $2, 1)",
                        tg_id, slot_id,
                    )
                await _log(conn, tg_id, "permit_bought", {"slot": slot_id, "cc_cost": cc_cost})
                return await _wrap_state(conn, tg_id, {
                    "kind": "permit", "slot_id": slot_id, "new_level": 1, "spent_casecoins": cc_cost,
                })

            # Some weapons require a boss kill threshold (e.g. HL3 Crowbar).
            requires_boss = spec.get("requires_boss_kill")
            if requires_boss is not None and int(user["bosses_killed"]) < int(requires_boss):
                return {"ok": False, "error": "boss_kill_locked", "needed_boss_kills": int(requires_boss)}

            cur = await conn.fetchrow(
                """select level from clicker_upgrades where tg_id = $1 and kind = $2 and slot_id = $3 for update""",
                tg_id, kind, slot_id,
            )
            cur_level = int(cur["level"]) if cur else 0
            if cur_level >= max_level:
                return {"ok": False, "error": "max_level"}

            requested = min(count, max_level - cur_level)
            base_cost = spec.get("base_cost", 0)

            total_cost = Decimal(0)
            for i in range(requested):
                total_cost += _upgrade_cost(base_cost, cur_level + i)

            # Resource costs scale per target level: base × RES_COST_GROWTH^(lvl-1).
            res_cost_per_level = spec.get("resource_cost_per_level") or {}
            res_starts = int(spec.get("resource_cost_starts_level", 1))
            total_res_cost: dict[str, Decimal] = {}
            growth = Decimal(str(cfg.RES_COST_GROWTH))
            for i in range(requested):
                target_level = cur_level + i + 1
                if target_level < res_starts:
                    continue
                scale = growth ** (target_level - 1)
                for res, amt in res_cost_per_level.items():
                    cost = (Decimal(str(amt)) * scale).quantize(Decimal("1"))
                    total_res_cost[res] = total_res_cost.get(res, Decimal(0)) + cost

            if Decimal(user["cash"]) < total_cost:
                return {"ok": False, "error": "not_enough_cash", "needed": str(total_cost)}

            if total_res_cost:
                res_rows = await conn.fetch(
                    "select resource_type, amount from clicker_resources where tg_id = $1 for update",
                    tg_id,
                )
                have = {r["resource_type"]: Decimal(r["amount"]) for r in res_rows}
                for res, amt in total_res_cost.items():
                    if have.get(res, Decimal(0)) < amt:
                        return {
                            "ok": False, "error": "not_enough_resource",
                            "resource": res, "needed": str(amt), "have": str(have.get(res, Decimal(0))),
                        }

            new_level = cur_level + requested

            if cur:
                await conn.execute(
                    """update clicker_upgrades set level = $4
                       where tg_id = $1 and kind = $2 and slot_id = $3""",
                    tg_id, kind, slot_id, new_level,
                )
            else:
                await conn.execute(
                    """insert into clicker_upgrades (tg_id, kind, slot_id, level)
                       values ($1, $2, $3, $4)""",
                    tg_id, kind, slot_id, new_level,
                )

            await conn.execute(
                "update clicker_users set cash = cash - $2 where tg_id = $1",
                tg_id, total_cost,
            )
            for res, amt in total_res_cost.items():
                await conn.execute(
                    "update clicker_resources set amount = amount - $3 where tg_id = $1 and resource_type = $2",
                    tg_id, res, amt,
                )

            await _recompute_stats(conn, tg_id)
            await _log(conn, tg_id, "upgrade_bought", {
                "kind": kind, "slot": slot_id, "from": cur_level, "to": new_level,
                "cost": str(total_cost),
                "res_cost": {k: str(v) for k, v in total_res_cost.items()},
            })
            return await _wrap_state(conn, tg_id, {
                "kind": kind, "slot_id": slot_id, "new_level": new_level,
                "spent": str(total_cost),
                "res_spent": {k: str(v) for k, v in total_res_cost.items()},
            })


# ---------- equip / unequip artifacts --------------------------------------


async def equip(tg_id: int, inventory_id: int, slot: int) -> dict:
    """Equip an artifact/mythic into a 0..(slots-1) slot."""
    async with pool().acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow(
                "select artifact_slots from clicker_users where tg_id = $1 for update", tg_id,
            )
            if not user:
                return {"ok": False, "error": "no_user"}
            if slot < 0 or slot >= int(user["artifact_slots"]):
                return {"ok": False, "error": "bad_slot"}

            inv = await conn.fetchrow(
                """select * from clicker_inventory where id = $1 and tg_id = $2 and consumed_at is null for update""",
                inventory_id, tg_id,
            )
            if not inv or inv["item_kind"] not in ("artifact", "mythic"):
                return {"ok": False, "error": "not_equippable"}

            # Unequip whatever was in that slot, then equip new.
            await conn.execute(
                """update clicker_inventory set equipped_slot = null
                   where tg_id = $1 and equipped_slot = $2""",
                tg_id, slot,
            )
            # Also unequip this same item from another slot if it was there.
            await conn.execute(
                """update clicker_inventory set equipped_slot = $3
                   where tg_id = $1 and id = $2""",
                tg_id, inventory_id, slot,
            )

            await _recompute_stats(conn, tg_id)
            await _log(conn, tg_id, "equip", {"id": inventory_id, "slot": slot})
            return await _wrap_state(conn, tg_id, {"equipped": inventory_id, "slot": slot})


async def unequip(tg_id: int, inventory_id: int) -> dict:
    async with pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """update clicker_inventory set equipped_slot = null
                   where tg_id = $1 and id = $2""",
                tg_id, inventory_id,
            )
            await _recompute_stats(conn, tg_id)
            return await _wrap_state(conn, tg_id, {"unequipped": inventory_id})


# ---------- prestige --------------------------------------------------------


async def prestige(tg_id: int) -> dict:
    """Reset progress for ★ glory. Keeps artifacts, casecoins, glory, prestige count + tree."""
    async with pool().acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow(
                "select * from clicker_users where tg_id = $1 for update", tg_id,
            )
            if not user:
                return {"ok": False, "error": "no_user"}
            max_level = int(user["max_level"])
            if max_level < 20:
                return {"ok": False, "error": "level_too_low", "needed": 20}

            # Read prestige tree for bonuses applied at prestige time.
            pt_levels = await _prestige_levels(conn, tg_id)
            pt = _prestige_effects(pt_levels)
            pt_flags = pt["flags"]
            pt_pct = pt["pct"]

            glory_gained = max(1, int((max_level / cfg.PRESTIGE_GLORY_DIVISOR) ** cfg.PRESTIGE_GLORY_EXP))
            if pt_flags.get("next_glory_x2"):
                glory_gained *= 2

            # Artifact slots: base 2 + node levels (max 6).
            base_slots = cfg.ARTIFACT_SLOT_BASE
            slot_bonus = int(pt_pct.get("artifact_slot", 0))
            new_slots = min(cfg.ARTIFACT_SLOT_MAX, max(int(user["artifact_slots"]), base_slots + slot_bonus))

            # Контракт с Гейбом legendary — prestige_casecoins_bonus.
            equipped = await _equipped_artifacts(conn, tg_id)
            art_flags = _artifact_flags(equipped)
            cc_bonus = int(art_flags.get("prestige_casecoins_bonus", 0))

            # Starter cash from prestige tree.
            starter_cash = Decimal(int(pt_pct.get("starter_cash", 0)))

            await conn.execute(
                """update clicker_users set
                      level = 1,
                      checkpoint = 1,
                      cash = $4,
                      glory = glory + $2,
                      casecoins = casecoins + $5,
                      prestige_count = prestige_count + 1,
                      artifact_slots = $3,
                      click_damage = 1,
                      auto_dps = 0,
                      crit_chance = 0,
                      crit_multiplier = 2,
                      luck = 0
                   where tg_id = $1""",
                tg_id, glory_gained, new_slots, starter_cash, cc_bonus,
            )
            # Wipe combat upgrades but keep prestige_node and (optionally) business levels.
            keep_business = pt_flags.get("business_keep_lvl1", False)
            await conn.execute(
                """delete from clicker_upgrades
                   where tg_id = $1 and kind not in ('prestige_node')""",
                tg_id,
            )
            # Re-seed weapon_01.
            await conn.execute(
                """insert into clicker_upgrades (tg_id, kind, slot_id, level)
                   values ($1, 'weapon', 'weapon_01', 0)""",
                tg_id,
            )
            # If business_reserve flag, re-give level 1 of every business they had unlocked.
            if keep_business:
                user_max = max_level  # use pre-prestige peak
                for bdef in cfg.businesses():
                    if user_max >= int(bdef["unlock_level"]):
                        await conn.execute(
                            """insert into clicker_upgrades (tg_id, kind, slot_id, level)
                               values ($1, 'business', $2, 1)""",
                            tg_id, bdef["id"],
                        )
            # Reset business idle/pending.
            await conn.execute("delete from clicker_businesses where tg_id = $1", tg_id)
            # Reset resources except keep gas (rare).
            await conn.execute(
                "delete from clicker_resources where tg_id = $1 and resource_type != 'gas'",
                tg_id,
            )
            # If glory_doubler was used, consume it.
            if pt_flags.get("next_glory_x2"):
                await conn.execute(
                    """delete from clicker_upgrades
                       where tg_id = $1 and kind = 'prestige_node' and slot_id = 'pt_glory_doubler'""",
                    tg_id,
                )
            # Spawn enemy for level 1.
            await _spawn_enemy_for_level(conn, tg_id, 1, _now())

            await _recompute_stats(conn, tg_id)
            await _log(conn, tg_id, "prestige", {"max_level": max_level, "glory_gained": glory_gained})
            return await _wrap_state(conn, tg_id, {"glory_gained": glory_gained, "starter_cash": str(starter_cash)})


async def goto_level(tg_id: int, target_level: int) -> dict:
    """Manually switch to any level in [1, max_level]. Spawns fresh enemy at that level."""
    now = _now()
    async with pool().acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow(
                "select level, max_level, checkpoint from clicker_users where tg_id = $1 for update",
                tg_id,
            )
            if not user:
                return {"ok": False, "error": "no_user"}
            tgt = max(1, min(int(target_level), int(user["max_level"])))
            await conn.execute(
                "update clicker_users set level = $2, last_combat_at = $3 where tg_id = $1",
                tg_id, tgt, now,
            )
            await _spawn_enemy_for_level(conn, tg_id, tgt, now)
            return await _wrap_state(conn, tg_id, {"new_level": tgt})


async def buy_prestige_node(tg_id: int, node_id: str) -> dict:
    """Spend ★ glory to level a prestige tree node. Each level has its own cost."""
    node = _prestige_node_def(node_id)
    if not node:
        return {"ok": False, "error": "unknown_node"}
    async with pool().acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow(
                "select glory from clicker_users where tg_id = $1 for update", tg_id,
            )
            if not user:
                return {"ok": False, "error": "no_user"}
            cur = await conn.fetchrow(
                """select level from clicker_upgrades
                   where tg_id = $1 and kind = 'prestige_node' and slot_id = $2 for update""",
                tg_id, node_id,
            )
            cur_level = int(cur["level"]) if cur else 0
            if cur_level >= int(node.get("max_level", 1)):
                return {"ok": False, "error": "max_level"}

            costs = node.get("cost_per_level") or [1]
            cost_idx = min(cur_level, len(costs) - 1)
            cost = Decimal(int(costs[cost_idx]))
            if Decimal(user["glory"]) < cost:
                return {"ok": False, "error": "not_enough_glory", "needed": str(cost)}

            new_level = cur_level + 1
            if cur:
                await conn.execute(
                    """update clicker_upgrades set level = $3
                       where tg_id = $1 and kind = 'prestige_node' and slot_id = $2""",
                    tg_id, node_id, new_level,
                )
            else:
                await conn.execute(
                    """insert into clicker_upgrades (tg_id, kind, slot_id, level)
                       values ($1, 'prestige_node', $2, $3)""",
                    tg_id, node_id, new_level,
                )
            await conn.execute(
                "update clicker_users set glory = glory - $2 where tg_id = $1",
                tg_id, cost,
            )
            # If artifact_slot purchased, raise artifact_slots by the per-level value (1).
            if "artifact_slot" in (node.get("effect") or {}):
                await conn.execute(
                    """update clicker_users
                       set artifact_slots = least($2, artifact_slots + 1)
                       where tg_id = $1""",
                    tg_id, cfg.ARTIFACT_SLOT_MAX,
                )
            await _recompute_stats(conn, tg_id)
            await _log(conn, tg_id, "prestige_node_bought", {
                "node_id": node_id, "from": cur_level, "to": new_level, "cost": str(cost),
            })
            return await _wrap_state(conn, tg_id, {"node_id": node_id, "new_level": new_level, "spent": str(cost)})


# ---------- state snapshot --------------------------------------------------


async def _wrap_state(conn, tg_id: int, extra: dict | None = None) -> dict:
    state = await _build_state(conn, tg_id)
    if extra is not None:
        return {"ok": True, "data": {"state": state, **extra}}
    return {"ok": True, "data": {"state": state}}


# ---------- battle pass --------------------------------------------------


def _bp_week_start(now: datetime) -> date:
    """Return the date of the most recent Monday 00:00 UTC."""
    # weekday(): Monday=0..Sunday=6
    days_since_monday = now.weekday()
    monday = (now - timedelta(days=days_since_monday)).date()
    return monday


def _bp_xp_to_level(bp_xp: Decimal) -> tuple[int, Decimal, Decimal]:
    """Given total weekly BP-XP, return (level, xp_into_level, xp_for_next).
    Level capped at config max_level."""
    cfg_bp = cfg.battlepass()
    max_level = int(cfg_bp.get("max_level", 50))
    xp_per_level = cfg_bp.get("xp_per_level") or [100] * max_level
    remaining = Decimal(bp_xp)
    level = 0
    while level < max_level and remaining >= Decimal(int(xp_per_level[level])):
        remaining -= Decimal(int(xp_per_level[level]))
        level += 1
    next_cost = Decimal(int(xp_per_level[level])) if level < max_level else Decimal(0)
    return level, remaining, next_cost


async def _ensure_bp_week(conn, tg_id: int, now: datetime) -> dict:
    """Get-or-create the player's battle-pass row for the current UTC week."""
    week = _bp_week_start(now)
    row = await conn.fetchrow(
        "select * from clicker_battlepass where tg_id = $1 and week_start = $2",
        tg_id, week,
    )
    if row:
        return dict(row)
    await conn.execute(
        """insert into clicker_battlepass (tg_id, week_start, bp_xp, bp_level, premium, rewards_claimed)
           values ($1, $2, 0, 0, false, '{}'::int[])
           on conflict (tg_id, week_start) do nothing""",
        tg_id, week,
    )
    row = await conn.fetchrow(
        "select * from clicker_battlepass where tg_id = $1 and week_start = $2",
        tg_id, week,
    )
    return dict(row)


async def _add_bp_xp(conn, tg_id: int, damage: Decimal, now: datetime) -> None:
    """Convert damage → BP-XP (1k damage = 1 XP) and update weekly row."""
    if damage <= 0:
        return
    bp_xp_delta = (damage / Decimal(1000)).quantize(Decimal("0.01"))
    row = await _ensure_bp_week(conn, tg_id, now)
    new_total = Decimal(row["bp_xp"]) + bp_xp_delta
    new_level, _rem, _next = _bp_xp_to_level(new_total)
    await conn.execute(
        """update clicker_battlepass set bp_xp = $3, bp_level = $4
           where tg_id = $1 and week_start = $2""",
        tg_id, row["week_start"], new_total, new_level,
    )
    # Mirror cumulative bp_xp into clicker_users for any leaderboards.
    await conn.execute(
        "update clicker_users set bp_xp = bp_xp + $2 where tg_id = $1",
        tg_id, bp_xp_delta,
    )


async def bp_buy_premium(tg_id: int) -> dict:
    cfg_bp = cfg.battlepass()
    cost = int(cfg_bp.get("premium_cost_casecoins", 50))
    now = _now()
    async with pool().acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow(
                "select casecoins from clicker_users where tg_id = $1 for update", tg_id,
            )
            if not user:
                return {"ok": False, "error": "no_user"}
            row = await _ensure_bp_week(conn, tg_id, now)
            if row["premium"]:
                return {"ok": False, "error": "already_premium"}
            if Decimal(user["casecoins"]) < Decimal(cost):
                return {"ok": False, "error": "not_enough_casecoins", "needed": str(cost)}
            await conn.execute(
                "update clicker_users set casecoins = casecoins - $2 where tg_id = $1",
                tg_id, cost,
            )
            await conn.execute(
                """update clicker_battlepass set premium = true
                   where tg_id = $1 and week_start = $2""",
                tg_id, row["week_start"],
            )
            await _log(conn, tg_id, "bp_premium_bought", {"week": str(row["week_start"]), "cost": cost})
    state = await get_state(tg_id)
    return {"ok": True, "data": state["data"]}


async def bp_claim(tg_id: int, level: int, track: str) -> dict:
    """Claim a free or premium reward at the given BP level."""
    cfg_bp = cfg.battlepass()
    rewards = cfg_bp.get("rewards") or []
    reward_def = next((r for r in rewards if int(r["level"]) == int(level)), None)
    if not reward_def:
        return {"ok": False, "error": "unknown_level"}
    track = track if track in ("free", "premium") else "free"
    payload = reward_def.get(track) or {}
    if not payload:
        return {"ok": False, "error": "empty_reward"}

    now = _now()
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await _ensure_bp_week(conn, tg_id, now)
            if int(row["bp_level"]) < int(level):
                return {"ok": False, "error": "level_locked"}
            if track == "premium" and not row["premium"]:
                return {"ok": False, "error": "premium_locked"}

            claimed: list = list(row["rewards_claimed"] or [])
            # We encode claimed as level*2 (free) or level*2+1 (premium).
            key = int(level) * 2 + (1 if track == "premium" else 0)
            if key in claimed:
                return {"ok": False, "error": "already_claimed"}

            granted = await _grant_bp_reward(conn, tg_id, payload, now)
            claimed.append(key)
            await conn.execute(
                """update clicker_battlepass set rewards_claimed = $3
                   where tg_id = $1 and week_start = $2""",
                tg_id, row["week_start"], claimed,
            )
            await _log(conn, tg_id, "bp_reward_claimed", {
                "level": level, "track": track, "granted": granted,
            })
    state = await get_state(tg_id)
    return {"ok": True, "data": {**state["data"], "granted": granted}}


async def _grant_bp_reward(conn, tg_id: int, payload: dict, now: datetime) -> dict:
    """Apply a reward payload from BP config. Returns dict of what was granted."""
    granted: dict = {}
    if "cash" in payload:
        await conn.execute(
            "update clicker_users set cash = cash + $2 where tg_id = $1",
            tg_id, Decimal(int(payload["cash"])),
        )
        granted["cash"] = str(payload["cash"])
    if "casecoins" in payload:
        await conn.execute(
            "update clicker_users set casecoins = casecoins + $2 where tg_id = $1",
            tg_id, Decimal(int(payload["casecoins"])),
        )
        granted["casecoins"] = int(payload["casecoins"])
    if "glory" in payload:
        await conn.execute(
            "update clicker_users set glory = glory + $2 where tg_id = $1",
            tg_id, Decimal(int(payload["glory"])),
        )
        granted["glory"] = int(payload["glory"])
    if "resources" in payload:
        for res, amt in payload["resources"].items():
            await conn.execute(
                """insert into clicker_resources (tg_id, resource_type, amount) values ($1, $2, $3)
                   on conflict (tg_id, resource_type) do update set amount = clicker_resources.amount + excluded.amount""",
                tg_id, res, Decimal(int(amt)),
            )
        granted["resources"] = payload["resources"]
    if "chest" in payload:
        chest_id = await _grant_chest(conn, tg_id, payload["chest"])
        granted["chest"] = payload["chest"]
        granted["chest_inventory_id"] = chest_id
    if "artifact_rarity" in payload:
        rarity = payload["artifact_rarity"]
        candidates = [a for a in cfg.artifacts() if a["rarity"] == rarity]
        if candidates:
            chosen = random.choice(candidates)
            await _grant_artifact(conn, tg_id, chosen, "artifact")
            granted["artifact"] = chosen
    if "exclusive_artifact_weekly" in payload:
        # Pick a random Mythic artifact as the "exclusive of the week".
        mythics = [a for a in cfg.artifacts() if a["rarity"] == "mythic"]
        if mythics:
            chosen = random.choice(mythics)
            await _grant_artifact(conn, tg_id, chosen, "artifact")
            granted["exclusive_artifact"] = chosen
    return granted


async def get_battlepass(tg_id: int) -> dict:
    now = _now()
    async with pool().acquire() as conn:
        row = await _ensure_bp_week(conn, tg_id, now)
    cfg_bp = cfg.battlepass()
    bp_xp = Decimal(row["bp_xp"])
    level, into_lvl, next_cost = _bp_xp_to_level(bp_xp)
    return {
        "ok": True,
        "data": {
            "week_start": str(row["week_start"]),
            "bp_xp": str(bp_xp),
            "bp_level": int(level),
            "xp_into_level": str(into_lvl),
            "xp_for_next": str(next_cost),
            "premium": bool(row["premium"]),
            "rewards_claimed": list(row["rewards_claimed"] or []),
            "max_level": int(cfg_bp.get("max_level", 50)),
            "premium_cost_casecoins": int(cfg_bp.get("premium_cost_casecoins", 50)),
            "rewards": cfg_bp.get("rewards") or [],
        },
    }


# ---------- end battle pass ---------------------------------------------


async def _accrue_casecoin_time(conn, tg_id: int, user_row, now: datetime) -> int:
    """Award 1 casecoin per CASECOINS_RATE_SECONDS (600s = 10min) of online time.
    Daily cap = CASECOINS_DAILY_CAP (60). Anti-farming: time delta capped at 120s/request.
    """
    today = now.date()
    user_row = dict(user_row)

    # Roll over the daily counter if a new UTC day started.
    cur_day = user_row.get("casecoins_day")
    if cur_day != today:
        await conn.execute(
            "update clicker_users set casecoins_today = 0, casecoins_day = $2 where tg_id = $1",
            tg_id, today,
        )
        user_row["casecoins_today"] = 0
        user_row["casecoins_day"] = today

    last_seen = user_row["last_seen_at"]
    if last_seen is None:
        return 0
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    delta_s = max(0.0, min(120.0, (now - last_seen).total_seconds()))
    if delta_s <= 0:
        return 0

    new_online = int(user_row.get("online_seconds") or 0) + int(delta_s)
    casecoins_today = int(user_row.get("casecoins_today") or 0)
    awards = 0
    while new_online >= cfg.CASECOINS_RATE_SECONDS and casecoins_today < cfg.CASECOINS_DAILY_CAP:
        new_online -= cfg.CASECOINS_RATE_SECONDS
        casecoins_today += 1
        awards += 1

    await conn.execute(
        """update clicker_users set
              online_seconds = $2,
              casecoins_today = $3,
              casecoins = casecoins + $4
           where tg_id = $1""",
        tg_id, new_online, casecoins_today, awards,
    )
    return awards


async def _check_timeout_respawn(conn, tg_id: int, now: datetime) -> bool:
    """If the current enemy's timer expired without a kill, respawn it at full HP.
    Returns True if a respawn happened. Without this, the player has to tap to
    unstick a timed-out enemy — bad UX when they walk away mid-fight."""
    user = await conn.fetchrow(
        "select level from clicker_users where tg_id = $1", tg_id,
    )
    if not user:
        return False
    combat = await conn.fetchrow(
        "select * from clicker_combat_state where tg_id = $1 for update", tg_id,
    )
    if not combat:
        return False
    timer_ends = combat["timer_ends_at"]
    if timer_ends and timer_ends.tzinfo is None:
        timer_ends = timer_ends.replace(tzinfo=timezone.utc)
    if timer_ends and timer_ends <= now and Decimal(combat["enemy_max_hp"]) > 0:
        await _spawn_enemy_for_level(conn, tg_id, int(user["level"]), now)
        return True
    return False


async def get_state(tg_id: int) -> dict:
    async with pool().acquire() as conn:
        async with conn.transaction():
            user_row = await conn.fetchrow(
                "select * from clicker_users where tg_id = $1 for update", tg_id,
            )
            if user_row:
                now = _now()
                await _accrue_casecoin_time(conn, tg_id, user_row, now)
                await _check_timeout_respawn(conn, tg_id, now)
        return await _wrap_state(conn, tg_id)


async def _build_state(conn, tg_id: int) -> dict:
    user = await conn.fetchrow("select * from clicker_users where tg_id = $1", tg_id)
    if not user:
        raise RuntimeError("ensure_user must be called first")

    combat = await conn.fetchrow("select * from clicker_combat_state where tg_id = $1", tg_id)
    upg_rows = await conn.fetch(
        "select kind, slot_id, level from clicker_upgrades where tg_id = $1", tg_id,
    )
    inv_rows = await conn.fetch(
        """select * from clicker_inventory where tg_id = $1 and consumed_at is null
           order by acquired_at desc limit 200""",
        tg_id,
    )
    res_rows = await conn.fetch(
        "select resource_type, amount from clicker_resources where tg_id = $1", tg_id,
    )
    biz_rows = await conn.fetch(
        "select * from clicker_businesses where tg_id = $1", tg_id,
    )

    # Compute live pending for each business (without persisting — just for display).
    now = _now()
    biz_state = []
    user_max = int(user["max_level"])
    for bdef in cfg.businesses():
        bid = bdef["id"]
        if user_max < int(bdef["unlock_level"]):
            continue
        # Find row.
        biz_row = next((dict(r) for r in biz_rows if r["business_id"] == bid), None)
        bus_level = next((int(u["level"]) for u in upg_rows if u["kind"] == "business" and u["slot_id"] == bid), 0)
        rate = _business_idle_per_sec(bdef, bus_level)
        upgrade_cost = _business_upgrade_cost(bdef, bus_level)
        tap_yield = _business_tap_yield(bdef, bus_level)
        pending = Decimal(0)
        if biz_row:
            last = biz_row["last_idle_at"]
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            elapsed = max(0.0, (now - last).total_seconds())
            cap = cfg.BUSINESS_IDLE_CAP_HOURS * 3600
            elapsed = min(elapsed, cap)
            produced = (rate * Decimal(str(elapsed)))
            pending = Decimal(biz_row["pending_amount"]) + produced
        biz_res_cost = _business_resource_cost(bdef, bus_level)
        # Branch state for this business
        branch_lvls = _business_branch_levels(upg_rows, bid)
        # Recompute rate/tap with branch bonuses
        branch_pcts = _business_branch_pcts(bid, branch_lvls)
        rate = _business_idle_per_sec(bdef, bus_level, branch_pcts)
        tap_yield = _business_tap_yield(bdef, bus_level, branch_pcts)
        consumption = _business_consumption_per_sec(bdef, bus_level, branch_pcts)
        biz_state.append({
            "id": bid,
            "level": bus_level,
            "unlock_level": int(bdef["unlock_level"]),
            "resource": bdef["resource"],
            "rate_per_sec": str(rate.quantize(Decimal("0.0001"))),
            "tap_yield": str(tap_yield.quantize(Decimal("0.001"))),
            "pending": str(pending.quantize(Decimal("0.01"))),
            "upgrade_cost": str(upgrade_cost),
            "upgrade_resource_cost": {k: str(v) for k, v in biz_res_cost.items()},
            "idle_consumption_per_sec": {k: str(v.quantize(Decimal("0.0001"))) for k, v in consumption.items()},
            "branches": branch_lvls,
            "branch_bonuses": branch_pcts,
        })

    level = int(user["level"])
    boss_def = cfg.boss_for_level(level)
    loc = cfg.location_for_level(level)
    enemy_sprite = cfg.enemy_for_level(level)

    return {
        "user": {
            "tg_id": int(user["tg_id"]),
            "first_name": user["first_name"],
            "username": user["username"],
            "level": level,
            "max_level": int(user["max_level"]),
            "checkpoint": int(user["checkpoint"]),
            "cash": str(user["cash"]),
            "casecoins": str(user["casecoins"]),
            "glory": str(user["glory"]),
            "bp_xp": str(user["bp_xp"]),
            "click_damage": str(user["click_damage"]),
            "auto_dps": str(user["auto_dps"]),
            "crit_chance": str(user["crit_chance"]),
            "crit_multiplier": str(user["crit_multiplier"]),
            "luck": str(user["luck"]),
            "prestige_count": int(user["prestige_count"]),
            "artifact_slots": int(user["artifact_slots"]),
            "bosses_killed": int(user["bosses_killed"]),
            "chests_opened": int(user["chests_opened"]),
            "total_damage": str(user["total_damage"]),
        },
        "combat": {
            "enemy_hp": str(combat["enemy_hp"]) if combat else "0",
            "enemy_max_hp": str(combat["enemy_max_hp"]) if combat else "0",
            "is_boss": bool(combat["is_boss"]) if combat else False,
            "timer_ends_at": combat["timer_ends_at"].isoformat() if combat and combat["timer_ends_at"] else None,
        } if combat else {"enemy_hp": "0", "enemy_max_hp": "0", "is_boss": False, "timer_ends_at": None},
        "level_meta": {
            "level": level,
            "is_boss": boss_def is not None,
            "location_name": loc["name"],
            "location_bg": loc["bg"],
            "enemy_sprite": boss_def["icon"] if boss_def else enemy_sprite,
            "enemy_name": boss_def["name"] if boss_def else None,
            "boss_flavor": boss_def["flavor"] if boss_def else None,
            "next_boss_level": _next_boss_level(level),
        },
        "upgrades": [
            {"kind": u["kind"], "slot_id": u["slot_id"], "level": int(u["level"])}
            for u in upg_rows
        ],
        "inventory": [
            {
                "id": int(r["id"]),
                "kind": r["item_kind"],
                "item_id": r["item_id"],
                "rarity": r["rarity"],
                "equipped_slot": int(r["equipped_slot"]) if r["equipped_slot"] is not None else None,
                "metadata": _parse_jsonb(r["metadata"]) or {},
            }
            for r in inv_rows
        ],
        "resources": {r["resource_type"]: str(r["amount"]) for r in res_rows},
        "businesses": biz_state,
        "prestige_nodes": {
            u["slot_id"]: int(u["level"]) for u in upg_rows if u["kind"] == "prestige_node"
        },
        "server_time": _now().isoformat(),
    }


def _next_boss_level(level: int) -> int:
    nxt = ((level - 1) // 5 + 1) * 5
    if nxt < level:
        nxt += 5
    return max(level, nxt)


# ---------- businesses ------------------------------------------------------


async def _accrue_business_idle(conn, tg_id: int, business_id: str, now: datetime) -> Decimal:
    """Compute new idle pending amount and update last_idle_at to `now`.
    Drains idle_consumption_per_sec from resources for sustainable seconds.
    If the player runs out of a consumed resource mid-window, production stops at that point."""
    bdef = _business_def(business_id)
    if not bdef:
        return Decimal(0)
    state = await _ensure_business_state(conn, tg_id, business_id, now)
    last = state["last_idle_at"]
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    elapsed = max(0.0, (now - last).total_seconds())
    cap = cfg.BUSINESS_IDLE_CAP_HOURS * 3600
    elapsed = min(elapsed, cap)
    level = await _business_level(conn, tg_id, business_id)
    upg_rows = await conn.fetch(
        "select kind, slot_id, level from clicker_upgrades where tg_id = $1 and kind = 'business_branch'",
        tg_id,
    )
    branch_lvls = _business_branch_levels(upg_rows, business_id)
    branch_pcts = _business_branch_pcts(business_id, branch_lvls)

    # Fetch equipped artifacts once for both offline_pct and all_production_x3.
    equipped = await _equipped_artifacts(conn, tg_id)
    eff = _artifact_effects(equipped)
    art_flags = _artifact_flags(equipped)

    # Apply offline_pct branch when window > 60 sec (treat as offline accrual).
    offline_mult = Decimal(1)
    if elapsed > 60:
        off_pct = Decimal(str(branch_pcts.get("offline_pct", 0)))
        off_pct += Decimal(str(eff.get("offline_pct", 0)))
        offline_mult = Decimal(1) + off_pct / Decimal(100)

    # Λ-Кристалл mythic — all_production_x3 multiplies production AND consumption.
    prod_mult = Decimal(3) if art_flags.get("all_production_x3") else Decimal(1)
    # Resource-specific bonus from artifacts (e.g. brass_pct, contraband_pct, all_resources_pct).
    res_id = bdef.get("resource") or ""
    res_bonus = Decimal(str(eff.get(f"{res_id}_pct", 0))) + Decimal(str(eff.get("all_resources_pct", 0)))
    res_mult = Decimal(1) + res_bonus / Decimal(100)

    rate = _business_idle_per_sec(bdef, level, branch_pcts) * offline_mult * prod_mult * res_mult
    consumption = _business_consumption_per_sec(bdef, level, branch_pcts)
    if art_flags.get("all_production_x3"):
        consumption = {k: v * Decimal(3) for k, v in consumption.items()}

    # Determine how many of `elapsed` seconds we can actually sustain given resources.
    sustainable_s = Decimal(str(elapsed))
    if consumption:
        # Read current resources for the consumed types.
        res_rows = await conn.fetch(
            "select resource_type, amount from clicker_resources where tg_id = $1 and resource_type = any($2::text[]) for update",
            tg_id, list(consumption.keys()),
        )
        have = {r["resource_type"]: Decimal(r["amount"]) for r in res_rows}
        for res, cons_rate in consumption.items():
            if cons_rate <= 0:
                continue
            avail = have.get(res, Decimal(0))
            max_for_res = avail / cons_rate
            if max_for_res < sustainable_s:
                sustainable_s = max_for_res
        # Drain consumption for the sustainable window.
        if sustainable_s > 0:
            for res, cons_rate in consumption.items():
                drained = (cons_rate * sustainable_s).quantize(Decimal("0.0001"))
                await conn.execute(
                    """insert into clicker_resources (tg_id, resource_type, amount) values ($1, $2, 0)
                       on conflict (tg_id, resource_type) do update set amount = clicker_resources.amount - $3""",
                    tg_id, res, drained,
                )

    produced = (rate * sustainable_s).quantize(Decimal("0.0001"))
    new_pending = Decimal(state["pending_amount"]) + produced
    await conn.execute(
        """update clicker_businesses set pending_amount = $3, last_idle_at = $4
           where tg_id = $1 and business_id = $2""",
        tg_id, business_id, new_pending, now,
    )
    return new_pending


async def business_tap(tg_id: int, business_id: str) -> dict:
    bdef = _business_def(business_id)
    if not bdef:
        return {"ok": False, "error": "unknown_business"}
    now = _now()
    async with pool().acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow(
                "select * from clicker_users where tg_id = $1 for update", tg_id,
            )
            if not user:
                return {"ok": False, "error": "no_user"}
            if int(user["max_level"]) < int(bdef["unlock_level"]):
                return {"ok": False, "error": "locked", "unlock_level": int(bdef["unlock_level"])}

            # Rate-limit business taps via 1-sec sliding window — unless permit owned.
            permit_lvl = await conn.fetchval(
                "select level from clicker_upgrades where tg_id = $1 and kind = 'permit' and slot_id = 'clicker_permit'",
                tg_id,
            )
            if not permit_lvl:
                try:
                    win_start = user["biz_tap_window_start"]
                    win_count = int(user["biz_tap_window_count"] or 0)
                except (KeyError, IndexError):
                    win_start, win_count = None, 0
                if win_start and win_start.tzinfo is None:
                    win_start = win_start.replace(tzinfo=timezone.utc)
                if win_start and (now - win_start).total_seconds() < 1.0:
                    if win_count >= cfg.TAP_RATE_BASE:
                        return {"ok": False, "error": "throttled", "rate_cap": cfg.TAP_RATE_BASE}
                    new_start, new_count = win_start, win_count + 1
                else:
                    new_start, new_count = now, 1
                await conn.execute(
                    "update clicker_users set biz_tap_window_start = $2, biz_tap_window_count = $3 where tg_id = $1",
                    tg_id, new_start, new_count,
                )

            level = await _business_level(conn, tg_id, business_id)
            upg_rows = await conn.fetch(
                "select kind, slot_id, level from clicker_upgrades where tg_id = $1 and kind = 'business_branch'",
                tg_id,
            )
            branch_lvls = _business_branch_levels(upg_rows, business_id)
            branch_pcts = _business_branch_pcts(business_id, branch_lvls)
            yield_amount = _business_tap_yield(bdef, level, branch_pcts)
            await conn.execute(
                """insert into clicker_resources (tg_id, resource_type, amount) values ($1, $2, $3)
                   on conflict (tg_id, resource_type) do update set amount = clicker_resources.amount + excluded.amount""",
                tg_id, bdef["resource"], yield_amount,
            )
    state = await get_state(tg_id)
    return {"ok": True, "data": {**state["data"], "tapped": str(yield_amount), "resource": bdef["resource"]}}


async def buy_business_branch(tg_id: int, business_id: str, branch_id: str) -> dict:
    """Spend cash + (optionally) the business's own resource to level a branch."""
    branches = cfg.business_tree().get(business_id, [])
    branch_def = next((b for b in branches if b["id"] == branch_id), None)
    if not branch_def:
        return {"ok": False, "error": "unknown_branch"}
    bdef = _business_def(business_id)
    if not bdef:
        return {"ok": False, "error": "unknown_business"}
    now = _now()
    async with pool().acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow(
                "select cash, max_level from clicker_users where tg_id = $1 for update", tg_id,
            )
            if not user:
                return {"ok": False, "error": "no_user"}
            if int(user["max_level"]) < int(bdef["unlock_level"]):
                return {"ok": False, "error": "locked"}

            slot_id = f"bt_{business_id}_{branch_id}"
            cur = await conn.fetchrow(
                """select level from clicker_upgrades
                   where tg_id = $1 and kind = 'business_branch' and slot_id = $2 for update""",
                tg_id, slot_id,
            )
            cur_level = int(cur["level"]) if cur else 0
            if cur_level >= int(branch_def.get("max_level", 10)):
                return {"ok": False, "error": "max_level"}

            base_cost = Decimal(str(branch_def["base_cost"]))
            growth = Decimal("1.20") ** cur_level
            cost = (base_cost * growth).quantize(Decimal("1"))
            if Decimal(user["cash"]) < cost:
                return {"ok": False, "error": "not_enough_cash", "needed": str(cost)}

            # Multi-resource: cost_resources dict (preferred), legacy cost_resource+cost_per_level still works.
            res_costs: dict[str, Decimal] = {}
            cr_dict = branch_def.get("cost_resources") or {}
            if cr_dict:
                for res, base_amt in cr_dict.items():
                    res_costs[res] = (Decimal(str(base_amt)) * growth).quantize(Decimal("1"))
            else:
                legacy_res = branch_def.get("cost_resource")
                if legacy_res:
                    cost_pl = branch_def.get("cost_per_level", 0)
                    res_costs[legacy_res] = (Decimal(str(cost_pl)) * growth).quantize(Decimal("1"))

            if res_costs:
                res_rows = await conn.fetch(
                    "select resource_type, amount from clicker_resources where tg_id = $1 and resource_type = any($2::text[]) for update",
                    tg_id, list(res_costs.keys()),
                )
                have = {r["resource_type"]: Decimal(r["amount"]) for r in res_rows}
                for res, amt in res_costs.items():
                    if have.get(res, Decimal(0)) < amt:
                        return {
                            "ok": False, "error": "not_enough_resource",
                            "resource": res, "needed": str(amt), "have": str(have.get(res, Decimal(0))),
                        }

            # Accrue current rate before bumping level — so new bonus only applies forward.
            await _accrue_business_idle(conn, tg_id, business_id, now)

            new_level = cur_level + 1
            if cur:
                await conn.execute(
                    """update clicker_upgrades set level = $3
                       where tg_id = $1 and kind = 'business_branch' and slot_id = $2""",
                    tg_id, slot_id, new_level,
                )
            else:
                await conn.execute(
                    """insert into clicker_upgrades (tg_id, kind, slot_id, level)
                       values ($1, 'business_branch', $2, $3)""",
                    tg_id, slot_id, new_level,
                )
            await conn.execute("update clicker_users set cash = cash - $2 where tg_id = $1", tg_id, cost)
            for res, amt in res_costs.items():
                if amt > 0:
                    await conn.execute(
                        "update clicker_resources set amount = amount - $3 where tg_id = $1 and resource_type = $2",
                        tg_id, res, amt,
                    )
            await _log(conn, tg_id, "business_branch_bought", {
                "business_id": business_id, "branch_id": branch_id,
                "from": cur_level, "to": new_level, "cost": str(cost),
                "res_cost": {k: str(v) for k, v in res_costs.items()},
            })
            return await _wrap_state(conn, tg_id, {
                "business_id": business_id, "branch_id": branch_id, "new_level": new_level,
                "spent_cash": str(cost),
                "spent_resources": {k: str(v) for k, v in res_costs.items()},
            })


async def business_collect(tg_id: int, business_id: str | None = None) -> dict:
    """Collect pending idle from one or all businesses. None → all."""
    now = _now()
    targets: list[str] = []
    if business_id:
        targets = [business_id]
    else:
        targets = [b["id"] for b in cfg.businesses()]

    async with pool().acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow(
                "select max_level from clicker_users where tg_id = $1 for update", tg_id,
            )
            if not user:
                return {"ok": False, "error": "no_user"}
            user_max = int(user["max_level"])

            collected: dict[str, Decimal] = {}
            for bid in targets:
                bdef = _business_def(bid)
                if not bdef:
                    continue
                if user_max < int(bdef["unlock_level"]):
                    continue
                pending = await _accrue_business_idle(conn, tg_id, bid, now)
                if pending <= 0:
                    continue
                # Drop fractional dust below 1 — collect floor.
                amount = Decimal(int(pending))
                if amount <= 0:
                    continue
                rest = pending - amount
                await conn.execute(
                    """update clicker_businesses set pending_amount = $3
                       where tg_id = $1 and business_id = $2""",
                    tg_id, bid, rest,
                )
                await conn.execute(
                    """insert into clicker_resources (tg_id, resource_type, amount) values ($1, $2, $3)
                       on conflict (tg_id, resource_type) do update set amount = clicker_resources.amount + excluded.amount""",
                    tg_id, bdef["resource"], amount,
                )
                collected[bdef["resource"]] = collected.get(bdef["resource"], Decimal(0)) + amount

    state = await get_state(tg_id)
    return {"ok": True, "data": {**state["data"], "collected": {k: str(v) for k, v in collected.items()}}}


def _business_resource_cost(bdef: dict, current_level: int) -> dict[str, Decimal]:
    """Resource cost scales gently with level (1.10^level)."""
    base = bdef.get("upgrade_resource_cost") or {}
    growth = Decimal("1.10") ** current_level
    return {res: (Decimal(str(amt)) * growth).quantize(Decimal("1")) for res, amt in base.items()}


async def business_upgrade(tg_id: int, business_id: str) -> dict:
    bdef = _business_def(business_id)
    if not bdef:
        return {"ok": False, "error": "unknown_business"}
    now = _now()
    async with pool().acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow(
                "select cash, max_level from clicker_users where tg_id = $1 for update", tg_id,
            )
            if not user:
                return {"ok": False, "error": "no_user"}
            if int(user["max_level"]) < int(bdef["unlock_level"]):
                return {"ok": False, "error": "locked", "unlock_level": int(bdef["unlock_level"])}
            level = await _business_level(conn, tg_id, business_id)
            cost = _business_upgrade_cost(bdef, level)
            if Decimal(user["cash"]) < cost:
                return {"ok": False, "error": "not_enough_cash", "needed": str(cost)}

            # Resource cost check.
            res_cost = _business_resource_cost(bdef, level)
            if res_cost:
                res_rows = await conn.fetch(
                    "select resource_type, amount from clicker_resources where tg_id = $1 for update",
                    tg_id,
                )
                have = {r["resource_type"]: Decimal(r["amount"]) for r in res_rows}
                for res, amt in res_cost.items():
                    if have.get(res, Decimal(0)) < amt:
                        return {
                            "ok": False, "error": "not_enough_resource",
                            "resource": res, "needed": str(amt), "have": str(have.get(res, Decimal(0))),
                        }

            # First, accrue current rate's idle into pending so the upgrade applies forward only.
            await _accrue_business_idle(conn, tg_id, business_id, now)

            await conn.execute(
                """insert into clicker_upgrades (tg_id, kind, slot_id, level) values ($1, 'business', $2, $3)
                   on conflict (tg_id, kind, slot_id) do update set level = excluded.level""",
                tg_id, business_id, level + 1,
            )
            await conn.execute(
                "update clicker_users set cash = cash - $2 where tg_id = $1",
                tg_id, cost,
            )
            for res, amt in res_cost.items():
                await conn.execute(
                    "update clicker_resources set amount = amount - $3 where tg_id = $1 and resource_type = $2",
                    tg_id, res, amt,
                )
            await _log(conn, tg_id, "business_upgrade", {
                "business_id": business_id, "from": level, "to": level + 1, "cost": str(cost),
                "res_cost": {k: str(v) for k, v in res_cost.items()},
            })

    state = await get_state(tg_id)
    return {"ok": True, "data": {**state["data"], "new_level": level + 1, "spent": str(cost),
                                 "res_spent": {k: str(v) for k, v in res_cost.items()}}}


# ---------- public config ---------------------------------------------------


def public_config() -> dict:
    return {
        "version": "0.2.0",
        "weapons": cfg.weapons(),
        "mercs": cfg.mercs(),
        "locations": cfg.locations(),
        "bosses": cfg.bosses(),
        "chests": cfg.chests(),
        "artifacts": cfg.artifacts(),
        "mythics": cfg.mythics(),
        "crit_luck": cfg.crit_luck(),
        "businesses": cfg.businesses(),
        "resources_meta": cfg.resources_meta(),
        "prestige_tree": cfg.prestige_tree(),
        "business_tree": cfg.business_tree(),
        "permits": cfg.permits(),
        "constants": {
            "level_time_normal": cfg.LEVEL_TIME_NORMAL,
            "level_time_boss": cfg.LEVEL_TIME_BOSS,
            "hp_base": cfg.HP_BASE,
            "hp_growth": cfg.HP_GROWTH,
            "hp_boss_mult": cfg.HP_BOSS_MULT,
            "coin_drop_ratio": cfg.COIN_DROP_RATIO,
            "boss_coin_mult": cfg.BOSS_COIN_MULT,
            "cost_growth": cfg.COST_GROWTH,
            "res_cost_growth": cfg.RES_COST_GROWTH,
            "damage_per_level": cfg.DAMAGE_PER_LEVEL,
            "checkpoint_every": cfg.CHECKPOINT_EVERY,
            "business_idle_cap_hours": cfg.BUSINESS_IDLE_CAP_HOURS,
        },
    }


# ---------- log -------------------------------------------------------------


async def _log(conn, tg_id: int, event_type: str, data: dict) -> None:
    try:
        await conn.execute(
            "insert into clicker_event_log (tg_id, event_type, data) values ($1, $2, $3::jsonb)",
            tg_id, event_type, json.dumps(data, default=str),
        )
    except Exception:
        log.exception("clicker event log failed")


# ---------- leaderboards ----------------------------------------------------


async def leaderboard(metric: str, limit: int = 50) -> list[dict]:
    col_map = {
        "level": "max_level",
        "cash": "cash",
        "casecoins": "casecoins",
        "glory": "glory",
        "prestige": "prestige_count",
        "bosses": "bosses_killed",
    }
    col = col_map.get(metric, "max_level")
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"""select tg_id, first_name, username, {col} as score, max_level, prestige_count
                from clicker_users where banned = false
                order by {col} desc nulls last limit $1""",
            int(limit),
        )
    return [
        {
            "tg_id": int(r["tg_id"]),
            "first_name": r["first_name"],
            "username": r["username"],
            "score": str(r["score"]) if r["score"] is not None else "0",
            "max_level": int(r["max_level"]),
            "prestige_count": int(r["prestige_count"]),
        }
        for r in rows
    ]
