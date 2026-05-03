"""P2P Marketplace logic. Resource ↔ resource, $ ↔ resource, artifact ↔ anything."""
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


LOT_LIFETIME_HOURS = 48
COMMISSION_PCT = Decimal("5")
COMMISSION_MIN_CASH = Decimal(100)
MAX_ACTIVE_LOTS = 5
OFFER_LIFETIME_HOURS = 24

VALID_KINDS = {"resource", "artifact", "cash", "casecoins"}


async def _max_active_lots(conn, tg_id: int) -> int:
    """Base + max_lots_bonus from equipped legendary artifact (Бойл-чек)."""
    rows = await conn.fetch(
        """select i.item_kind, i.item_id from clicker_inventory i
           where i.tg_id = $1 and i.equipped_slot is not null and i.consumed_at is null""",
        tg_id,
    )
    art_index = {a["id"]: a for a in cfg.artifacts()}
    bonus = 0
    for r in rows:
        if r["item_kind"] != "artifact":
            continue
        short = r["item_id"].replace("artifact_", "", 1)
        spec = art_index.get(short) or {}
        eff = spec.get("effect") or {}
        v = eff.get("max_lots_bonus")
        if isinstance(v, (int, float)):
            bonus += int(v)
    return MAX_ACTIVE_LOTS + bonus


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


def _validate_kind(kind: str, item_id: str | None, amount: Decimal) -> tuple[bool, str]:
    if kind not in VALID_KINDS:
        return False, "unknown_kind"
    if amount <= 0:
        return False, "bad_amount"
    if kind == "resource":
        if not item_id:
            return False, "missing_resource_type"
        if item_id not in cfg.resources_meta():
            return False, "unknown_resource"
    if kind == "artifact":
        if not item_id:
            return False, "missing_artifact_id"
    return True, ""


# ---------- create lot ------------------------------------------------------


