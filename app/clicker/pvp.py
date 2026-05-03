"""PvP: raids on businesses + async duels by DPS."""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.db.client import pool
from app.clicker import config_loader as cfg

log = logging.getLogger(__name__)


RAID_UNLOCK_LEVEL = 30
RAID_COST_CASH = Decimal(100_000)
RAID_COOLDOWN_HOURS = 24
RAID_BASE_SUCCESS = Decimal("0.70")
RAID_MIN_SUCCESS = Decimal("0.10")
RAID_STEAL_FRACTION = Decimal("0.10")     # of resource produced last 24h
RAID_STEAL_BALANCE_CAP_PCT = Decimal(25)  # never steal more than 25% of victim's current resource
RAID_DAILY_LIMIT = 3                       # max successful or attempted raids per day per raider

DUEL_UNLOCK_LEVEL = 15
DUEL_COOLDOWN_MINUTES = 60
DUEL_COMMISSION_PCT = Decimal(10)
DUEL_STAKE_CAP_PCT = Decimal(25)           # max stake = 25% of min(both players' resource pool)
DUEL_INSTANT_THRESHOLD_CASH = Decimal(100_000)        # > this requires invite + accept
DUEL_INSTANT_THRESHOLD_CASECOINS = Decimal(50)
DUEL_INVITE_LIFETIME_HOURS = 24

# PvP matchmaking range. Players outside the bracket are not visible / cannot be attacked.
PVP_LEVEL_RANGE = 15
PVP_TOP_BRACKET_FLOOR = 100   # everyone at level 100+ is in one shared endgame bracket
IMMUNITY_HOURS = 72            # accounts younger than this can't be raided/dueled


def _within_pvp_range(my_level: int, opp_level: int) -> bool:
    """Both endgame (>= 100) → always allowed. Otherwise within ±PVP_LEVEL_RANGE."""
    if my_level >= PVP_TOP_BRACKET_FLOOR and opp_level >= PVP_TOP_BRACKET_FLOOR:
        return True
    return abs(int(my_level) - int(opp_level)) <= PVP_LEVEL_RANGE


def _bracket_bounds(my_level: int) -> tuple[int, int]:
    """Visible target level window for the requester."""
    if my_level >= PVP_TOP_BRACKET_FLOOR:
        return (PVP_TOP_BRACKET_FLOOR, 9999)
    return (max(1, my_level - PVP_LEVEL_RANGE), my_level + PVP_LEVEL_RANGE)


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


# ---------- targets list ----------------------------------------------------


async def list_targets(self_tg_id: int, limit: int = 30) -> list[dict]:
    """Targets within the requester's PvP bracket only. Excludes immune (new) accounts."""
    async with pool().acquire() as conn:
        me = await conn.fetchrow(
            "select max_level from clicker_users where tg_id = $1", self_tg_id,
        )
        if not me:
            return []
        my_level = int(me["max_level"])
        lo, hi = _bracket_bounds(my_level)
        immunity_cutoff = _now() - timedelta(hours=IMMUNITY_HOURS)
        rows = await conn.fetch(
            """select tg_id, first_name, username, max_level, prestige_count,
                      cash, casecoins, click_damage, auto_dps, crit_chance,
                      crit_multiplier, created_at
               from clicker_users
               where tg_id != $1 and banned = false
                 and max_level >= 5
                 and max_level >= $2 and max_level <= $3
                 and created_at <= $4
               order by max_level desc, prestige_count desc
               limit $5""",
            self_tg_id, int(lo), int(hi), immunity_cutoff, int(limit),
        )
    out = []
    for r in rows:
        out.append({
            "tg_id": int(r["tg_id"]),
            "first_name": r["first_name"],
            "username": r["username"],
            "max_level": int(r["max_level"]),
            "prestige_count": int(r["prestige_count"]),
            "cash": str(r["cash"]),
            "casecoins": str(r["casecoins"]),
            "click_damage": str(r["click_damage"]),
            "auto_dps": str(r["auto_dps"]),
            "crit_chance": str(r["crit_chance"]),
            "crit_multiplier": str(r["crit_multiplier"]),
        })
    return out


