"""FastAPI routes for the Casino Mini App."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import require_user
from app.db import repos as base_repos
from app.economy import repo as eco
from app.economy.pricing import rarity_emoji, rarity_label, wear_label, wear_short

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["casino"])


class OpenCaseReq(BaseModel):
    case_id: int = Field(..., ge=1)


class OpenCaseMultiReq(BaseModel):
    case_id: int = Field(..., ge=1)
    count: int = Field(default=1, ge=1, le=5)


def _skin_to_dict(item: dict) -> dict:
    """Normalize inventory row for JSON output."""
    return {
        "id": int(item["id"]),
        "skin_id": int(item["skin_id"]),
        "name": item["full_name"],
        "weapon": item["weapon"],
        "skin_name": item["skin_name"],
        "rarity": item["rarity"],
        "rarity_label": rarity_label(item["rarity"]),
        "rarity_color": item["rarity_color"],
        "rarity_emoji": rarity_emoji(item["rarity"]),
        "image_url": item["image_url"],
        "category": item["category"],
        "float": round(float(item["float_value"]), 4),
        "wear": item["wear"],
        "wear_label": wear_label(item["wear"]),
        "wear_short": wear_short(item["wear"]),
        "stat_trak": bool(item["stat_trak"]),
        "price": int(item["price"]),
        "acquired_at": item["acquired_at"].isoformat() if item.get("acquired_at") else None,
        "locked": bool(item.get("locked", False)),
        "coinflip_lobby_id": int(item["coinflip_lobby_id"]) if item.get("coinflip_lobby_id") else 0,
    }


@router.get("/me")
async def api_me(user: dict = Depends(require_user)) -> dict:
    tg_id = int(user["id"])
    # keep users table in sync with Telegram profile
    await base_repos.upsert_user(
        tg_id=tg_id,
        username=user.get("username"),
        first_name=user.get("first_name"),
        last_name=user.get("last_name"),
    )
    await eco.ensure_user(tg_id)
    row = await eco.get_user(tg_id)
    inv_count = len(await eco.inventory_of(tg_id, limit=1000))
    return {
        "tg_id": tg_id,
        "username": user.get("username"),
        "first_name": user.get("first_name"),
        "photo_url": user.get("photo_url"),
        "balance": int(row["balance"]) if row else 0,
        "total_earned": int(row["total_earned"]) if row else 0,
        "total_spent": int(row["total_spent"]) if row else 0,
        "cases_opened": int(row["cases_opened"]) if row else 0,
        "current_streak": int(row["current_streak"]) if row else 0,
        "best_streak": int(row["best_streak"]) if row else 0,
        "last_daily_at": row["last_daily_at"].isoformat() if row and row.get("last_daily_at") else None,
        "inventory_count": inv_count,
    }


@router.post("/daily")
async def api_daily(user: dict = Depends(require_user)) -> dict:
    tg_id = int(user["id"])
    result = await eco.try_claim_daily(tg_id)
    return result


@router.get("/cases")
async def api_cases(user: dict = Depends(require_user)) -> list[dict]:
    _ = user
    from app.db.client import pool as dbpool

    cases = await eco.list_cases()
    out = []
    async with dbpool().acquire() as conn:
        for c in cases:
            full = await conn.fetchrow(
                "select loot_pool from economy_cases where id = $1", int(c["id"])
            )
            pool = full["loot_pool"] if full else None
            if isinstance(pool, str):
                pool = json.loads(pool)
            all_ids: list[int] = []
            for ids in (pool or {}).get("by_rarity", {}).values():
                all_ids.extend(ids)
            all_ids = list(set(all_ids))

            preview_items: list[dict] = []
            if all_ids:
                # Prefer regular weapons for the preview, but fall back to knives/gloves
                # when the pool has too few weapons (e.g. "Нож или ничего" has only 1).
                rows = await conn.fetch(
                    "select id, full_name, rarity, rarity_color, image_url, base_price, category "
                    "from economy_skins_catalog "
                    "where id = any($1::int[]) "
                    "order by (case when category = 'weapon' then 0 else 1 end), "
                    "         base_price desc "
                    "limit 5",
                    all_ids,
                )
                preview_items = [
                    {
                        "id": int(r["id"]),
                        "name": r["full_name"],
                        "rarity": r["rarity"],
                        "rarity_color": r["rarity_color"],
                        "image_url": r["image_url"],
                    }
                    for r in rows
                ]

            out.append({
                "id": int(c["id"]),
                "key": c["key"],
                "name": c["name"],
                "description": c["description"],
                "price": int(c["price"]),
                "image_url": c.get("image_url"),  # official case PNG (if seeded)
                "preview_items": preview_items[:4],
            })
    return out


@router.post("/case/open")
async def api_case_open(req: OpenCaseReq, user: dict = Depends(require_user)) -> dict:
    tg_id = int(user["id"])
    result = await eco.open_case(tg_id, req.case_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "error"))
    return result


@router.post("/case/open_multi")
async def api_case_open_multi(req: OpenCaseMultiReq, user: dict = Depends(require_user)) -> dict:
    """Open the same case `count` times atomically. Validates total balance upfront
    so player either gets all N or none (no partial opens)."""
    tg_id = int(user["id"])
    case = await eco.get_case(req.case_id)
    if not case:
        raise HTTPException(status_code=404, detail="case not found")

    from app.economy import repo as _repo
    from app.db.client import pool as _pool

    # Pre-check balance for all N opens (gear case_discount may apply per open,
    # so use base price as upper-bound; per-open will deduct discounted)
    total_cost = int(case["price"]) * req.count
    async with _pool().acquire() as conn:
        bal_row = await conn.fetchrow(
            "select balance from economy_users where tg_id = $1", tg_id,
        )
        if bal_row is None or int(bal_row["balance"]) < total_cost:
            raise HTTPException(
                status_code=400,
                detail=f"Не хватает коинов на {req.count}× ({total_cost} 🪙)",
            )

    results = []
    errors = []
    for i in range(req.count):
        try:
            r = await eco.open_case(tg_id, req.case_id)
        except Exception as e:
            log.exception("multi-open #%d failed for tg_id=%s case_id=%s", i, tg_id, req.case_id)
            errors.append({"i": i, "error": str(e)})
            continue
        if not r.get("ok"):
            log.warning("multi-open #%d returned not-ok for tg_id=%s: %s", i, tg_id, r.get("error"))
            errors.append({"i": i, "error": r.get("error", "unknown")})
            continue
        results.append(r)
    return {
        "ok": True,
        "results": results,
        "opened": len(results),
        "expected": req.count,
        "errors": errors,
    }


@router.get("/case/{case_id}/pool")
async def api_case_pool(case_id: int, user: dict = Depends(require_user)) -> dict:
    """Return the full loot pool (all items that can drop) for a case.
    Used to render the preview carousel."""
    _ = user
    case = await eco.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="case not found")
    pool = case["loot_pool"]
    if isinstance(pool, str):
        pool = json.loads(pool)
    by_rarity = pool.get("by_rarity", {})
    all_ids = []
    for ids in by_rarity.values():
        all_ids.extend(ids)
    all_ids = list(set(all_ids))
    if not all_ids:
        return {"name": case["name"], "items": []}
    from app.db.client import pool as dbpool
    async with dbpool().acquire() as conn:
        rows = await conn.fetch(
            "select id, full_name, weapon, skin_name, rarity, rarity_color, image_url, base_price "
            "from economy_skins_catalog where id = any($1::int[])",
            all_ids,
        )
    items = [
        {
            "id": int(r["id"]),
            "name": r["full_name"],
            "rarity": r["rarity"],
            "rarity_color": r["rarity_color"],
            "rarity_emoji": rarity_emoji(r["rarity"]),
            "image_url": r["image_url"],
            "base_price": int(r["base_price"]),
        }
        for r in rows
    ]
    items.sort(key=lambda x: x["base_price"], reverse=True)
    return {
        "id": int(case["id"]),
        "name": case["name"],
        "description": case["description"],
        "price": int(case["price"]),
        "items": items,
    }


@router.get("/inventory")
async def api_inventory(user: dict = Depends(require_user)) -> dict:
    tg_id = int(user["id"])
    items = await eco.inventory_of(tg_id, limit=500)
    total_value = sum(int(i["price"]) for i in items)
    return {
        "count": len(items),
        "total_value": total_value,
        "items": [_skin_to_dict(i) for i in items],
    }


# ============ sell ============
class SellReq(BaseModel):
    inventory_id: int = Field(..., ge=1)


@router.post("/sell")
async def api_sell(req: SellReq, user: dict = Depends(require_user)) -> dict:
    tg_id = int(user["id"])
    result = await eco.sell_to_dealer(tg_id, req.inventory_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


class BulkSellReq(BaseModel):
    inventory_ids: list[int]


@router.post("/sell_bulk")
async def api_sell_bulk(req: BulkSellReq, user: dict = Depends(require_user)) -> dict:
    tg_id = int(user["id"])
    if not req.inventory_ids:
        raise HTTPException(status_code=400, detail="empty list")
    result = await eco.sell_bulk_to_dealer(tg_id, req.inventory_ids)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


# ============ upgrade ============
class UpgradeReq(BaseModel):
    inventory_id: int = Field(..., ge=1)
    target_skin_id: int = Field(..., ge=1)
    extra_coins: int = Field(default=0, ge=0)


@router.post("/upgrade")
async def api_upgrade(req: UpgradeReq, user: dict = Depends(require_user)) -> dict:
    tg_id = int(user["id"])
    result = await eco.upgrade_item(tg_id, req.inventory_id, req.target_skin_id, req.extra_coins)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


@router.get("/upgrade/candidates")
async def api_upgrade_candidates(
    min_price: int = 0, max_price: int = 1_000_000_000, limit: int = 80,
    user: dict = Depends(require_user),
) -> list[dict]:
    """Return skins whose median base_price is in the requested range."""
    _ = user
    from app.db.client import pool as dbpool
    async with dbpool().acquire() as conn:
        rows = await conn.fetch(
            "select id, full_name, weapon, skin_name, rarity, rarity_color, image_url, base_price "
            "from economy_skins_catalog where active and base_price between $1 and $2 "
            "order by base_price asc limit $3",
            int(min_price), int(max_price), int(min(200, limit)),
        )
    return [
        {
            "id": int(r["id"]),
            "name": r["full_name"],
            "weapon": r["weapon"],
            "skin_name": r["skin_name"],
            "rarity": r["rarity"],
            "rarity_color": r["rarity_color"],
            "image_url": r["image_url"],
            "base_price": int(r["base_price"]),
        }
        for r in rows
    ]


# ============ casino ============
class CoinflipReq(BaseModel):
    bet: int = Field(..., ge=1)
    side: str


@router.post("/casino/coinflip")
async def api_coinflip(req: CoinflipReq, user: dict = Depends(require_user)) -> dict:
    tg_id = int(user["id"])
    result = await eco.play_coinflip(tg_id, req.bet, req.side)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


class SlotsReq(BaseModel):
    bet: int = Field(..., ge=1)


@router.post("/casino/slots")
async def api_slots(req: SlotsReq, user: dict = Depends(require_user)) -> dict:
    tg_id = int(user["id"])
    result = await eco.play_slots(tg_id, req.bet)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


class CrashReq(BaseModel):
    bet: int = Field(..., ge=1)
    target_mult: float = Field(..., ge=1.01, le=50.0)


@router.post("/casino/crash")
async def api_crash(req: CrashReq, user: dict = Depends(require_user)) -> dict:
    tg_id = int(user["id"])
    result = await eco.play_crash(tg_id, req.bet, req.target_mult)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


# ============ daily task ============
@router.get("/task")
async def api_task(user: dict = Depends(require_user)) -> dict:
    tg_id = int(user["id"])
    return await eco.get_or_create_daily_task(tg_id)


class TaskAnswerReq(BaseModel):
    answer: str


@router.post("/task/answer")
async def api_task_answer(req: TaskAnswerReq, user: dict = Depends(require_user)) -> dict:
    tg_id = int(user["id"])
    return await eco.submit_daily_task(tg_id, req.answer)


@router.get("/leaderboard")
async def api_leaderboard(user: dict = Depends(require_user)) -> list[dict]:
    _ = user
    import json as _json
    from app.economy.boss import BADGES as _BADGES
    top = await eco.leaderboard_rich(limit=20)
    out = []
    for r in top:
        raw_badges = r.get("badges") or []
        if isinstance(raw_badges, str):
            try:
                raw_badges = _json.loads(raw_badges)
            except Exception:
                raw_badges = []
        # Resolve badge keys to display objects (icon + name + rarity)
        badges = [_BADGES[k] for k in raw_badges if k in _BADGES]
        out.append({
            "tg_id":        int(r["tg_id"]),
            "username":     r["username"],
            "first_name":   r["first_name"],
            "balance":      int(r["balance"]),
            "cases_opened": int(r["cases_opened"]),
            "streak":       int(r["current_streak"]),
            "badges":       badges,
        })
    return out


# ================ retention: achievements / missions / wheel / pvp ================
from app.economy import retention as rt
from pydantic import BaseModel as _BM


@router.get("/achievements")
async def api_achievements(user: dict = Depends(require_user)) -> dict:
    tg_id = int(user["id"])
    await eco.ensure_user(tg_id)
    items = await rt.list_achievements_for_user(tg_id)
    from app.db.client import pool as dbpool
    async with dbpool().acquire() as conn:
        u = await conn.fetchrow(
            "select active_title, level, xp from economy_users where tg_id = $1", tg_id,
        )
    return {
        "active_title": (u["active_title"] if u else None),
        "level": int(u["level"]) if u else 1,
        "xp": int(u["xp"]) if u else 0,
        "items": items,
    }


class TitleReq(_BM):
    title: str | None = None


@router.post("/achievements/title")
async def api_set_title(req: TitleReq, user: dict = Depends(require_user)) -> dict:
    tg_id = int(user["id"])
    ok = await rt.set_active_title(tg_id, req.title)
    return {"ok": ok}


@router.get("/missions")
async def api_missions(user: dict = Depends(require_user)) -> dict:
    tg_id = int(user["id"])
    return await rt.get_or_create_missions(tg_id)


@router.post("/missions/claim_final")
async def api_missions_claim(user: dict = Depends(require_user)) -> dict:
    tg_id = int(user["id"])
    return await rt.claim_final_mission_reward(tg_id)


@router.get("/wheel")
async def api_wheel_status(user: dict = Depends(require_user)) -> dict:
    tg_id = int(user["id"])
    return await rt.wheel_status(tg_id)


@router.post("/wheel/spin")
async def api_wheel_spin(user: dict = Depends(require_user)) -> dict:
    tg_id = int(user["id"])
    result = await rt.spin_wheel(tg_id)
    return result


@router.get("/level")
async def api_level(user: dict = Depends(require_user)) -> dict:
    tg_id = int(user["id"])
    from app.db.client import pool as dbpool
    async with dbpool().acquire() as conn:
        row = await conn.fetchrow(
            "select xp, level from economy_users where tg_id = $1", tg_id,
        )
    xp = int(row["xp"]) if row else 0
    lvl = int(row["level"]) if row else 1
    return {
        "xp": xp,
        "level": lvl,
        "next_level_xp": rt.xp_for_level(lvl + 1),
        "current_level_xp": rt.xp_for_level(lvl),
        "perks": {k: v for k, v in rt.LEVEL_PERKS.items() if k <= lvl + 5},
    }


@router.get("/pvp/leaderboard")
async def api_pvp_leaderboard(metric: str = "total_winnings", user: dict = Depends(require_user)) -> list[dict]:
    _ = user
    return await rt.pvp_leaderboard(metric=metric, limit=10)


# ================ FORGE ================
from app.economy import forge as _forge


@router.get("/forge/state")
async def api_forge_state(user: dict = Depends(require_user)) -> dict:
    return await _forge.get_state(int(user["id"]))


@router.post("/forge/hit")
async def api_forge_hit(user: dict = Depends(require_user)) -> dict:
    return await _forge.hit(int(user["id"]))


class ForgeHitBatchReq(BaseModel):
    count: int = Field(..., ge=1, le=30)


@router.post("/forge/hit_batch")
async def api_forge_hit_batch(req: ForgeHitBatchReq, user: dict = Depends(require_user)) -> dict:
    return await _forge.hit_batch(int(user["id"]), req.count)


@router.post("/forge/claim_afk")
async def api_forge_claim_afk(user: dict = Depends(require_user)) -> dict:
    return await _forge.claim_afk(int(user["id"]))


@router.post("/forge/skip")
async def api_forge_skip(user: dict = Depends(require_user)) -> dict:
    return await _forge.skip_weapon(int(user["id"]))


class ForgeUpgradeReq(_BM):
    branch: str


@router.post("/forge/upgrade")
async def api_forge_upgrade(req: ForgeUpgradeReq, user: dict = Depends(require_user)) -> dict:
    return await _forge.buy_upgrade(int(user["id"]), req.branch)


class ForgeExchangeReq(_BM):
    particles: int


@router.post("/forge/exchange")
async def api_forge_exchange(req: ForgeExchangeReq, user: dict = Depends(require_user)) -> dict:
    return await _forge.exchange(int(user["id"]), req.particles)


@router.get("/forge/tree")
async def api_forge_tree(user: dict = Depends(require_user)) -> list[dict]:
    _ = user
    return _forge.get_branches_info()


@router.get("/forge/leaderboard")
async def api_forge_leaderboard(user: dict = Depends(require_user)) -> list[dict]:
    _ = user
    return await _forge.leaderboard(limit=20)


# ================ PRESTIGE ================
from app.economy import prestige as _prestige


@router.get("/prestige/state")
async def api_prestige_state(user: dict = Depends(require_user)) -> dict:
    return await _prestige.get_state(int(user["id"]))


@router.post("/prestige/do")
async def api_prestige_do(user: dict = Depends(require_user)) -> dict:
    return await _prestige.do_prestige(int(user["id"]))


class PrestigeBuyReq(BaseModel):
    branch: str


@router.post("/prestige/buy")
async def api_prestige_buy(req: PrestigeBuyReq, user: dict = Depends(require_user)) -> dict:
    return await _prestige.buy_upgrade(int(user["id"]), req.branch)


# ================ GEAR ================
from app.economy import gear as _gear


@router.get("/gear/shop")
async def api_gear_shop(user: dict = Depends(require_user)) -> dict:
    return await _gear.get_shop(int(user["id"]))


@router.get("/gear/inventory")
async def api_gear_inventory(user: dict = Depends(require_user)) -> dict:
    return await _gear.get_inventory(int(user["id"]))


class GearBuyReq(BaseModel):
    item_key: str


@router.post("/gear/buy")
async def api_gear_buy(req: GearBuyReq, user: dict = Depends(require_user)) -> dict:
    return await _gear.buy(int(user["id"]), req.item_key)


class GearInvIdReq(BaseModel):
    inv_id: int


@router.post("/gear/equip")
async def api_gear_equip(req: GearInvIdReq, user: dict = Depends(require_user)) -> dict:
    return await _gear.equip(int(user["id"]), req.inv_id)


@router.post("/gear/unequip")
async def api_gear_unequip(req: GearInvIdReq, user: dict = Depends(require_user)) -> dict:
    return await _gear.unequip(int(user["id"]), req.inv_id)


@router.post("/gear/sell")
async def api_gear_sell(req: GearInvIdReq, user: dict = Depends(require_user)) -> dict:
    return await _gear.sell(int(user["id"]), req.inv_id)


# ================ MEGASLOT (CS Gates — Zeus-style) ================
from app.economy import megaslot as _megaslot


class MegaslotSpinReq(BaseModel):
    bet: int = Field(..., ge=1, le=10_000)
    bonus_buy: bool = False
    bonus_type: str = "regular"  # regular | premium


@router.post("/casino/megaslot/spin")
async def api_megaslot_spin(req: MegaslotSpinReq, user: dict = Depends(require_user)) -> dict:
    return await _megaslot.spin(int(user["id"]), req.bet, req.bonus_buy, req.bonus_type)


@router.get("/casino/megaslot/config")
async def api_megaslot_config(user: dict = Depends(require_user)) -> dict:
    _ = user
    return await _megaslot.get_config()


# ================ BOSS RAIDS ================
from app.economy import boss as _boss


@router.get("/forge/boss/state")
async def api_boss_state(user: dict = Depends(require_user)) -> dict:
    return await _boss.get_state(int(user["id"]))


class BossAttackReq(BaseModel):
    taps: int = Field(default=1, ge=1, le=50)


@router.post("/forge/boss/attack")
async def api_boss_attack(req: BossAttackReq, user: dict = Depends(require_user)) -> dict:
    return await _boss.attack(int(user["id"]), req.taps)


class BossUpgradeReq(BaseModel):
    branch: str


@router.post("/forge/boss/upgrade")
async def api_boss_upgrade(req: BossUpgradeReq, user: dict = Depends(require_user)) -> dict:
    return await _boss.buy_boss_upgrade(int(user["id"]), req.branch)


@router.get("/forge/boss/branches")
async def api_boss_branches(user: dict = Depends(require_user)) -> list[dict]:
    _ = user
    return _boss.get_branches_info()


class BossSelectReq(BaseModel):
    tier: int = Field(..., ge=1)


@router.post("/forge/boss/select")
async def api_boss_select(req: BossSelectReq, user: dict = Depends(require_user)) -> dict:
    return await _boss.select_tier(int(user["id"]), req.tier)


# ================ MINES (CS-themed Сапёр) ================
from app.economy import mines as _mines


class MinesStartReq(BaseModel):
    bet: int = Field(..., ge=1)
    bombs: int = Field(..., ge=1, le=24)


class MinesRevealReq(BaseModel):
    cell: int = Field(..., ge=0, le=24)


@router.get("/casino/mines/config")
async def api_mines_config(user: dict = Depends(require_user)) -> dict:
    _ = user
    return await _mines.get_config()


@router.get("/casino/mines/state")
async def api_mines_state(user: dict = Depends(require_user)) -> dict:
    return await _mines.get_state(int(user["id"]))


@router.post("/casino/mines/start")
async def api_mines_start(req: MinesStartReq, user: dict = Depends(require_user)) -> dict:
    return await _mines.start_game(int(user["id"]), req.bet, req.bombs)


@router.post("/casino/mines/reveal")
async def api_mines_reveal(req: MinesRevealReq, user: dict = Depends(require_user)) -> dict:
    return await _mines.reveal(int(user["id"]), req.cell)


@router.post("/casino/mines/cashout")
async def api_mines_cashout(user: dict = Depends(require_user)) -> dict:
    return await _mines.cashout(int(user["id"]))


# ================ PLINKO (drop-the-grenade) ================
from app.economy import plinko as _plinko


class PlinkoPlayReq(BaseModel):
    bet: int = Field(..., ge=1)
    mode: str = "classic"  # casual | classic | savage


@router.get("/casino/plinko/config")
async def api_plinko_config(user: dict = Depends(require_user)) -> dict:
    _ = user
    return await _plinko.get_config()


@router.post("/casino/plinko/play")
async def api_plinko_play(req: PlinkoPlayReq, user: dict = Depends(require_user)) -> dict:
    return await _plinko.play_drop(int(user["id"]), req.bet, req.mode)


# ================ PVP COINFLIP (skin duels) ================
from app.economy import coinflip_pvp as _cfpvp


class CFCreateReq(BaseModel):
    inventory_ids: list[int] = Field(..., min_length=1, max_length=20)


class CFJoinReq(BaseModel):
    lobby_id: int = Field(..., ge=1)
    inventory_ids: list[int] = Field(..., min_length=1, max_length=20)


class CFLobbyIdReq(BaseModel):
    lobby_id: int = Field(..., ge=1)


@router.post("/pvp/coinflip/create")
async def api_cf_create(req: CFCreateReq, user: dict = Depends(require_user)) -> dict:
    return await _cfpvp.create_lobby(int(user["id"]), [int(i) for i in req.inventory_ids])


@router.post("/pvp/coinflip/join")
async def api_cf_join(req: CFJoinReq, user: dict = Depends(require_user)) -> dict:
    return await _cfpvp.join_lobby(int(user["id"]), int(req.lobby_id), [int(i) for i in req.inventory_ids])


@router.post("/pvp/coinflip/cancel")
async def api_cf_cancel(req: CFLobbyIdReq, user: dict = Depends(require_user)) -> dict:
    return await _cfpvp.cancel_lobby(int(user["id"]), int(req.lobby_id))


@router.get("/pvp/coinflip/list")
async def api_cf_list(user: dict = Depends(require_user)) -> dict:
    open_ = await _cfpvp.list_open_lobbies(int(user["id"]))
    recent = await _cfpvp.list_recent_settled(limit=10)
    return {"open": open_, "recent": recent}


@router.get("/pvp/coinflip/lobby/{lobby_id}")
async def api_cf_get(lobby_id: int, user: dict = Depends(require_user)) -> dict:
    _ = user
    lobby = await _cfpvp.get_lobby(int(lobby_id))
    if lobby is None:
        raise HTTPException(status_code=404, detail="Лобби не найдено")
    return lobby


@router.post("/pvp/coinflip/share")
async def api_cf_share(req: CFLobbyIdReq, user: dict = Depends(require_user)) -> dict:
    """Share an open lobby to the bot's allowed group chat. One-shot per lobby."""
    tg_id = int(user["id"])
    lobby = await _cfpvp.get_lobby(int(req.lobby_id))
    if lobby is None:
        raise HTTPException(status_code=404, detail="Лобби не найдено")
    if int(lobby["creator_id"]) != tg_id:
        raise HTTPException(status_code=403, detail="Не твоё лобби")
    if lobby["status"] != "open":
        raise HTTPException(status_code=400, detail="Лобби уже неактивно")

    # Mark as invited (idempotent — second call returns ok=False)
    mark = await _cfpvp.mark_invited_to_chat(int(req.lobby_id), tg_id)
    if not mark.get("ok"):
        return mark

    # Compose message + send to group via bot
    from app.bot import get_bot
    from app.config import get_settings
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    s = get_settings()
    if s.tg_allowed_chat_id is None:
        return {"ok": False, "error": "Group chat не настроен"}
    bot = get_bot()
    creator_name = lobby.get("creator_name") or f"user{lobby['creator_id']}"
    n_items = len(lobby.get("creator_skins") or [])
    value = int(lobby.get("creator_value") or 0)

    # Build deep-link to mini app with start_param so the receiver auto-opens this lobby
    # Format: t.me/<bot>/<app>?startapp=cf_<id>  (Telegram passes start_param to webapp)
    if s.miniapp_tme_url:
        deep_link = f"{s.miniapp_tme_url}?startapp=cf_{int(req.lobby_id)}"
    else:
        deep_link = (s.miniapp_url or "")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⚔️ Принять вызов", url=deep_link),
    ]])
    text = (
        f"⚔️ <b>{creator_name}</b> бросил вызов в Coinflip!\n\n"
        f"🎒 Ставка: <b>{n_items}</b> предмет(ов) на <b>{value:,} 🪙</b>\n"
        f"⚖️ Матч: ±10% по стоимости\n"
        f"⏱ Лобби активно 24ч\n\n"
        f"Кто рискнёт?"
    )
    try:
        await bot.send_message(s.tg_allowed_chat_id, text, reply_markup=keyboard)
    except Exception as e:
        log.warning("coinflip share send_message failed: %s", e)
        return {"ok": False, "error": "Не удалось отправить в чат"}
    return {"ok": True}
