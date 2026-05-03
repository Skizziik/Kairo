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

DUEL_UNLOCK_LEVEL = 15
DUEL_COOLDOWN_MINUTES = 60
DUEL_COMMISSION_PCT = Decimal(10)


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
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """select tg_id, first_name, username, max_level, prestige_count,
                      cash, casecoins, click_damage, auto_dps, crit_chance,
                      crit_multiplier
               from clicker_users
               where tg_id != $1 and banned = false and max_level >= 5
               order by max_level desc, prestige_count desc
               limit $2""",
            self_tg_id, int(limit),
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

            victim = await conn.fetchrow(
                "select * from clicker_users where tg_id = $1 for update", victim_tg_id,
            )
            if not victim:
                return {"ok": False, "error": "no_victim"}
            if victim["banned"]:
                return {"ok": False, "error": "victim_banned"}
            if int(victim["max_level"]) < int(bdef["unlock_level"]):
                return {"ok": False, "error": "victim_business_locked"}

            # Cooldown: same pair within 24h.
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
                amount_stolen = min(steal, victim_have)

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
            # We check opponent ahead of time so they don't get into a free-loss situation.
            ok2, err2 = await _deduct_stake(conn, opponent_tg_id, stake_kind, stake_id, stake)
            if not ok2:
                # Refund challenger.
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