async def create_lot(
    tg_id: int,
    offer_kind: str,
    offer_id: str | None,
    offer_amount: int | float | str,
    ask_kind: str,
    ask_id: str | None,
    ask_amount: int | float | str,
) -> dict:
    offer_amt = Decimal(str(offer_amount))
    ask_amt = Decimal(str(ask_amount))

    ok, err = _validate_kind(offer_kind, offer_id, offer_amt)
    if not ok:
        return {"ok": False, "error": f"offer_{err}"}
    ok, err = _validate_kind(ask_kind, ask_id, ask_amt)
    if not ok:
        return {"ok": False, "error": f"ask_{err}"}
    if offer_kind == ask_kind and offer_id == ask_id:
        return {"ok": False, "error": "same_kind_swap"}

    # Estimate commission as 5% of the asked cash equivalent (we use $ if asked is cash,
    # else fall back to a flat per-resource estimate). Keep it simple.
    commission = (ask_amt * COMMISSION_PCT / Decimal(100)).quantize(Decimal("1"))
    if ask_kind != "cash":
        # For non-cash asks we just charge the minimum.
        commission = COMMISSION_MIN_CASH
    if commission < COMMISSION_MIN_CASH:
        commission = COMMISSION_MIN_CASH

    expires_at = _now() + timedelta(hours=LOT_LIFETIME_HOURS)

    async with pool().acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow(
                "select * from clicker_users where tg_id = $1 for update", tg_id,
            )
            if not user:
                return {"ok": False, "error": "no_user"}

            # Active lot limit (artifact bonus from max_lots_bonus).
            active = await conn.fetchrow(
                "select count(*) as n from clicker_lots where seller_tg_id = $1 and status = 'active'",
                tg_id,
            )
            user_max = await _max_active_lots(conn, tg_id)
            if int(active["n"]) >= user_max:
                return {"ok": False, "error": "lot_limit", "max": user_max}

            # Check + take commission cash
            if Decimal(user["cash"]) < commission:
                return {"ok": False, "error": "not_enough_commission", "needed": str(commission)}
            await conn.execute(
                "update clicker_users set cash = cash - $2 where tg_id = $1",
                tg_id, commission,
            )

            # Take the offered items from the seller (escrow into the lot row's payload)
            payload: dict = {}
            if offer_kind == "cash":
                if Decimal(user["cash"]) < offer_amt:
                    return {"ok": False, "error": "not_enough_cash"}
                await conn.execute(
                    "update clicker_users set cash = cash - $2 where tg_id = $1", tg_id, offer_amt,
                )
                payload["kind"] = "cash"; payload["amount"] = str(offer_amt)
            elif offer_kind == "casecoins":
                if Decimal(user["casecoins"]) < offer_amt:
                    return {"ok": False, "error": "not_enough_casecoins"}
                await conn.execute(
                    "update clicker_users set casecoins = casecoins - $2 where tg_id = $1", tg_id, offer_amt,
                )
                payload["kind"] = "casecoins"; payload["amount"] = str(offer_amt)
            elif offer_kind == "resource":
                row = await conn.fetchrow(
                    "select amount from clicker_resources where tg_id = $1 and resource_type = $2 for update",
                    tg_id, offer_id,
                )
                if not row or Decimal(row["amount"]) < offer_amt:
                    return {"ok": False, "error": "not_enough_resource"}
                await conn.execute(
                    "update clicker_resources set amount = amount - $3 where tg_id = $1 and resource_type = $2",
                    tg_id, offer_id, offer_amt,
                )
                payload["kind"] = "resource"; payload["resource_type"] = offer_id; payload["amount"] = str(offer_amt)
            elif offer_kind == "artifact":
                # offer_id is the inventory.id (numeric string)
                try:
                    inv_id = int(offer_id or 0)
                except Exception:
                    return {"ok": False, "error": "bad_artifact_id"}
                inv = await conn.fetchrow(
                    """select * from clicker_inventory where id = $1 and tg_id = $2 and item_kind in ('artifact','mythic')
                       and consumed_at is null and equipped_slot is null for update""",
                    inv_id, tg_id,
                )
                if not inv:
                    return {"ok": False, "error": "artifact_not_available"}
                # Snapshot needed to recreate on cancel
                payload["kind"] = "artifact"
                payload["item_kind"] = inv["item_kind"]
                payload["item_id"] = inv["item_id"]
                payload["rarity"] = inv["rarity"]
                # Move artifact "into" the lot by deleting inv row
                await conn.execute("delete from clicker_inventory where id = $1", inv_id)

            seller_name = user["first_name"] or user["username"] or f"player{tg_id}"
            row = await conn.fetchrow(
                """insert into clicker_lots
                    (seller_tg_id, offer_kind, offer_id, offer_amount, offer_payload,
                     ask_kind, ask_id, ask_amount, status, expires_at, seller_name)
                   values ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, 'active', $9, $10)
                   returning id""",
                tg_id, offer_kind, offer_id, offer_amt, json.dumps(payload),
                ask_kind, ask_id, ask_amt, expires_at, seller_name,
            )
            lot_id = int(row["id"])

    return {"ok": True, "data": {"lot_id": lot_id, "commission_paid": str(commission)}}


# ---------- accept lot ------------------------------------------------------


