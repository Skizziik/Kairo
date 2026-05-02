"""Core Village Tycoon game logic. All numbers server-side, lazy idle compute."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from app.db.client import pool
from app.villager import config_loader as cfg

log = logging.getLogger(__name__)


def _parse_jsonb(val: Any) -> Any:
    """asyncpg without a custom codec returns jsonb as text string."""
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


# ---------- helpers ---------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _building_def(btype: str) -> dict | None:
    return cfg.buildings().get(btype)


def _level_def(btype: str, level: int) -> dict | None:
    bdef = _building_def(btype)
    if not bdef:
        return None
    levels = bdef.get("levels", [])
    if level < 1 or level > len(levels):
        return None
    return levels[level - 1]


def _is_within_map(x: int, y: int, w: int, h: int) -> bool:
    map_w, map_h = cfg.MAP_SIZE
    return 0 <= x and x + w <= map_w and 0 <= y and y + h <= map_h


# ---------- user / state ---------------------------------------------------


async def ensure_user(tg_id: int, **profile) -> dict:
    """Idempotent — creates row + initial resources + Town Hall + first quest if needed."""
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                insert into villager_users (tg_id, username, first_name, last_name, language_code, is_premium)
                values ($1, $2, $3, $4, $5, $6)
                on conflict (tg_id) do update set
                    username = coalesce(excluded.username, villager_users.username),
                    first_name = coalesce(excluded.first_name, villager_users.first_name),
                    last_name = coalesce(excluded.last_name, villager_users.last_name),
                    language_code = coalesce(excluded.language_code, villager_users.language_code),
                    is_premium = excluded.is_premium,
                    last_seen_at = now()
                returning *, (xmax = 0) as inserted
                """,
                tg_id,
                profile.get("username"),
                profile.get("first_name"),
                profile.get("last_name"),
                profile.get("language_code") or "ru",
                bool(profile.get("is_premium", False)),
            )

            if not row["inserted"]:
                return dict(row)

            # First time — seed initial state.
            for rtype, rdef in cfg.resources().items():
                init_amount = 100 if rtype in ("wood", "stone", "food", "water") else (
                    50 if rtype == "gold" else (10 if rtype == "gems" else 0)
                )
                await conn.execute(
                    """insert into villager_resources (tg_id, resource_type, amount, cap)
                       values ($1, $2, $3, $4)
                       on conflict (tg_id, resource_type) do nothing""",
                    tg_id, rtype, Decimal(init_amount), Decimal(rdef["base_cap"]),
                )

            # Place Town Hall in the center of the 16x16 map: (6,6) for 3x3 = covers 6..8.
            await conn.execute(
                """insert into villager_buildings (tg_id, building_type, level, position_x, position_y, status)
                   values ($1, 'townhall', 1, 6, 6, 'active')
                   on conflict do nothing""",
                tg_id,
            )

            # Auto-start onboarding quests.
            for qid, qdef in cfg.quests().items():
                if qdef.get("auto_start"):
                    await conn.execute(
                        """insert into villager_quests_progress (tg_id, quest_id, status, progress)
                           values ($1, $2, 'active', '{}'::jsonb)
                           on conflict do nothing""",
                        tg_id, qid,
                    )

    return dict(row)


# ---------- idle ------------------------------------------------------------


def _compute_storage_cap(buildings_rows: list[dict]) -> dict[str, Decimal]:
    """Total cap per resource = base + sum of storage_bonus from active buildings."""
    caps: dict[str, Decimal] = {
        rtype: Decimal(rdef["base_cap"]) for rtype, rdef in cfg.resources().items()
    }
    for b in buildings_rows:
        if b["status"] != "active":
            continue
        ldef = _level_def(b["building_type"], b["level"])
        if not ldef:
            continue
        for rtype, bonus in (ldef.get("storage_bonus") or {}).items():
            caps[rtype] = caps.get(rtype, Decimal(0)) + Decimal(bonus)
    return caps