async def bracket_info(self_tg_id: int) -> dict:
    """Returns the player's PvP bracket bounds + immunity/limit info for the UI."""
    async with pool().acquire() as conn:
        me = await conn.fetchrow(
            "select max_level, created_at from clicker_users where tg_id = $1", self_tg_id,
        )
        if not me:
            return {}
        my_level = int(me["max_level"])
        lo, hi = _bracket_bounds(my_level)
        # Today's raid count (last 24h).
        cutoff = _now() - timedelta(hours=24)
        cnt_row = await conn.fetchrow(
            """select count(*) as n from clicker_raids
               where raider_tg_id = $1 and started_at >= $2""",
            self_tg_id, cutoff,
        )
        raids_today = int(cnt_row["n"]) if cnt_row else 0
        # Immunity status (own).
        my_created = me["created_at"]
        if my_created and my_created.tzinfo is None:
            my_created = my_created.replace(tzinfo=timezone.utc)
        immune_until = (my_created + timedelta(hours=IMMUNITY_HOURS)) if my_created else None
        immune = bool(immune_until and immune_until > _now())
    return {
        "my_level": my_level,
        "level_range_min": int(lo),
        "level_range_max": int(hi),
        "is_top_bracket": my_level >= PVP_TOP_BRACKET_FLOOR,
        "raids_today": raids_today,
        "raid_daily_limit": RAID_DAILY_LIMIT,
        "raid_cost_cash": str(RAID_COST_CASH),
        "raid_unlock_level": RAID_UNLOCK_LEVEL,
        "duel_unlock_level": DUEL_UNLOCK_LEVEL,
        "duel_stake_cap_pct": str(DUEL_STAKE_CAP_PCT),
        "duel_invite_threshold_cash": str(DUEL_INSTANT_THRESHOLD_CASH),
        "duel_invite_threshold_casecoins": str(DUEL_INSTANT_THRESHOLD_CASECOINS),
        "immunity_hours": IMMUNITY_HOURS,
        "immune_until": immune_until.isoformat() if immune_until else None,
        "immune_now": immune,
    }


# ---------- raid ------------------------------------------------------------


async def _raid_defense_pct(conn, victim_tg_id: int) -> Decimal:
    """Sum raid defense from prestige + business branches + artifacts."""
    # Prestige
    pt_rows = await conn.fetch(
        """select slot_id, level from clicker_upgrades
           where tg_id = $1 and kind = 'prestige_node'""",
        victim_tg_id,
    )
    total = Decimal(0)
    pt_index = {n["id"]: n for n in cfg.prestige_tree()}
    for r in pt_rows:
        node = pt_index.get(r["slot_id"])
        if not node:
            continue
        eff = node.get("effect") or {}
        if "raid_defense_pct" in eff:
            total += Decimal(str(eff["raid_defense_pct"])) * int(r["level"])
    # Business branch raid_def
    biz_rows = await conn.fetch(
        """select slot_id, level from clicker_upgrades
           where tg_id = $1 and kind = 'business_branch'""",
        victim_tg_id,
    )
    biz_tree = cfg.business_tree()
    for r in biz_rows:
        slot = r["slot_id"]
        if not slot.startswith("bt_"):
            continue
        # bt_<businessId>_<branchId>
        rest = slot[3:]
        for biz_id, branches in biz_tree.items():
            prefix = f"{biz_id}_"
            if rest.startswith(prefix):
                branch_id = rest[len(prefix):]
                bd = next((b for b in branches if b["id"] == branch_id), None)
                if bd and bd.get("effect") == "raid_def_pct":
                    total += Decimal(str(bd.get("per_level", 0))) * int(r["level"])
                break
    # Artifact raid defense (e.g. Шапочка-фольга +15%)
    art_rows = await conn.fetch(
        """select i.item_id, i.item_kind from clicker_inventory i
           where i.tg_id = $1 and i.equipped_slot is not null and i.consumed_at is null""",
        victim_tg_id,
    )
    art_index = {a["id"]: a for a in cfg.artifacts()}
    for r in art_rows:
        if r["item_kind"] != "artifact":
            continue
        short = r["item_id"].replace("artifact_", "", 1)
        a = art_index.get(short)
        if a and "raid_defense_pct" in (a.get("effect") or {}):
            total += Decimal(str(a["effect"]["raid_defense_pct"]))
    return total