async def accept_lot(tg_id: int, lot_id: int) -> dict:
    async with pool().acquire() as conn:
        async with conn.transaction():
            lot = await conn.fetchrow(
                "select * from clicker_lots where id = $1 for update", lot_id,
            )
            if not lot:
                return {"ok": False, "error": "lot_not_found"}
            if lot["status"] != "active":
                return {"ok": False, "error": "lot_inactive"}
            if int(lot["seller_tg_id"]) == tg_id:
                return {"ok": False, "error": "self_buy"}

            now = _now()
            if lot["expires_at"] and lot["expires_at"].tzinfo is None:
                exp = lot["expires_at"].replace(tzinfo=timezone.utc)
            else:
                exp = lot["expires_at"]
            if exp and exp <= now:
                return {"ok": False, "error": "expired"}

            # Buyer must have the asked item.
            ask_kind = lot["ask_kind"]
            ask_id = lot["ask_id"]
            ask_amt = Decimal(lot["ask_amount"])

            buyer = await conn.fetchrow(
                "select * from clicker_users where tg_id = $1 for update", tg_id,
            )
            if not buyer:
                return {"ok": False, "error": "no_user"}

            # Deduct from buyer
            if ask_kind == "cash":
                if Decimal(buyer["cash"]) < ask_amt:
                    return {"ok": False, "error": "not_enough_cash"}
                await conn.execute(
                    "update clicker_users set cash = cash - $2 where tg_id = $1", tg_id, ask_amt,
                )
            elif ask_kind == "casecoins":
                if Decimal(buyer["casecoins"]) < ask_amt:
                    return {"ok": False, "error": "not_enough_casecoins"}
                await conn.execute(
                    "update clicker_users set casecoins = casecoins - $2 where tg_id = $1", tg_id, ask_amt,
                )
            elif ask_kind == "resource":
                row = await conn.fetchrow(
                    "select amount from clicker_resources where tg_id = $1 and resource_type = $2 for update",
                    tg_id, ask_id,
                )
                if not row or Decimal(row["amount"]) < ask_amt:
                    return {"ok": False, "error": "not_enough_resource"}
                await conn.execute(
                    "update clicker_resources set amount = amount - $3 where tg_id = $1 and resource_type = $2",
                    tg_id, ask_id, ask_amt,
                )
            elif ask_kind == "artifact":
                # Buyer must own this artifact (by inventory.id), unequipped + not consumed.
                try:
                    inv_id = int(ask_id or 0)
                except Exception:
                    return {"ok": False, "error": "bad_ask_artifact"}
                inv = await conn.fetchrow(
                    """select * from clicker_inventory where id = $1 and tg_id = $2
                       and consumed_at is null and equipped_slot is null for update""",
                    inv_id, tg_id,
                )
                if not inv:
                    return {"ok": False, "error": "ask_artifact_not_owned"}
                # Hand it to seller
                await conn.execute(
                    "update clicker_inventory set tg_id = $2 where id = $1",
                    inv_id, int(lot["seller_tg_id"]),
                )

            # Credit seller with non-artifact ask amount.
            seller_id = int(lot["seller_tg_id"])
            if ask_kind == "cash":
                await conn.execute(
                    "update clicker_users set cash = cash + $2 where tg_id = $1", seller_id, ask_amt,
                )
            elif ask_kind == "casecoins":
                await conn.execute(
                    "update clicker_users set casecoins = casecoins + $2 where tg_id = $1", seller_id, ask_amt,
                )
            elif ask_kind == "resource":
                await conn.execute(
                    """insert into clicker_resources (tg_id, resource_type, amount) values ($1, $2, $3)
                       on conflict (tg_id, resource_type) do update set amount = clicker_resources.amount + excluded.amount""",
                    seller_id, ask_id, ask_amt,
                )
            # (artifact ask — already moved to seller above)

            # Hand the offer to buyer.
            payload = _parse_jsonb(lot["offer_payload"]) or {}
            offer_kind = lot["offer_kind"]
            offer_amt = Decimal(lot["offer_amount"])
            if offer_kind == "cash":
                await conn.execute(
                    "update clicker_users set cash = cash + $2 where tg_id = $1", tg_id, offer_amt,
                )
            elif offer_kind == "casecoins":
                await conn.execute(
                    "update clicker_users set casecoins = casecoins + $2 where tg_id = $1", tg_id, offer_amt,
                )
            elif offer_kind == "resource":
                await conn.execute(
                    """insert into clicker_resources (tg_id, resource_type, amount) values ($1, $2, $3)
                       on conflict (tg_id, resource_type) do update set amount = clicker_resources.amount + excluded.amount""",
                    tg_id, lot["offer_id"], offer_amt,
                )
            elif offer_kind == "artifact":
                await conn.execute(
                    """insert into clicker_inventory (tg_id, item_kind, item_id, rarity)
                       values ($1, $2, $3, $4)""",
                    tg_id, payload.get("item_kind") or "artifact",
                    payload.get("item_id"), payload.get("rarity"),
                )

            await conn.execute(
                """update clicker_lots set status = 'sold', sold_to_tg_id = $2, sold_at = $3
                   where id = $1""",
                lot_id, tg_id, _now(),
            )

    return {"ok": True, "data": {"lot_id": lot_id}}


# ---------- cancel / expire -------------------------------------------------


async def cancel_lot(tg_id: int, lot_id: int) -> dict:
    async with pool().acquire() as conn:
        async with conn.transaction():
            lot = await conn.fetchrow(
                "select * from clicker_lots where id = $1 for update", lot_id,
            )
            if not lot:
                return {"ok": False, "error": "lot_not_found"}
            if int(lot["seller_tg_id"]) != tg_id:
                return {"ok": False, "error": "not_owner"}
            if lot["status"] != "active":
                return {"ok": False, "error": "lot_inactive"}

            await _refund_offer_to_seller(conn, lot)

            await conn.execute(
                """update clicker_lots set status = 'cancelled', cancelled_at = $2
                   where id = $1""",
                lot_id, _now(),
            )
    return {"ok": True, "data": {"lot_id": lot_id}}


