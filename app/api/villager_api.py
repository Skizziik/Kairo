"""FastAPI routes for the Village Tycoon Mini App."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import require_user
from app.villager import game as vt

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/villager", tags=["villager"])


class BuildReq(BaseModel):
    type: str = Field(..., min_length=1)
    x: int = Field(..., ge=0, le=63)
    y: int = Field(..., ge=0, le=63)


class UpgradeReq(BaseModel):
    building_id: int = Field(..., ge=1)


class MoveReq(BaseModel):
    building_id: int = Field(..., ge=1)
    x: int = Field(..., ge=0, le=63)
    y: int = Field(..., ge=0, le=63)


class DemolishReq(BaseModel):
    building_id: int = Field(..., ge=1)


class QuestClaimReq(BaseModel):
    quest_id: str = Field(..., min_length=1)


@router.get("/config")
async def api_villager_config() -> dict:
    return {"ok": True, "data": vt.public_config()}


@router.get("/state")
async def api_villager_state(user: dict = Depends(require_user)) -> dict:
    tg_id = int(user["id"])
    await vt.ensure_user(
        tg_id,
        username=user.get("username"),
        first_name=user.get("first_name"),
        last_name=user.get("last_name"),
        language_code=user.get("language_code"),
        is_premium=bool(user.get("is_premium", False)),
    )
    state = await vt.get_state(tg_id)
    return {"ok": True, "data": state}


@router.post("/build")
async def api_villager_build(req: BuildReq, user: dict = Depends(require_user)) -> dict:
    return await vt.build(int(user["id"]), req.type, req.x, req.y)


@router.post("/upgrade")
async def api_villager_upgrade(req: UpgradeReq, user: dict = Depends(require_user)) -> dict:
    return await vt.upgrade(int(user["id"]), req.building_id)


@router.post("/move")
async def api_villager_move(req: MoveReq, user: dict = Depends(require_user)) -> dict:
    return await vt.move(int(user["id"]), req.building_id, req.x, req.y)


@router.post("/demolish")
async def api_villager_demolish(req: DemolishReq, user: dict = Depends(require_user)) -> dict:
    return await vt.demolish(int(user["id"]), req.building_id)


@router.post("/collect_all")
async def api_villager_collect_all(user: dict = Depends(require_user)) -> dict:
    return await vt.collect_all(int(user["id"]))


@router.post("/quest/claim")
async def api_villager_quest_claim(req: QuestClaimReq, user: dict = Depends(require_user)) -> dict:
    return await vt.quest_claim(int(user["id"]), req.quest_id)