async def execute_raid(raider_tg_id: int, victim_tg_id: int, business_id: str) -> dict:
    if raider_tg_id == victim_tg_id:
        return {"ok": False, "error": "self_raid"}
    bdef = next((b for b in cfg.businesses() if b["id"] == business_id), None)
    if not bdef:
        return {"ok": False, "error": "unknown_business"}
    now = _now()
    async with pool().acquire() as conn:
        async with conn.transaction():
            raider = await conn.fetchrow(
                "select * from clicker_users where tg_id = $1 for update", raider_tg_id,
            )
            if not raider:
                return {"ok": False, "error": "no_raider"}
            if int(raider["max_level"]) < RAID_UNLOCK_LEVEL:
                return {"ok": False, "error": "level_locked", "needed": RAID_UNLOCK_LEVEL}
            if Decimal(raider["cash"]) < RAID_COST_CASH:
                return {"ok": False, "error": "not_enough_cash", "needed": str(RAID_COST_CASH)}

            # Daily raid limit (across all victims).
            day_cutoff = now - timedelta(hours=24)
            cnt_row = await conn.fetchrow(
                """select count(*) as n from clicker_raids
                   where raider_tg_id = $1 and started_at >= $2""",
                raider_tg_id, day_cutoff,
            )
            if cnt_row and int(cnt_row["n"]) >= RAID_DAILY_LIMIT:
                return {"ok": False, "error": "daily_limit",
                        "limit": RAID_DAILY_LIMIT, "used": int(cnt_row["n"])}

            victim = await conn.fetchrow(
                "select * from clicker_users where tg_id = $1 for update", victim_tg_id,
            )
            if not victim:
                return {"ok": False, "error": "no_victim"}
            if victim["banned"]:
                return {"ok": False, "error": "victim_banned"}
            if int(victim["max_level"]) < int(bdef["unlock_level"]):
                return {"ok": False, "error": "victim_business_locked"}

            # PvP level bracket.
            if not _within_pvp_range(int(raider["max_level"]), int(victim["max_level"])):
                return {"ok": False, "error": "out_of_range",
                        "my_level": int(raider["max_level"]),
                        "opponent_level": int(victim["max_level"]),
                        "range": PVP_LEVEL_RANGE}

            # Victim immunity (new account).
            v_created = victim["created_at"]
            if v_created and v_created.tzinfo is None:
                v_created = v_created.replace(tzinfo=timezone.utc)
            if v_created and (now - v_created) < timedelta(hours=IMMUNITY_HOURS):
                until = v_created + timedelta(hours=IMMUNITY_HOURS)
                return {"ok": False, "error": "victim_immune",
                        "immune_until": until.isoformat()}

            # Per-pair cooldown: 24h.
            cd_row = await conn.fetchrow(
                """select started_at from clicker_raids
                   where raider_tg_id = $1 and victim_tg_id = $2
                   order by started_at desc limit 1""",
                raider_tg_id, victim_tg_id,
            )
            if cd_row and cd_row["started_at"]:
                last = cd_row["started_at"]
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if (now - last) < timedelta(hours=RAID_COOLDOWN_HOURS):
                    remaining = timedelta(hours=RAID_COOLDOWN_HOURS) - (now - last)
                    return {"ok": False, "error": "cooldown", "seconds_remaining": int(remaining.total_seconds())}

            # Pay cost
            await conn.execute(
                "update clicker_users set cash = cash - $2 where tg_id = $1",
                raider_tg_id, RAID_COST_CASH,
            )

            # Compute success chance
            defense = await _raid_defense_pct(conn, victim_tg_id)
            success_chance = max(RAID_MIN_SUCCESS, RAID_BASE_SUCCESS - defense / Decimal(100))
            success = random.random() < float(success_chance)

            amount_stolen = Decimal(0)
            resource_type: str | None = None
            if success:
                resource_type = bdef["resource"]
                # Estimate "produced last 24h" using current rate × 86400.
                # We cap at the victim's actual on-hand amount of that resource.
                # Get victim's branch bonuses (idle multiplier).
                upg_rows = await conn.fetch(
                    "select kind, slot_id, level from clicker_upgrades where tg_id = $1 and kind = 'business_branch'",
                    victim_tg_id,
                )
                branch_lvls = _branch_levels(upg_rows, business_id)
                branch_pcts = _branch_pcts(business_id, branch_lvls)
                bus_lvl = await _biz_level(conn, victim_tg_id, business_id)
                rate = _idle_rate(bdef, bus_lvl, branch_pcts)
                produced_24h = (rate * Decimal(86400)).quantize(Decimal("1"))
                steal = (produced_24h * RAID_STEAL_FRACTION).quantize(Decimal("1"))
                # Cap by what victim actually has.
                victim_res = await conn.fetchrow(
                    "select amount from clicker_resources where tg_id = $1 and resource_type = $2 for update",
                    victim_tg_id, resource_type,
                )
                victim_have = Decimal(victim_res["amount"]) if victim_res else Decimal(0)
                # Cap by 25% of the victim's current balance — protects victim from
                # being cleaned out in a single raid.
                balance_cap = (victim_have * RAID_STEAL_BALANCE_CAP_PCT / Decimal(100)).quantize(Decimal("1"))
                amount_stolen = min(steal, balance_cap)

                if amount_stolen > 0:
                    await conn.execute(
                        "update clicker_resources set amount = amount - $3 where tg_id = $1 and resource_type = $2",
                        victim_tg_id, resource_type, amount_stolen,
                    )
                    await conn.execute(
                        """insert into clicker_resources (tg_id, resource_type, amount) values ($1, $2, $3)
                           on conflict (tg_id, resource_type) do update set amount = clicker_resources.amount + excluded.amount""",
                        raider_tg_id, resource_type, amount_stolen,
                    )

            await conn.execute(
                """insert into clicker_raids (raider_tg_id, victim_tg_id, business_id, success,
                                              resource_type, amount_stolen, cost_paid, success_chance)
                   values ($1, $2, $3, $4, $5, $6, $7, $8)""",
                raider_tg_id, victim_tg_id, business_id, success,
                resource_type, amount_stolen, RAID_COST_CASH, success_chance,
            )

    return {
        "ok": True,
        "data": {
            "success": success,
            "success_chance": str(success_chance),
            "resource_type": resource_type,
            "amount_stolen": str(amount_stolen),
            "cost_paid": str(RAID_COST_CASH),
        },
    }