def _idle_pending(building: dict, now: datetime, online: bool) -> dict[str, Decimal]:
    """How much each resource this building has produced since last collect."""
    if building["status"] != "active":
        return {}
    ldef = _level_def(building["building_type"], building["level"])
    if not ldef:
        return {}
    output = ldef.get("output_per_hour") or {}
    if not output:
        return {}
    last = building["last_collected_at"]
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    delta_seconds = max(0.0, (now - last).total_seconds())
    delta_seconds = min(delta_seconds, cfg.OFFLINE_CAP_HOURS * 3600)
    efficiency = 1.0 if online else cfg.OFFLINE_EFFICIENCY
    delta_hours = (delta_seconds / 3600.0) * efficiency
    pending: dict[str, Decimal] = {}
    for rtype, per_hour in output.items():
        amount = Decimal(per_hour) * Decimal(str(delta_hours))
        if amount > 0:
            pending[rtype] = amount.quantize(Decimal("1"))  # int-cents
    return pending


async def _process_finished_jobs(conn, tg_id: int, now: datetime) -> list[dict]:
    """Promote 'building' → 'active' and 'upgrading' → next level for jobs whose finish_at passed."""
    rows = await conn.fetch(
        """select * from villager_buildings
           where tg_id = $1 and status in ('building', 'upgrading') and finish_at <= $2
           for update""",
        tg_id, now,
    )
    finished = []
    for row in rows:
        if row["status"] == "building":
            updated = await conn.fetchrow(
                """update villager_buildings
                   set status = 'active',
                       finish_at = null,
                       last_collected_at = $2
                   where id = $1
                   returning *""",
                row["id"], now,
            )
        else:  # upgrading
            updated = await conn.fetchrow(
                """update villager_buildings
                   set level = level + 1,
                       status = 'active',
                       finish_at = null,
                       last_collected_at = $2
                   where id = $1
                   returning *""",
                row["id"], now,
            )
        finished.append(dict(updated))
    return finished


# ---------- snapshot --------------------------------------------------------


def _serialize_building(b: dict, pending: dict[str, Decimal]) -> dict:
    return {
        "id": int(b["id"]),
        "type": b["building_type"],
        "level": int(b["level"]),
        "x": int(b["position_x"]),
        "y": int(b["position_y"]),
        "status": b["status"],
        "finish_at": b["finish_at"].isoformat() if b["finish_at"] else None,
        "pending_collect": {k: str(v) for k, v in pending.items()},
    }


async def get_state(tg_id: int) -> dict:
    """Full snapshot for the client. Lazy-processes idle income + finished jobs."""
    now = _now()
    async with pool().acquire() as conn:
        async with conn.transaction():
            # Promote any finished build/upgrade jobs first.
            user_row = await conn.fetchrow(
                "select * from villager_users where tg_id = $1 for update", tg_id,
            )
            if not user_row:
                raise RuntimeError("ensure_user must be called first")

            await _process_finished_jobs(conn, tg_id, now)

            buildings_rows = [
                dict(r) for r in await conn.fetch(
                    "select * from villager_buildings where tg_id = $1 order by id", tg_id,
                )
            ]
            resources_rows = [
                dict(r) for r in await conn.fetch(
                    "select * from villager_resources where tg_id = $1", tg_id,
                )
            ]
            quests_rows = [
                dict(r) for r in await conn.fetch(
                    "select * from villager_quests_progress where tg_id = $1", tg_id,
                )
            ]

            await conn.execute(
                "update villager_users set last_seen_at = $2, last_sync_at = $2 where tg_id = $1",
                tg_id, now,
            )

    last_seen = user_row["last_seen_at"]
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    online = (now - last_seen).total_seconds() < cfg.ONLINE_THRESHOLD_SECONDS

    storage_caps = _compute_storage_cap(buildings_rows)
    serialized_buildings = []
    pending_total: dict[str, Decimal] = {}
    for b in buildings_rows:
        pending = _idle_pending(b, now, online)
        for k, v in pending.items():
            pending_total[k] = pending_total.get(k, Decimal(0)) + v
        serialized_buildings.append(_serialize_building(b, pending))

    serialized_resources = []
    res_map = {r["resource_type"]: r for r in resources_rows}
    for rtype, rdef in cfg.resources().items():
        row = res_map.get(rtype)
        amount = row["amount"] if row else Decimal(0)
        cap = storage_caps.get(rtype, Decimal(rdef["base_cap"]))
        serialized_resources.append({
            "type": rtype,
            "amount": str(amount),
            "cap": str(cap),
        })

    serialized_quests = []
    qdefs = cfg.quests()
    for q in quests_rows:
        qdef = qdefs.get(q["quest_id"])
        if not qdef:
            continue
        serialized_quests.append({
            "id": q["quest_id"],
            "name": qdef["name"],
            "description": qdef["description"],
            "status": q["status"],
            "progress": _parse_jsonb(q["progress"]) or {},
            "rewards": qdef["rewards"],
            "claimed_at": q["claimed_at"].isoformat() if q["claimed_at"] else None,
        })

    builder_slots = cfg.DEFAULT_BUILDER_SLOTS + sum(
        1 for b in buildings_rows
        if b["building_type"] == "builderhut" and b["status"] == "active"
    )

    return {
        "user": {
            "tg_id": int(user_row["tg_id"]),
            "village_name": user_row["village_name"],
            "era": int(user_row["era"]),
            "player_level": int(user_row["player_level"]),
            "experience": int(user_row["experience"]),
            "gems_balance": str(user_row["gems_balance"]),
            "first_name": user_row["first_name"],
            "username": user_row["username"],
        },
        "resources": serialized_resources,
        "buildings": serialized_buildings,
        "quests": serialized_quests,
        "pending_total": {k: str(v) for k, v in pending_total.items()},
        "builder_slots": builder_slots,
        "map_size": list(cfg.MAP_SIZE),
        "tile_size": cfg.TILE_SIZE,
        "server_time": now.isoformat(),
    }