async def _refund_offer_to_seller(conn, lot) -> None:
    payload = _parse_jsonb(lot["offer_payload"]) or {}
    seller_id = int(lot["seller_tg_id"])
    offer_kind = lot["offer_kind"]
    offer_amt = Decimal(lot["offer_amount"])
    if offer_kind == "cash":
        await conn.execute(
            "update clicker_users set cash = cash + $2 where tg_id = $1", seller_id, offer_amt,
        )
    elif offer_kind == "casecoins":
        await conn.execute(
            "update clicker_users set casecoins = casecoins + $2 where tg_id = $1", seller_id, offer_amt,
        )
    elif offer_kind == "resource":
        await conn.execute(
            """insert into clicker_resources (tg_id, resource_type, amount) values ($1, $2, $3)
               on conflict (tg_id, resource_type) do update set amount = clicker_resources.amount + excluded.amount""",
            seller_id, lot["offer_id"], offer_amt,
        )
    elif offer_kind == "artifact":
        await conn.execute(
            """insert into clicker_inventory (tg_id, item_kind, item_id, rarity)
               values ($1, $2, $3, $4)""",
            seller_id, payload.get("item_kind") or "artifact",
            payload.get("item_id"), payload.get("rarity"),
        )


async def _expire_pending() -> int:
    """Sweep expired lots, refund offers to sellers. Best-effort, cheap."""
    now = _now()
    async with pool().acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """select * from clicker_lots
                   where status = 'active' and expires_at <= $1 for update""",
                now,
            )
            for lot in rows:
                await _refund_offer_to_seller(conn, lot)
                await conn.execute(
                    "update clicker_lots set status = 'expired' where id = $1",
                    lot["id"],
                )
            return len(rows)


# ---------- listings --------------------------------------------------------


def _serialize_lot(row) -> dict:
    return {
        "id": int(row["id"]),
        "seller_tg_id": int(row["seller_tg_id"]),
        "seller_name": row["seller_name"],
        "offer_kind": row["offer_kind"],
        "offer_id": row["offer_id"],
        "offer_amount": str(row["offer_amount"]),
        "offer_payload": _parse_jsonb(row["offer_payload"]) or {},
        "ask_kind": row["ask_kind"],
        "ask_id": row["ask_id"],
        "ask_amount": str(row["ask_amount"]),
        "status": row["status"],
        "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
        "sold_to_tg_id": int(row["sold_to_tg_id"]) if row["sold_to_tg_id"] else None,
        "sold_at": row["sold_at"].isoformat() if row["sold_at"] else None,
        "created_at": row["created_at"].isoformat(),
    }


async def list_lots(limit: int = 50, exclude_seller: int | None = None) -> list[dict]:
    await _expire_pending()
    async with pool().acquire() as conn:
        if exclude_seller is not None:
            rows = await conn.fetch(
                """select * from clicker_lots where status = 'active' and seller_tg_id != $1
                   order by created_at desc limit $2""",
                exclude_seller, int(limit),
            )
        else:
            rows = await conn.fetch(
                """select * from clicker_lots where status = 'active'
                   order by created_at desc limit $1""",
                int(limit),
            )
    return [_serialize_lot(r) for r in rows]


async def my_lots(tg_id: int) -> dict:
    await _expire_pending()
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """select * from clicker_lots where seller_tg_id = $1
               order by created_at desc limit 50""",
            tg_id,
        )
    return [_serialize_lot(r) for r in rows]


async def history(limit: int = 50, asset_kind: str | None = None, asset_id: str | None = None) -> list[dict]:
    """Last N sold/cancelled/expired lots — 'price index' source."""
    async with pool().acquire() as conn:
        if asset_kind and asset_id:
            rows = await conn.fetch(
                """select * from clicker_lots
                   where status in ('sold','cancelled','expired')
                     and ((offer_kind = $1 and offer_id = $2) or (ask_kind = $1 and ask_id = $2))
                   order by coalesce(sold_at, cancelled_at, expires_at) desc nulls last
                   limit $3""",
                asset_kind, asset_id, int(limit),
            )
        else:
            rows = await conn.fetch(
                """select * from clicker_lots where status in ('sold','cancelled','expired')
                   order by coalesce(sold_at, cancelled_at, expires_at) desc nulls last
                   limit $1""",
                int(limit),
            )
    return [_serialize_lot(r) for r in rows]