# ---------- duel ------------------------------------------------------------


def _is_high_stake(stake_kind: str, stake: Decimal) -> bool:
    if stake_kind == "cash":
        return stake > DUEL_INSTANT_THRESHOLD_CASH
    if stake_kind == "casecoins":
        return stake > DUEL_INSTANT_THRESHOLD_CASECOINS
    return False  # resource stakes are always instant (cap below still applies)


def _stake_cap(challenger_row, opponent_row, stake_kind: str) -> Decimal | None:
    """Return the max stake (25% of the smaller side's pool) for cash/casecoins.
    None means no cap (e.g. resource stakes — caller validates differently)."""
    if stake_kind == "cash":
        smaller = min(Decimal(challenger_row["cash"]), Decimal(opponent_row["cash"]))
    elif stake_kind == "casecoins":
        smaller = min(Decimal(challenger_row["casecoins"]), Decimal(opponent_row["casecoins"]))
    else:
        return None
    return (smaller * DUEL_STAKE_CAP_PCT / Decimal(100)).quantize(Decimal("1"))


async def execute_duel(challenger_tg_id: int, opponent_tg_id: int,
                       stake_kind: str, stake_id: str | None,
                       stake_amount: int | float | str) -> dict:
    if challenger_tg_id == opponent_tg_id:
        return {"ok": False, "error": "self_duel"}
    if stake_kind not in ("cash", "casecoins", "resource"):
        return {"ok": False, "error": "bad_stake"}
    stake = Decimal(str(stake_amount))
    if stake <= 0:
        return {"ok": False, "error": "bad_stake"}

    now = _now()
    async with pool().acquire() as conn:
        async with conn.transaction():
            ch = await conn.fetchrow(
                "select * from clicker_users where tg_id = $1 for update", challenger_tg_id,
            )
            if not ch:
                return {"ok": False, "error": "no_challenger"}
            if int(ch["max_level"]) < DUEL_UNLOCK_LEVEL:
                return {"ok": False, "error": "level_locked", "needed": DUEL_UNLOCK_LEVEL}

            op = await conn.fetchrow(
                "select * from clicker_users where tg_id = $1 for update", opponent_tg_id,
            )
            if not op:
                return {"ok": False, "error": "no_opponent"}
            if op["banned"]:
                return {"ok": False, "error": "opponent_banned"}

            # PvP level bracket.
            if not _within_pvp_range(int(ch["max_level"]), int(op["max_level"])):
                return {"ok": False, "error": "out_of_range",
                        "my_level": int(ch["max_level"]),
                        "opponent_level": int(op["max_level"]),
                        "range": PVP_LEVEL_RANGE}

            # Opponent immunity.
            op_created = op["created_at"]
            if op_created and op_created.tzinfo is None:
                op_created = op_created.replace(tzinfo=timezone.utc)
            if op_created and (now - op_created) < timedelta(hours=IMMUNITY_HOURS):
                until = op_created + timedelta(hours=IMMUNITY_HOURS)
                return {"ok": False, "error": "opponent_immune",
                        "immune_until": until.isoformat()}

            # High-stake duels need a mutual-consent invite — instant duel rejected.
            if _is_high_stake(stake_kind, stake):
                return {"ok": False, "error": "needs_invite",
                        "instant_threshold_cash": str(DUEL_INSTANT_THRESHOLD_CASH),
                        "instant_threshold_casecoins": str(DUEL_INSTANT_THRESHOLD_CASECOINS)}

            # Stake cap: 25% of the smaller side's pool. Prevents a rich attacker from
            # forcing a small player into a trivial-loss position.
            cap = _stake_cap(ch, op, stake_kind)
            if cap is not None and stake > cap:
                return {"ok": False, "error": "stake_too_high",
                        "max_stake": str(cap),
                        "cap_pct": str(DUEL_STAKE_CAP_PCT)}

            # Cooldown
            cd_row = await conn.fetchrow(
                """select started_at from clicker_duels
                   where challenger_tg_id = $1 and challenged_tg_id = $2
                   order by started_at desc limit 1""",
                challenger_tg_id, opponent_tg_id,
            )
            if cd_row and cd_row["started_at"]:
                last = cd_row["started_at"]
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if (now - last) < timedelta(minutes=DUEL_COOLDOWN_MINUTES):
                    remaining = timedelta(minutes=DUEL_COOLDOWN_MINUTES) - (now - last)
                    return {"ok": False, "error": "cooldown", "seconds_remaining": int(remaining.total_seconds())}

            # Take stake from challenger.
            ok, err = await _deduct_stake(conn, challenger_tg_id, stake_kind, stake_id, stake)
            if not ok:
                return {"ok": False, "error": err}

            # Both must be able to afford if they LOSE the same stake.
            ok2, err2 = await _deduct_stake(conn, opponent_tg_id, stake_kind, stake_id, stake)
            if not ok2:
                await _refund_stake(conn, challenger_tg_id, stake_kind, stake_id, stake)
                return {"ok": False, "error": "opponent_" + err2}

            # Score: time to kill a 1B HP target with both stats. Lower TTK wins.
            score_ch = _duel_score(ch)
            score_op = _duel_score(op)

            if score_ch >= score_op:
                winner = challenger_tg_id
            else:
                winner = opponent_tg_id

            # Pot = 2 × stake. Commission 10% burned. Winner gets 1.8 × stake.
            commission = (stake * Decimal(2) * DUEL_COMMISSION_PCT / Decimal(100)).quantize(Decimal("1"))
            payout = (stake * Decimal(2)) - commission

            await _refund_stake(conn, winner, stake_kind, stake_id, payout)

            await conn.execute(
                """insert into clicker_duels
                   (challenger_tg_id, challenged_tg_id, stake_kind, stake_id, stake_amount,
                    challenger_score, challenged_score, winner_tg_id, commission_paid)
                   values ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                challenger_tg_id, opponent_tg_id, stake_kind, stake_id, stake,
                str(score_ch), str(score_op), winner, commission,
            )

    return {
        "ok": True,
        "data": {
            "winner_tg_id": winner,
            "challenger_score": str(score_ch),
            "challenged_score": str(score_op),
            "stake": str(stake),
            "payout": str(payout),
            "commission_paid": str(commission),
        },
    }


def _duel_score(user_row) -> Decimal:
    """Higher = stronger. Models DPS from click + auto + crit expectation."""
    click = Decimal(user_row["click_damage"])
    auto = Decimal(user_row["auto_dps"])
    crit_chance = Decimal(user_row["crit_chance"]) / Decimal(100)
    crit_mult = Decimal(user_row["crit_multiplier"])
    # Effective click DPS assuming 5 taps/sec
    tap_per_sec = Decimal(5)
    eff_click = click * tap_per_sec * (Decimal(1) + crit_chance * (crit_mult - Decimal(1)))
    return eff_click + auto


async def _deduct_stake(conn, tg_id: int, kind: str, item_id: str | None, amount: Decimal) -> tuple[bool, str]:
    if kind == "cash":
        row = await conn.fetchrow("select cash from clicker_users where tg_id = $1 for update", tg_id)
        if not row or Decimal(row["cash"]) < amount:
            return False, "not_enough_cash"
        await conn.execute("update clicker_users set cash = cash - $2 where tg_id = $1", tg_id, amount)
    elif kind == "casecoins":
        row = await conn.fetchrow("select casecoins from clicker_users where tg_id = $1 for update", tg_id)
        if not row or Decimal(row["casecoins"]) < amount:
            return False, "not_enough_casecoins"
        await conn.execute("update clicker_users set casecoins = casecoins - $2 where tg_id = $1", tg_id, amount)
    elif kind == "resource":
        if not item_id:
            return False, "missing_resource_type"
        row = await conn.fetchrow(
            "select amount from clicker_resources where tg_id = $1 and resource_type = $2 for update",
            tg_id, item_id,
        )
        if not row or Decimal(row["amount"]) < amount:
            return False, "not_enough_resource"
        await conn.execute(
            "update clicker_resources set amount = amount - $3 where tg_id = $1 and resource_type = $2",
            tg_id, item_id, amount,
        )
    else:
        return False, "bad_stake_kind"
    return True, ""


async def _refund_stake(conn, tg_id: int, kind: str, item_id: str | None, amount: Decimal) -> None:
    if amount <= 0:
        return
    if kind == "cash":
        await conn.execute("update clicker_users set cash = cash + $2 where tg_id = $1", tg_id, amount)
    elif kind == "casecoins":
        await conn.execute("update clicker_users set casecoins = casecoins + $2 where tg_id = $1", tg_id, amount)
    elif kind == "resource" and item_id:
        await conn.execute(
            """insert into clicker_resources (tg_id, resource_type, amount) values ($1, $2, $3)
               on conflict (tg_id, resource_type) do update set amount = clicker_resources.amount + excluded.amount""",
            tg_id, item_id, amount,
        )


# ---------- duel invites (high-stake mutual-consent flow) -------------------


async def create_duel_invite(challenger_tg_id: int, opponent_tg_id: int,
                              stake_kind: str, stake_id: str | None,
                              stake_amount: int | float | str) -> dict:
    """Send a high-stake duel invite. Stake is escrowed from challenger immediately.
    Opponent has DUEL_INVITE_LIFETIME_HOURS to accept; on accept the duel runs and
    payouts apply. On decline / expiry, escrow is refunded."""
    if challenger_tg_id == opponent_tg_id:
        return {"ok": False, "error": "self_duel"}
    if stake_kind not in ("cash", "casecoins", "resource"):
        return {"ok": False, "error": "bad_stake"}
    stake = Decimal(str(stake_amount))
    if stake <= 0:
        return {"ok": False, "error": "bad_stake"}

    now = _now()
    async with pool().acquire() as conn:
        async with conn.transaction():
            ch = await conn.fetchrow(
                "select * from clicker_users where tg_id = $1 for update", challenger_tg_id,
            )
            if not ch:
                return {"ok": False, "error": "no_challenger"}
            if int(ch["max_level"]) < DUEL_UNLOCK_LEVEL:
                return {"ok": False, "error": "level_locked", "needed": DUEL_UNLOCK_LEVEL}
            op = await conn.fetchrow(
                "select * from clicker_users where tg_id = $1", opponent_tg_id,
            )
            if not op:
                return {"ok": False, "error": "no_opponent"}
            if op["banned"]:
                return {"ok": False, "error": "opponent_banned"}

            if not _within_pvp_range(int(ch["max_level"]), int(op["max_level"])):
                return {"ok": False, "error": "out_of_range",
                        "my_level": int(ch["max_level"]),
                        "opponent_level": int(op["max_level"]),
                        "range": PVP_LEVEL_RANGE}

            op_created = op["created_at"]
            if op_created and op_created.tzinfo is None:
                op_created = op_created.replace(tzinfo=timezone.utc)
            if op_created and (now - op_created) < timedelta(hours=IMMUNITY_HOURS):
                until = op_created + timedelta(hours=IMMUNITY_HOURS)
                return {"ok": False, "error": "opponent_immune",
                        "immune_until": until.isoformat()}

            cap = _stake_cap(ch, op, stake_kind)
            if cap is not None and stake > cap:
                return {"ok": False, "error": "stake_too_high",
                        "max_stake": str(cap),
                        "cap_pct": str(DUEL_STAKE_CAP_PCT)}

            # No duplicate pending invite to the same opponent.
            dup = await conn.fetchrow(
                """select id from clicker_duel_invites
                   where challenger_tg_id = $1 and challenged_tg_id = $2 and status = 'pending'""",
                challenger_tg_id, opponent_tg_id,
            )
            if dup:
                return {"ok": False, "error": "duplicate_invite"}

            # Escrow stake from challenger.
            ok, err = await _deduct_stake(conn, challenger_tg_id, stake_kind, stake_id, stake)
            if not ok:
                return {"ok": False, "error": err}

            expires_at = now + timedelta(hours=DUEL_INVITE_LIFETIME_HOURS)
            row = await conn.fetchrow(
                """insert into clicker_duel_invites
                    (challenger_tg_id, challenged_tg_id, stake_kind, stake_id, stake_amount,
                     status, expires_at)
                   values ($1, $2, $3, $4, $5, 'pending', $6)
                   returning id""",
                challenger_tg_id, opponent_tg_id, stake_kind, stake_id, stake, expires_at,
            )
            return {"ok": True, "data": {
                "invite_id": int(row["id"]),
                "expires_at": expires_at.isoformat(),
                "stake_escrowed": str(stake),
            }}


async def respond_duel_invite(opponent_tg_id: int, invite_id: int, accept: bool) -> dict:
    """Accept → run the duel synchronously (both stakes escrowed, payout to winner).
    Decline → refund challenger's escrow."""
    now = _now()
    async with pool().acquire() as conn:
        async with conn.transaction():
            inv = await conn.fetchrow(
                "select * from clicker_duel_invites where id = $1 for update", invite_id,
            )
            if not inv:
                return {"ok": False, "error": "invite_not_found"}
            if int(inv["challenged_tg_id"]) != opponent_tg_id:
                return {"ok": False, "error": "not_recipient"}
            if inv["status"] != "pending":
                return {"ok": False, "error": "invite_inactive"}

            exp = inv["expires_at"]
            if exp and exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp and exp <= now:
                # Auto-expire: refund.
                await _refund_stake(conn, int(inv["challenger_tg_id"]),
                                    inv["stake_kind"], inv["stake_id"], Decimal(inv["stake_amount"]))
                await conn.execute(
                    "update clicker_duel_invites set status = 'expired', responded_at = $2 where id = $1",
                    invite_id, now,
                )
                return {"ok": False, "error": "invite_expired"}

            challenger_id = int(inv["challenger_tg_id"])
            stake_kind = inv["stake_kind"]
            stake_id = inv["stake_id"]
            stake = Decimal(inv["stake_amount"])

            if not accept:
                await _refund_stake(conn, challenger_id, stake_kind, stake_id, stake)
                await conn.execute(
                    "update clicker_duel_invites set status = 'declined', responded_at = $2 where id = $1",
                    invite_id, now,
                )
                return {"ok": True, "data": {"declined": True, "invite_id": invite_id}}

            # Accept → run the duel right here, mirroring execute_duel's payout logic.
            ch = await conn.fetchrow(
                "select * from clicker_users where tg_id = $1 for update", challenger_id,
            )
            op = await conn.fetchrow(
                "select * from clicker_users where tg_id = $1 for update", opponent_tg_id,
            )
            if not ch or not op:
                # Refund on broken state.
                await _refund_stake(conn, challenger_id, stake_kind, stake_id, stake)
                await conn.execute(
                    "update clicker_duel_invites set status = 'expired', responded_at = $2 where id = $1",
                    invite_id, now,
                )
                return {"ok": False, "error": "user_missing"}

            # Opponent must be able to match the stake.
            ok2, err2 = await _deduct_stake(conn, opponent_tg_id, stake_kind, stake_id, stake)
            if not ok2:
                await _refund_stake(conn, challenger_id, stake_kind, stake_id, stake)
                await conn.execute(
                    "update clicker_duel_invites set status = 'declined', responded_at = $2 where id = $1",
                    invite_id, now,
                )
                return {"ok": False, "error": "self_" + err2}

            score_ch = _duel_score(ch)
            score_op = _duel_score(op)
            winner = challenger_id if score_ch >= score_op else opponent_tg_id
            commission = (stake * Decimal(2) * DUEL_COMMISSION_PCT / Decimal(100)).quantize(Decimal("1"))
            payout = (stake * Decimal(2)) - commission
            await _refund_stake(conn, winner, stake_kind, stake_id, payout)

            duel_row = await conn.fetchrow(
                """insert into clicker_duels
                   (challenger_tg_id, challenged_tg_id, stake_kind, stake_id, stake_amount,
                    challenger_score, challenged_score, winner_tg_id, commission_paid)
                   values ($1, $2, $3, $4, $5, $6, $7, $8, $9) returning id""",
                challenger_id, opponent_tg_id, stake_kind, stake_id, stake,
                str(score_ch), str(score_op), winner, commission,
            )
            await conn.execute(
                """update clicker_duel_invites set status = 'accepted', responded_at = $2, duel_id = $3
                   where id = $1""",
                invite_id, now, int(duel_row["id"]),
            )

    return {"ok": True, "data": {
        "accepted": True,
        "invite_id": invite_id,
        "duel_id": int(duel_row["id"]),
        "winner_tg_id": winner,
        "self_won": winner == opponent_tg_id,
        "challenger_score": str(score_ch),
        "challenged_score": str(score_op),
        "stake": str(stake),
        "payout": str(payout),
        "commission_paid": str(commission),
    }}


async def cancel_duel_invite(challenger_tg_id: int, invite_id: int) -> dict:
    async with pool().acquire() as conn:
        async with conn.transaction():
            inv = await conn.fetchrow(
                "select * from clicker_duel_invites where id = $1 for update", invite_id,
            )
            if not inv:
                return {"ok": False, "error": "invite_not_found"}
            if int(inv["challenger_tg_id"]) != challenger_tg_id:
                return {"ok": False, "error": "not_owner"}
            if inv["status"] != "pending":
                return {"ok": False, "error": "invite_inactive"}
            await _refund_stake(conn, challenger_tg_id, inv["stake_kind"], inv["stake_id"], Decimal(inv["stake_amount"]))
            await conn.execute(
                "update clicker_duel_invites set status = 'cancelled', responded_at = $2 where id = $1",
                invite_id, _now(),
            )
    return {"ok": True, "data": {"invite_id": invite_id, "cancelled": True}}


def _serialize_invite(r) -> dict:
    return {
        "id": int(r["id"]),
        "challenger_tg_id": int(r["challenger_tg_id"]),
        "challenged_tg_id": int(r["challenged_tg_id"]),
        "stake_kind": r["stake_kind"],
        "stake_id": r["stake_id"],
        "stake_amount": str(r["stake_amount"]),
        "status": r["status"],
        "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
    }


async def list_invites_received(tg_id: int) -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """select i.*, u.first_name as challenger_name from clicker_duel_invites i
               left join clicker_users u on u.tg_id = i.challenger_tg_id
               where i.challenged_tg_id = $1 and i.status = 'pending'
                 and i.expires_at > now()
               order by i.created_at desc limit 50""",
            tg_id,
        )
    return [{**_serialize_invite(r), "challenger_name": r["challenger_name"]} for r in rows]