# ---------- build / upgrade -------------------------------------------------


async def _check_position_free(conn, tg_id: int, x: int, y: int, w: int, h: int,
                                exclude_id: int | None = None) -> bool:
    rows = await conn.fetch(
        "select * from villager_buildings where tg_id = $1", tg_id,
    )
    for b in rows:
        if exclude_id is not None and b["id"] == exclude_id:
            continue
        bdef = _building_def(b["building_type"])
        if not bdef:
            continue
        bw, bh = bdef["size"]
        bx, by = b["position_x"], b["position_y"]
        # Rectangles overlap?
        if x + w > bx and bx + bw > x and y + h > by and by + bh > y:
            return False
    return True


async def _deduct_resources(conn, tg_id: int, cost: dict[str, int]) -> dict[str, str] | None:
    if not cost:
        return None
    rows = await conn.fetch(
        "select resource_type, amount from villager_resources where tg_id = $1 for update",
        tg_id,
    )
    have = {r["resource_type"]: Decimal(r["amount"]) for r in rows}
    for rtype, c in cost.items():
        if have.get(rtype, Decimal(0)) < Decimal(c):
            return rtype  # missing resource type as error marker
    for rtype, c in cost.items():
        await conn.execute(
            "update villager_resources set amount = amount - $3 where tg_id = $1 and resource_type = $2",
            tg_id, rtype, Decimal(c),
        )
    return None


async def _count_active_jobs(conn, tg_id: int) -> int:
    row = await conn.fetchrow(
        """select count(*) as n from villager_buildings
           where tg_id = $1 and status in ('building', 'upgrading')""",
        tg_id,
    )
    return int(row["n"])


async def _builder_slots(conn, tg_id: int) -> int:
    row = await conn.fetchrow(
        """select count(*) as n from villager_buildings
           where tg_id = $1 and building_type = 'builderhut' and status = 'active'""",
        tg_id,
    )
    return cfg.DEFAULT_BUILDER_SLOTS + int(row["n"])


