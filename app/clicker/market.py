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

VALID_KINDS = {"resource", "artifact", "cash", "casecoins"}


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

            # Active lot limit
            active = await conn.fetchrow(
                "select count(*) as n from clicker_lots where seller_tg_id = $1 and status = 'active'",
                tg_id,
            )
            if int(active["n"]) >= MAX_ACTIVE_LOTS:
                return {"ok": False, "error": "lot_limit", "max": MAX_ACTIVE_LOTS}

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