async def list_invites_sent(tg_id: int) -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """select i.*, u.first_name as challenged_name from clicker_duel_invites i
               left join clicker_users u on u.tg_id = i.challenged_tg_id
               where i.challenger_tg_id = $1
               order by i.created_at desc limit 50""",
            tg_id,
        )
    return [{**_serialize_invite(r), "challenged_name": r["challenged_name"]} for r in rows]


# ---------- history ---------------------------------------------------------


async def history(tg_id: int, limit: int = 50) -> dict:
    async with pool().acquire() as conn:
        raids = await conn.fetch(
            """select r.*, u.first_name as victim_name from clicker_raids r
               left join clicker_users u on u.tg_id = r.victim_tg_id
               where r.raider_tg_id = $1 or r.victim_tg_id = $1
               order by r.started_at desc limit $2""",
            tg_id, int(limit),
        )
        duels = await conn.fetch(
            """select d.*, u1.first_name as challenger_name, u2.first_name as challenged_name
               from clicker_duels d
               left join clicker_users u1 on u1.tg_id = d.challenger_tg_id
               left join clicker_users u2 on u2.tg_id = d.challenged_tg_id
               where d.challenger_tg_id = $1 or d.challenged_tg_id = $1
               order by d.started_at desc limit $2""",
            tg_id, int(limit),
        )
    return {
        "raids": [
            {
                "id": int(r["id"]),
                "raider_tg_id": int(r["raider_tg_id"]),
                "victim_tg_id": int(r["victim_tg_id"]),
                "victim_name": r["victim_name"],
                "business_id": r["business_id"],
                "success": bool(r["success"]) if r["success"] is not None else None,
                "resource_type": r["resource_type"],
                "amount_stolen": str(r["amount_stolen"]),
                "cost_paid": str(r["cost_paid"]),
                "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                "self_was_raider": int(r["raider_tg_id"]) == tg_id,
            }
            for r in raids
        ],
        "duels": [
            {
                "id": int(d["id"]),
                "challenger_tg_id": int(d["challenger_tg_id"]),
                "challenged_tg_id": int(d["challenged_tg_id"]),
                "challenger_name": d["challenger_name"],
                "challenged_name": d["challenged_name"],
                "stake_kind": d["stake_kind"],
                "stake_id": d["stake_id"],
                "stake_amount": str(d["stake_amount"]),
                "challenger_score": str(d["challenger_score"]) if d["challenger_score"] else None,
                "challenged_score": str(d["challenged_score"]) if d["challenged_score"] else None,
                "winner_tg_id": int(d["winner_tg_id"]) if d["winner_tg_id"] else None,
                "started_at": d["started_at"].isoformat() if d["started_at"] else None,
                "self_won": int(d["winner_tg_id"]) == tg_id if d["winner_tg_id"] else False,
            }
            for d in duels
        ],
    }