# ---------- price index (24h sold lots) -------------------------------------


async def price_index(hours: int = 48) -> dict:
    """Aggregate sold lots over the last N hours: per resource → avg cash-equivalent unit price."""
    cutoff = _now() - timedelta(hours=hours)
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """select offer_kind, offer_id, offer_amount, ask_kind, ask_id, ask_amount
               from clicker_lots where status = 'sold' and sold_at >= $1""",
            cutoff,
        )

    # For each resource asset, compute avg cash per unit when sold-for-cash; otherwise skip.
    by_asset: dict[str, dict[str, Any]] = {}
    sample_count: dict[str, int] = {}
    for r in rows:
        # offer was sold; ask side is the price.
        if r["offer_kind"] == "resource" and r["ask_kind"] == "cash":
            res = r["offer_id"]
            unit_price = Decimal(r["ask_amount"]) / Decimal(r["offer_amount"]) if Decimal(r["offer_amount"]) > 0 else Decimal(0)
            agg = by_asset.setdefault(res, {"total_cash": Decimal(0), "total_units": Decimal(0), "trades": 0})
            agg["total_cash"] += Decimal(r["ask_amount"])
            agg["total_units"] += Decimal(r["offer_amount"])
            agg["trades"] += 1
        elif r["ask_kind"] == "resource" and r["offer_kind"] == "cash":
            res = r["ask_id"]
            agg = by_asset.setdefault(res, {"total_cash": Decimal(0), "total_units": Decimal(0), "trades": 0})
            agg["total_cash"] += Decimal(r["offer_amount"])
            agg["total_units"] += Decimal(r["ask_amount"])
            agg["trades"] += 1

    out = {}
    for res, agg in by_asset.items():
        if agg["total_units"] > 0:
            out[res] = {
                "avg_unit_cash": str((agg["total_cash"] / agg["total_units"]).quantize(Decimal("0.01"))),
                "trades": agg["trades"],
                "total_volume_units": str(agg["total_units"]),
            }
    return {"window_hours": hours, "assets": out}


# ---------- offers (Make Offer) ---------------------------------------------


async def make_offer(tg_id: int, lot_id: int, offer_kind: str, offer_id: str | None,
                     offer_amount: int | float | str, note: str | None = None) -> dict:
    """Buyer proposes an alternative payment for a lot.
    Escrow the offer immediately so it's binding when seller accepts."""
    offer_amt = Decimal(str(offer_amount))
    ok, err = _validate_kind(offer_kind, offer_id, offer_amt)
    if not ok:
        return {"ok": False, "error": err}

    async with pool().acquire() as conn:
        async with conn.transaction():
            lot = await conn.fetchrow(
                "select * from clicker_lots where id = $1 for update", lot_id,
            )
            if not lot:
                return {"ok": False, "error": "lot_not_found"}
            if lot["status"] != "active":
                return {"ok": False, "error": "lot_inactive"}
            if int(lot["seller_tg_id"]) == tg_id:
                return {"ok": False, "error": "self_offer"}

            buyer = await conn.fetchrow(
                "select * from clicker_users where tg_id = $1 for update", tg_id,
            )
            if not buyer:
                return {"ok": False, "error": "no_user"}

            payload: dict[str, Any] = {"kind": offer_kind, "amount": str(offer_amt)}
            if offer_kind == "cash":
                if Decimal(buyer["cash"]) < offer_amt:
                    return {"ok": False, "error": "not_enough_cash"}
                await conn.execute(
                    "update clicker_users set cash = cash - $2 where tg_id = $1", tg_id, offer_amt,
                )
            elif offer_kind == "casecoins":
                if Decimal(buyer["casecoins"]) < offer_amt:
                    return {"ok": False, "error": "not_enough_casecoins"}
                await conn.execute(
                    "update clicker_users set casecoins = casecoins - $2 where tg_id = $1", tg_id, offer_amt,
                )
            elif offer_kind == "resource":
                row = await conn.fetchrow(
                    "select amount from clicker_resources where tg_id = $1 and resource_type = $2 for update",
                    tg_id, offer_id,
                )
                if not row or Decimal(row["amount"]) < offer_amt:
                    return {"ok": False, "error": "not_enough_resource"}
                await conn.execute(
                    "update clicker_resources set amount = amount - $3 where tg_id = $1 and resource_type = $2",
                    tg_id, offer_id, offer_amt,
                )
                payload["resource_type"] = offer_id
            elif offer_kind == "artifact":
                try:
                    inv_id = int(offer_id or 0)
                except Exception:
                    return {"ok": False, "error": "bad_artifact_id"}
                inv = await conn.fetchrow(
                    """select * from clicker_inventory where id = $1 and tg_id = $2 and item_kind in ('artifact','mythic')
                       and consumed_at is null and equipped_slot is null for update""",
                    inv_id, tg_id,
                )
                if not inv:
                    return {"ok": False, "error": "artifact_not_available"}
                payload["item_kind"] = inv["item_kind"]
                payload["item_id"] = inv["item_id"]
                payload["rarity"] = inv["rarity"]
                await conn.execute("delete from clicker_inventory where id = $1", inv_id)

            expires_at = _now() + timedelta(hours=OFFER_LIFETIME_HOURS)
            row = await conn.fetchrow(
                """insert into clicker_offers (lot_id, offerer_tg_id, offer_payload, expires_at, note)
                   values ($1, $2, $3::jsonb, $4, $5) returning id""",
                lot_id, tg_id, json.dumps(payload), expires_at, (note or "")[:200],
            )
            return {"ok": True, "data": {"offer_id": int(row["id"])}}


