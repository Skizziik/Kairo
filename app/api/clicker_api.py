"""FastAPI routes for the CS:Clicker Mini App."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.auth import require_user
from app.clicker import game as gm
from app.clicker import market as mk

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/clicker", tags=["clicker"])


class TapReq(BaseModel):
    taps: int = Field(..., ge=0, le=60)
    dt_ms: int = Field(..., ge=0, le=10000)


class UpgradeReq(BaseModel):
    kind: str = Field(..., min_length=1, max_length=32)
    slot_id: str = Field(..., min_length=1, max_length=64)
    count: int = Field(default=1, ge=1, le=25)


class OpenChestReq(BaseModel):
    chest_inventory_id: int = Field(..., ge=1)


class EquipReq(BaseModel):
    inventory_id: int = Field(..., ge=1)
    slot: int = Field(..., ge=0, le=5)


class UnequipReq(BaseModel):
    inventory_id: int = Field(..., ge=1)


class BusinessReq(BaseModel):
    business_id: str = Field(..., min_length=1, max_length=32)


class BusinessCollectReq(BaseModel):
    business_id: str | None = Field(default=None)


class PrestigeNodeReq(BaseModel):
    node_id: str = Field(..., min_length=1, max_length=64)


class GotoLevelReq(BaseModel):
    target_level: int = Field(..., ge=1, le=999)


class BusinessBranchReq(BaseModel):
    business_id: str = Field(..., min_length=1, max_length=32)
    branch_id: str = Field(..., min_length=1, max_length=32)


class BPClaimReq(BaseModel):
    level: int = Field(..., ge=1, le=50)
    track: str = Field(..., min_length=4, max_length=8)


class CreateLotReq(BaseModel):
    offer_kind: str = Field(..., min_length=1, max_length=16)
    offer_id: str | None = Field(default=None, max_length=64)
    offer_amount: float = Field(..., ge=0.0001)
    ask_kind: str = Field(..., min_length=1, max_length=16)
    ask_id: str | None = Field(default=None, max_length=64)
    ask_amount: float = Field(..., ge=0.0001)


class LotIdReq(BaseModel):
    lot_id: int = Field(..., ge=1)


@router.get("/config")
async def api_config() -> dict:
    return {"ok": True, "data": gm.public_config()}


@router.get("/state")
async def api_state(user: dict = Depends(require_user)) -> dict:
    tg_id = int(user["id"])
    await gm.ensure_user(
        tg_id,
        username=user.get("username"),
        first_name=user.get("first_name"),
        last_name=user.get("last_name"),
        is_premium=bool(user.get("is_premium", False)),
    )
    return await gm.get_state(tg_id)


@router.post("/tap")
async def api_tap(req: TapReq, user: dict = Depends(require_user)) -> dict:
    return await gm.tap(int(user["id"]), req.taps, req.dt_ms)


@router.post("/upgrade")
async def api_upgrade(req: UpgradeReq, user: dict = Depends(require_user)) -> dict:
    return await gm.buy_upgrade(int(user["id"]), req.kind, req.slot_id, req.count)


@router.post("/chest/open")
async def api_chest_open(req: OpenChestReq, user: dict = Depends(require_user)) -> dict:
    return await gm.open_chest(int(user["id"]), req.chest_inventory_id)


@router.post("/equip")
async def api_equip(req: EquipReq, user: dict = Depends(require_user)) -> dict:
    return await gm.equip(int(user["id"]), req.inventory_id, req.slot)


@router.post("/unequip")
async def api_unequip(req: UnequipReq, user: dict = Depends(require_user)) -> dict:
    return await gm.unequip(int(user["id"]), req.inventory_id)


@router.post("/prestige")
async def api_prestige(user: dict = Depends(require_user)) -> dict:
    return await gm.prestige(int(user["id"]))


@router.post("/business/tap")
async def api_business_tap(req: BusinessReq, user: dict = Depends(require_user)) -> dict:
    return await gm.business_tap(int(user["id"]), req.business_id)


@router.post("/business/collect")
async def api_business_collect(req: BusinessCollectReq, user: dict = Depends(require_user)) -> dict:
    return await gm.business_collect(int(user["id"]), req.business_id)


@router.post("/business/upgrade")
async def api_business_upgrade(req: BusinessReq, user: dict = Depends(require_user)) -> dict:
    return await gm.business_upgrade(int(user["id"]), req.business_id)


@router.post("/business/branch/buy")
async def api_business_branch_buy(req: BusinessBranchReq, user: dict = Depends(require_user)) -> dict:
    return await gm.buy_business_branch(int(user["id"]), req.business_id, req.branch_id)


@router.get("/battlepass")
async def api_battlepass(user: dict = Depends(require_user)) -> dict:
    return await gm.get_battlepass(int(user["id"]))


@router.post("/battlepass/buy_premium")
async def api_battlepass_buy_premium(user: dict = Depends(require_user)) -> dict:
    return await gm.bp_buy_premium(int(user["id"]))


@router.post("/battlepass/claim")
async def api_battlepass_claim(req: BPClaimReq, user: dict = Depends(require_user)) -> dict:
    return await gm.bp_claim(int(user["id"]), req.level, req.track)


# ---- Marketplace ----

@router.get("/market/lots")
async def api_market_lots(user: dict = Depends(require_user)) -> dict:
    rows = await mk.list_lots(limit=100, exclude_seller=int(user["id"]))
    return {"ok": True, "data": rows}


@router.get("/market/my_lots")
async def api_market_my_lots(user: dict = Depends(require_user)) -> dict:
    rows = await mk.my_lots(int(user["id"]))
    return {"ok": True, "data": rows}


@router.get("/market/history")
async def api_market_history(asset_kind: str | None = None, asset_id: str | None = None,
                              user: dict = Depends(require_user)) -> dict:
    rows = await mk.history(limit=50, asset_kind=asset_kind, asset_id=asset_id)
    return {"ok": True, "data": rows}


@router.post("/market/create")
async def api_market_create(req: CreateLotReq, user: dict = Depends(require_user)) -> dict:
    return await mk.create_lot(
        int(user["id"]),
        req.offer_kind, req.offer_id, req.offer_amount,
        req.ask_kind, req.ask_id, req.ask_amount,
    )


@router.post("/market/accept")
async def api_market_accept(req: LotIdReq, user: dict = Depends(require_user)) -> dict:
    return await mk.accept_lot(int(user["id"]), req.lot_id)


@router.post("/market/cancel")
async def api_market_cancel(req: LotIdReq, user: dict = Depends(require_user)) -> dict:
    return await mk.cancel_lot(int(user["id"]), req.lot_id)


@router.post("/prestige/buy_node")
async def api_prestige_buy_node(req: PrestigeNodeReq, user: dict = Depends(require_user)) -> dict:
    return await gm.buy_prestige_node(int(user["id"]), req.node_id)


@router.post("/level/goto")
async def api_level_goto(req: GotoLevelReq, user: dict = Depends(require_user)) -> dict:
    return await gm.goto_level(int(user["id"]), req.target_level)


@router.get("/leaderboard")
async def api_leaderboard(
    metric: str = Query(default="level"),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict:
    rows = await gm.leaderboard(metric, limit)
    return {"ok": True, "data": rows}