async def build(tg_id: int, building_type: str, x: int, y: int) -> dict:
    bdef = _building_def(building_type)
    if not bdef:
        return {"ok": False, "error": "unknown_building"}
    w, h = bdef["size"]
    if not _is_within_map(x, y, w, h):
        return {"ok": False, "error": "invalid_position"}
    ldef = _level_def(building_type, 1)
    if not ldef:
        return {"ok": False, "error": "no_level_def"}

    now = _now()
    async with pool().acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow(
                "select era from villager_users where tg_id = $1 for update", tg_id,
            )
            if not user:
                return {"ok": False, "error": "no_user"}
            if int(user["era"]) < int(bdef.get("era", 1)):
                return {"ok": False, "error": "era_locked"}

            # Max-per-user limit.
            max_count = bdef.get("max_per_user")
            if max_count:
                cur = await conn.fetchrow(
                    "select count(*) as n from villager_buildings where tg_id = $1 and building_type = $2",
                    tg_id, building_type,
                )
                if int(cur["n"]) >= max_count:
                    return {"ok": False, "error": "limit_reached"}

            # Concurrent job slots.
            slots = await _builder_slots(conn, tg_id)
            in_progress = await _count_active_jobs(conn, tg_id)
            if in_progress >= slots:
                return {"ok": False, "error": "no_builder_slots"}

            # Position free?
            if not await _check_position_free(conn, tg_id, x, y, w, h):
                return {"ok": False, "error": "position_occupied"}

            # Resources.
            err = await _deduct_resources(conn, tg_id, ldef.get("cost") or {})
            if err is not None:
                return {"ok": False, "error": "not_enough_resources", "missing": err}

            build_time = int(ldef.get("build_time_seconds", 0))
            status = "active" if build_time == 0 else "building"
            finish_at = None if status == "active" else _now()
            if finish_at is not None:
                from datetime import timedelta
                finish_at = finish_at + timedelta(seconds=build_time)

            row = await conn.fetchrow(
                """insert into villager_buildings
                   (tg_id, building_type, level, position_x, position_y, status, finish_at, last_collected_at)
                   values ($1, $2, 1, $3, $4, $5, $6, $7)
                   returning *""",
                tg_id, building_type, x, y, status, finish_at, now,
            )

            await _quest_event(conn, tg_id, "build", {"building_type": building_type})
            await _log(conn, tg_id, "building_created", {
                "type": building_type, "x": x, "y": y, "build_time": build_time,
            })

    state = await get_state(tg_id)
    return {"ok": True, "data": {"state": state, "new_building_id": int(row["id"])}}


async def upgrade(tg_id: int, building_id: int) -> dict:
    now = _now()
    async with pool().acquire() as conn:
        async with conn.transaction():
            b = await conn.fetchrow(
                "select * from villager_buildings where id = $1 and tg_id = $2 for update",
                building_id, tg_id,
            )
            if not b:
                return {"ok": False, "error": "not_found"}
            if b["status"] != "active":
                return {"ok": False, "error": "busy"}
            bdef = _building_def(b["building_type"])
            if not bdef:
                return {"ok": False, "error": "unknown_building"}
            current_level = int(b["level"])
            if current_level >= bdef.get("max_level", 1):
                return {"ok": False, "error": "max_level"}

            next_ldef = _level_def(b["building_type"], current_level + 1)
            if not next_ldef:
                return {"ok": False, "error": "no_next_level"}

            slots = await _builder_slots(conn, tg_id)
            in_progress = await _count_active_jobs(conn, tg_id)
            if in_progress >= slots:
                return {"ok": False, "error": "no_builder_slots"}

            err = await _deduct_resources(conn, tg_id, next_ldef.get("cost") or {})
            if err is not None:
                return {"ok": False, "error": "not_enough_resources", "missing": err}

            build_time = int(next_ldef.get("build_time_seconds", 0))
            from datetime import timedelta
            finish_at = now + timedelta(seconds=build_time) if build_time > 0 else None
            status = "upgrading" if build_time > 0 else "active"
            new_level = current_level if build_time > 0 else current_level + 1

            await conn.execute(
                """update villager_buildings
                   set status = $2, finish_at = $3, level = $4
                   where id = $1""",
                building_id, status, finish_at, new_level,
            )
            if build_time == 0:
                # Instant upgrade: collect timer reset.
                await conn.execute(
                    "update villager_buildings set last_collected_at = $2 where id = $1",
                    building_id, now,
                )

            if build_time == 0:
                await _quest_event(conn, tg_id, "upgrade", {
                    "building_type": b["building_type"], "level": new_level,
                })
            await _log(conn, tg_id, "building_upgrade_started", {
                "id": int(building_id), "type": b["building_type"],
                "from": current_level, "to": current_level + 1,
            })

    state = await get_state(tg_id)
    return {"ok": True, "data": {"state": state}}