async def respond_offer(seller_tg_id: int, offer_id: int, accept: bool) -> dict:
    """Seller accepts or rejects an offer. Accept = swap; Reject = refund."""
    async with pool().acquire() as conn:
        async with conn.transaction():
            offer = await conn.fetchrow(
                "select * from clicker_offers where id = $1 for update", offer_id,
            )
            if not offer:
                return {"ok": False, "error": "offer_not_found"}
            if offer["status"] != "pending":
                return {"ok": False, "error": "offer_inactive"}
            lot = await conn.fetchrow(
                "select * from clicker_lots where id = $1 for update", int(offer["lot_id"]),
            )
            if not lot or int(lot["seller_tg_id"]) != seller_tg_id:
                return {"ok": False, "error": "not_your_lot"}
            if lot["status"] != "active" and accept:
                return {"ok": False, "error": "lot_inactive"}

            payload = _parse_jsonb(offer["offer_payload"]) or {}
            offerer = int(offer["offerer_tg_id"])

            if not accept:
                # Refund offerer.
                await _refund_payload(conn, offerer, payload)
                await conn.execute(
                    "update clicker_offers set status = 'rejected', responded_at = $2 where id = $1",
                    offer_id, _now(),
                )
                return {"ok": True, "data": {"offer_id": offer_id, "rejected": True}}

            # Accept: hand offer payload to seller, hand lot's offer to offerer, close lot.
            await _credit_payload(conn, seller_tg_id, payload)
            lot_payload = _parse_jsonb(lot["offer_payload"]) or {"kind": lot["offer_kind"]}
            if lot["offer_kind"] != "artifact":
                lot_payload.setdefault("amount", str(lot["offer_amount"]))
                lot_payload.setdefault("resource_type", lot["offer_id"])
            await _credit_payload(conn, offerer, lot_payload)

            now = _now()
            await conn.execute(
                """update clicker_lots set status = 'sold', sold_to_tg_id = $2, sold_at = $3
                   where id = $1""",
                int(lot["id"]), offerer, now,
            )
            await conn.execute(
                "update clicker_offers set status = 'accepted', responded_at = $2 where id = $1",
                offer_id, now,
            )
            # Refund any other pending offers on the same lot.
            others = await conn.fetch(
                "select * from clicker_offers where lot_id = $1 and status = 'pending' and id != $2 for update",
                int(lot["id"]), offer_id,
            )
            for o in others:
                op = _parse_jsonb(o["offer_payload"]) or {}
                await _refund_payload(conn, int(o["offerer_tg_id"]), op)
                await conn.execute(
                    "update clicker_offers set status = 'expired', responded_at = $2 where id = $1",
                    int(o["id"]), now,
                )
            return {"ok": True, "data": {"offer_id": offer_id, "accepted": True, "lot_id": int(lot["id"])}}