# ---------- helpers (mirror of game.py business funcs to avoid circular import)


def _branch_levels(upg_rows, business_id: str) -> dict[str, int]:
    out: dict[str, int] = {}
    prefix = f"bt_{business_id}_"
    for u in upg_rows:
        if u["kind"] == "business_branch" and u["slot_id"].startswith(prefix):
            branch = u["slot_id"][len(prefix):]
            out[branch] = int(u["level"])
    return out


def _branch_pcts(business_id: str, branch_levels: dict[str, int]) -> dict[str, float]:
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


async def _biz_level(conn, tg_id: int, business_id: str) -> int:
    row = await conn.fetchrow(
        """select level from clicker_upgrades where tg_id = $1 and kind = 'business' and slot_id = $2""",
        tg_id, business_id,
    )
    return int(row["level"]) if row else 0


def _idle_rate(bdef: dict, level: int, branch_pcts: dict[str, float]) -> Decimal:
    base = Decimal(str(bdef["base_idle_per_sec"]))
    rate = base * (Decimal(str(cfg.BUSINESS_IDLE_GROWTH)) ** level)
    bonus = Decimal(str(branch_pcts.get("idle_pct", 0))) + Decimal(str(branch_pcts.get("all_yield_pct", 0)))
    return rate * (Decimal(1) + bonus / Decimal(100))