async def collect_all(tg_id: int) -> dict:
    """Collect pending idle income from all active buildings, capped by storage."""
    now = _now()
    async with pool().acquire() as conn:
        async with conn.transaction():
            user_row = await conn.fetchrow(
                "select * from villager_users where tg_id = $1 for update", tg_id,
            )
            if not user_row:
                return {"ok": False, "error": "no_user"}

            await _process_finished_jobs(conn, tg_id, now)

            buildings_rows = [
                dict(r) for r in await conn.fetch(
                    "select * from villager_buildings where tg_id = $1 for update", tg_id,
                )
            ]
            res_rows = [
                dict(r) for r in await conn.fetch(
                    "select * from villager_resources where tg_id = $1 for update", tg_id,
                )
            ]

            last_seen = user_row["last_seen_at"]
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            online = (now - last_seen).total_seconds() < cfg.ONLINE_THRESHOLD_SECONDS

            storage_caps = _compute_storage_cap(buildings_rows)
            current_amounts = {r["resource_type"]: Decimal(r["amount"]) for r in res_rows}
            collected: dict[str, Decimal] = {}

            for b in buildings_rows:
                pending = _idle_pending(b, now, online)
                if not pending:
                    continue
                for rtype, amount in pending.items():
                    cap = storage_caps.get(rtype, Decimal(0))
                    cur = current_amounts.get(rtype, Decimal(0))
                    can_add = max(Decimal(0), cap - cur)
                    add = min(amount, can_add)
                    if add > 0:
                        current_amounts[rtype] = cur + add
                        collected[rtype] = collected.get(rtype, Decimal(0)) + add
                # Reset timer regardless (overflow loss).
                await conn.execute(
                    "update villager_buildings set last_collected_at = $2 where id = $1",
                    b["id"], now,
                )

            # Persist resource updates.
            for rtype, new_amount in current_amounts.items():
                await conn.execute(
                    """update villager_resources set amount = $3
                       where tg_id = $1 and resource_type = $2""",
                    tg_id, rtype, new_amount,
                )

            # Quest event (collect).
            for rtype, amt in collected.items():
                await _quest_event(conn, tg_id, "collect", {
                    "resource": rtype, "amount": int(amt),
                })

            await _log(conn, tg_id, "collect_all", {
                "collected": {k: str(v) for k, v in collected.items()},
            })

    state = await get_state(tg_id)
    return {"ok": True, "data": {
        "collected": {k: str(v) for k, v in collected.items()},
        "state": state,
    }}


async def demolish(tg_id: int, building_id: int) -> dict:
    """Remove a building. Returns 50% of last-level cost as refund."""
    async with pool().acquire() as conn:
        async with conn.transaction():
            b = await conn.fetchrow(
                "select * from villager_buildings where id = $1 and tg_id = $2 for update",
                building_id, tg_id,
            )
            if not b:
                return {"ok": False, "error": "not_found"}
            bdef = _building_def(b["building_type"])
            if not bdef or not bdef.get("is_demolishable", True):
                return {"ok": False, "error": "not_demolishable"}

            ldef = _level_def(b["building_type"], int(b["level"]))
            cost = (ldef or {}).get("cost") or {}
            refund: dict[str, int] = {}
            for rtype, c in cost.items():
                refund[rtype] = int(c) // 2
                await conn.execute(
                    """update villager_resources set
                          amount = least(amount + $3, cap)
                       where tg_id = $1 and resource_type = $2""",
                    tg_id, rtype, Decimal(refund[rtype]),
                )

            await conn.execute("delete from villager_buildings where id = $1", building_id)
            await _log(conn, tg_id, "building_demolished", {
                "id": int(building_id), "type": b["building_type"], "refund": refund,
            })

    state = await get_state(tg_id)
    return {"ok": True, "data": {"state": state, "refund": refund}}


async def move(tg_id: int, building_id: int, x: int, y: int) -> dict:
    async with pool().acquire() as conn:
        async with conn.transaction():
            b = await conn.fetchrow(
                "select * from villager_buildings where id = $1 and tg_id = $2 for update",
                building_id, tg_id,
            )
            if not b:
                return {"ok": False, "error": "not_found"}
            bdef = _building_def(b["building_type"])
            if not bdef:
                return {"ok": False, "error": "unknown_building"}
            w, h = bdef["size"]
            if not _is_within_map(x, y, w, h):
                return {"ok": False, "error": "invalid_position"}
            if not await _check_position_free(conn, tg_id, x, y, w, h, exclude_id=int(b["id"])):
                return {"ok": False, "error": "position_occupied"}
            await conn.execute(
                "update villager_buildings set position_x = $2, position_y = $3 where id = $1",
                building_id, x, y,
            )
    state = await get_state(tg_id)
    return {"ok": True, "data": {"state": state}}