async def cancel_offer(tg_id: int, offer_id: int) -> dict:
    """Offerer withdraws their pending offer. Refunds escrow."""
    async with pool().acquire() as conn:
        async with conn.transaction():
            offer = await conn.fetchrow(
                "select * from clicker_offers where id = $1 for update", offer_id,
            )
            if not offer:
                return {"ok": False, "error": "offer_not_found"}
            if int(offer["offerer_tg_id"]) != tg_id:
                return {"ok": False, "error": "not_offerer"}
            if offer["status"] != "pending":
                return {"ok": False, "error": "offer_inactive"}
            payload = _parse_jsonb(offer["offer_payload"]) or {}
            await _refund_payload(conn, tg_id, payload)
            await conn.execute(
                "update clicker_offers set status = 'cancelled', responded_at = $2 where id = $1",
                offer_id, _now(),
            )
    return {"ok": True, "data": {"offer_id": offer_id}}


async def list_offers_received(seller_tg_id: int) -> list[dict]:
    """Pending offers on the seller's active lots."""
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """select o.*, l.offer_kind as lot_offer_kind, l.offer_id as lot_offer_id, l.offer_amount as lot_offer_amount,
                      l.ask_kind as lot_ask_kind, l.ask_id as lot_ask_id, l.ask_amount as lot_ask_amount,
                      u.first_name as offerer_name
               from clicker_offers o
               join clicker_lots l on l.id = o.lot_id
               left join clicker_users u on u.tg_id = o.offerer_tg_id
               where l.seller_tg_id = $1 and o.status = 'pending'
               order by o.created_at desc limit 100""",
            seller_tg_id,
        )
    return [{
        "offer_id": int(r["id"]),
        "lot_id": int(r["lot_id"]),
        "offerer_tg_id": int(r["offerer_tg_id"]),
        "offerer_name": r["offerer_name"],
        "offer_payload": _parse_jsonb(r["offer_payload"]) or {},
        "note": r["note"],
        "created_at": r["created_at"].isoformat(),
        "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
        "lot": {
            "offer_kind": r["lot_offer_kind"],
            "offer_id": r["lot_offer_id"],
            "offer_amount": str(r["lot_offer_amount"]),
            "ask_kind": r["lot_ask_kind"],
            "ask_id": r["lot_ask_id"],
            "ask_amount": str(r["lot_ask_amount"]),
        },
    } for r in rows]


async def list_offers_made(offerer_tg_id: int) -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """select o.*, l.offer_kind as lot_offer_kind, l.offer_id as lot_offer_id, l.offer_amount as lot_offer_amount,
                      l.seller_name as seller_name
               from clicker_offers o
               join clicker_lots l on l.id = o.lot_id
               where o.offerer_tg_id = $1 order by o.created_at desc limit 100""",
            offerer_tg_id,
        )
    return [{
        "offer_id": int(r["id"]),
        "lot_id": int(r["lot_id"]),
        "status": r["status"],
        "seller_name": r["seller_name"],
        "offer_payload": _parse_jsonb(r["offer_payload"]) or {},
        "lot": {
            "offer_kind": r["lot_offer_kind"],
            "offer_id": r["lot_offer_id"],
            "offer_amount": str(r["lot_offer_amount"]),
        },
        "created_at": r["created_at"].isoformat(),
    } for r in rows]


async def _refund_payload(conn, tg_id: int, payload: dict) -> None:
    kind = payload.get("kind")
    if kind == "cash":
        await conn.execute(
            "update clicker_users set cash = cash + $2 where tg_id = $1",
            tg_id, Decimal(str(payload.get("amount", 0))),
        )
    elif kind == "casecoins":
        await conn.execute(
            "update clicker_users set casecoins = casecoins + $2 where tg_id = $1",
            tg_id, Decimal(str(payload.get("amount", 0))),
        )
    elif kind == "resource":
        await conn.execute(
            """insert into clicker_resources (tg_id, resource_type, amount) values ($1, $2, $3)
               on conflict (tg_id, resource_type) do update set amount = clicker_resources.amount + excluded.amount""",
            tg_id, payload.get("resource_type"), Decimal(str(payload.get("amount", 0))),
        )
    elif kind == "artifact":
        await conn.execute(
            """insert into clicker_inventory (tg_id, item_kind, item_id, rarity)
               values ($1, $2, $3, $4)""",
            tg_id, payload.get("item_kind") or "artifact",
            payload.get("item_id"), payload.get("rarity"),
        )


async def _credit_payload(conn, tg_id: int, payload: dict) -> None:
    """Same as refund — credit a payload to a user."""
    await _refund_payload(conn, tg_id, payload)