# ---------- quests ----------------------------------------------------------


async def _quest_event(conn, tg_id: int, trigger: str, data: dict) -> None:
    """Update quest progress + auto-complete when target reached."""
    rows = await conn.fetch(
        """select * from villager_quests_progress
           where tg_id = $1 and status = 'active'""",
        tg_id,
    )
    qdefs = cfg.quests()
    for r in rows:
        qid = r["quest_id"]
        qdef = qdefs.get(qid)
        if not qdef or qdef.get("trigger") != trigger:
            continue
        target = qdef.get("target") or {}
        progress = dict(_parse_jsonb(r["progress"]) or {})

        completed = False
        if trigger == "build" and data.get("building_type") == target.get("building_type"):
            current = int(progress.get("count", 0)) + 1
            progress["count"] = current
            if current >= int(target.get("count", 1)):
                completed = True
        elif trigger == "upgrade" and data.get("building_type") == target.get("building_type"):
            if int(data.get("level", 0)) >= int(target.get("level", 0)):
                completed = True
        elif trigger == "collect" and data.get("resource") == target.get("resource"):
            current = int(progress.get("amount", 0)) + int(data.get("amount", 0))
            progress["amount"] = current
            if current >= int(target.get("amount", 0)):
                completed = True

        new_status = "completed" if completed else "active"
        await conn.execute(
            """update villager_quests_progress
               set progress = $3::jsonb,
                   status = $4,
                   completed_at = case when $4 = 'completed' then now() else completed_at end
               where tg_id = $1 and quest_id = $2""",
            tg_id, qid, json.dumps(progress), new_status,
        )


async def quest_claim(tg_id: int, quest_id: str) -> dict:
    qdefs = cfg.quests()
    qdef = qdefs.get(quest_id)
    if not qdef:
        return {"ok": False, "error": "unknown_quest"}
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """select * from villager_quests_progress
                   where tg_id = $1 and quest_id = $2 for update""",
                tg_id, quest_id,
            )
            if not row:
                return {"ok": False, "error": "not_started"}
            if row["status"] != "completed":
                return {"ok": False, "error": "not_completed"}
            if row["claimed_at"]:
                return {"ok": False, "error": "already_claimed"}

            rewards = qdef.get("rewards") or {}
            for rtype, amt in rewards.items():
                if rtype == "experience":
                    await conn.execute(
                        "update villager_users set experience = experience + $2 where tg_id = $1",
                        tg_id, int(amt),
                    )
                elif rtype == "gems":
                    await conn.execute(
                        "update villager_users set gems_balance = gems_balance + $2 where tg_id = $1",
                        tg_id, Decimal(amt),
                    )
                else:
                    await conn.execute(
                        """update villager_resources
                              set amount = least(amount + $3, cap)
                           where tg_id = $1 and resource_type = $2""",
                        tg_id, rtype, Decimal(amt),
                    )

            await conn.execute(
                """update villager_quests_progress set status = 'claimed', claimed_at = now()
                   where tg_id = $1 and quest_id = $2""",
                tg_id, quest_id,
            )

            # Auto-start next quests.
            for next_id in (qdef.get("next") or []):
                await conn.execute(
                    """insert into villager_quests_progress (tg_id, quest_id, status, progress)
                       values ($1, $2, 'active', '{}'::jsonb)
                       on conflict do nothing""",
                    tg_id, next_id,
                )

            await _log(conn, tg_id, "quest_claimed", {
                "quest_id": quest_id, "rewards": rewards,
            })

    state = await get_state(tg_id)
    return {"ok": True, "data": {"state": state, "rewards": rewards}}


# ---------- log -------------------------------------------------------------


async def _log(conn, tg_id: int, event_type: str, data: dict) -> None:
    try:
        await conn.execute(
            "insert into villager_event_log (tg_id, event_type, data) values ($1, $2, $3::jsonb)",
            tg_id, event_type, json.dumps(data, default=str),
        )
    except Exception:
        log.exception("villager event log failed")


# ---------- config snapshot for client --------------------------------------


def public_config() -> dict:
    return {
        "version": "0.1.0",
        "buildings": cfg.buildings(),
        "resources": cfg.resources(),
        "quests": cfg.quests(),
        "map_size": list(cfg.MAP_SIZE),
        "tile_size": cfg.TILE_SIZE,
    }
