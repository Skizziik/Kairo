"""Skin Coinflip 1v1 — PvP duel: two players stake skins, server rolls 50/50, winner takes all.

Lifecycle:
    open → (matched) → settled (skins transferred to winner)
                    → cancelled (creator cancels before opponent joins)
                    → expired (24h timer; skins auto-unlock)

Provably fair: server stores `server_seed` (random hex) and `roll_value` (0..1).
Loser can reproduce: probability of winning = creator_value / pot_value
(weighted by stake — bigger stack has proportionally higher chance, like real
CS coinflip). Roll is deterministic from server_seed.

Inventory locking: when a player commits skins to a lobby, those rows get
`coinflip_lobby_id = X`. They can't be sold/listed/used in another lobby until
the lobby resolves (any terminal state clears the lock or transfers ownership).
"""
from __future__ import annotations

import json
import logging
import random
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.client import pool

log = logging.getLogger(__name__)


# ============================================================
# CONFIG
# ============================================================

MIN_STAKE_VALUE   = 100         # absolute floor — too small isn't worth the trouble
MAX_LOBBIES_OPEN  = 3           # per-creator concurrent open lobbies
MATCH_TOLERANCE   = 0.10        # opponent value must be within ±10% of creator
LOBBY_TTL_HOURS   = 24


# ============================================================
# SCHEMA
# ============================================================

async def ensure_schema() -> None:
    sql_path = Path(__file__).parent.parent / "db" / "migration_coinflip.sql"
    if not sql_path.exists():
        log.warning("coinflip migration SQL missing")
        return
    sql = sql_path.read_text(encoding="utf-8")
    async with pool().acquire() as conn:
        await conn.execute(sql)
    log.info("coinflip schema ensured")


# ============================================================
# HELPERS
# ============================================================

def _row_to_lobby(r: dict, *, hide_skins_for: int | None = None) -> dict:
    """Serialize a lobby row to JSON-friendly dict for the API.
    `hide_skins_for` = if set, hides opponent's exact skin list while open
    (so a watcher can't spy on what they'd face). For now we always show.
    """
    creator_skins = r["creator_skins"] if isinstance(r["creator_skins"], list) else json.loads(r["creator_skins"] or "[]")
    opp_skins_raw = r.get("opponent_skins")
    opp_skins = (opp_skins_raw if isinstance(opp_skins_raw, list)
                 else (json.loads(opp_skins_raw) if opp_skins_raw else None))
    return {
        "id":             int(r["id"]),
        "creator_id":     int(r["creator_id"]),
        "creator_skin_ids":  creator_skins,
        "creator_value":  int(r["creator_value"]),
        "opponent_id":    int(r["opponent_id"]) if r.get("opponent_id") else None,
        "opponent_skin_ids": opp_skins,
        "opponent_value": int(r["opponent_value"]) if r.get("opponent_value") else None,
        "status":         r["status"],
        "winner_id":      int(r["winner_id"]) if r.get("winner_id") else None,
        "pot_value":      int(r["pot_value"]) if r.get("pot_value") else None,
        "roll_value":     float(r["roll_value"]) if r.get("roll_value") else None,
        "server_seed":    r.get("server_seed"),
        "rolled_at":      r["rolled_at"].isoformat() if r.get("rolled_at") else None,
        "created_at":     r["created_at"].isoformat() if r.get("created_at") else None,
        "expires_at":     r["expires_at"].isoformat() if r.get("expires_at") else None,
        "invited_to_chat": bool(r.get("invited_to_chat", False)),
    }


async def _expand_skins(conn, inv_ids: list[int]) -> list[dict]:
    """Fetch skin display info for a set of inventory ids (joined with catalog)."""
    if not inv_ids:
        return []
    rows = await conn.fetch(
        """
        select i.id, i.skin_id, i.price, i.float_value, i.wear, i.stat_trak,
               s.full_name, s.weapon, s.skin_name, s.rarity, s.rarity_color, s.image_url
        from economy_inventory i
        join economy_skins_catalog s on s.id = i.skin_id
        where i.id = any($1::bigint[])
        order by i.price desc
        """,
        inv_ids,
    )
    return [
        {
            "id":          int(r["id"]),
            "skin_id":     int(r["skin_id"]),
            "price":       int(r["price"]),
            "name":        r["full_name"],
            "weapon":      r["weapon"],
            "skin_name":   r["skin_name"],
            "rarity":      r["rarity"],
            "rarity_color": r["rarity_color"],
            "image_url":   r["image_url"],
            "wear":        r["wear"],
            "stat_trak":   bool(r["stat_trak"]),
            "float":       round(float(r["float_value"]), 4),
        }
        for r in rows
    ]


async def _enrich_lobby(conn, lobby: dict) -> dict:
    """Add expanded skin objects + display names for both sides."""
    out = dict(lobby)
    out["creator_skins"]  = await _expand_skins(conn, lobby["creator_skin_ids"])
    out["opponent_skins"] = await _expand_skins(conn, lobby["opponent_skin_ids"]) if lobby.get("opponent_skin_ids") else []

    # Display names (username/first_name) for both sides
    ids = [i for i in [lobby.get("creator_id"), lobby.get("opponent_id")] if i]
    if ids:
        urows = await conn.fetch(
            "select tg_id, username, first_name from users where tg_id = any($1::bigint[])",
            ids,
        )
        umap = {int(r["tg_id"]): r for r in urows}
        c = umap.get(lobby["creator_id"])
        out["creator_name"] = (c["username"] or c["first_name"] or f"user{lobby['creator_id']}") if c else f"user{lobby['creator_id']}"
        if lobby.get("opponent_id"):
            o = umap.get(lobby["opponent_id"])
            out["opponent_name"] = (o["username"] or o["first_name"] or f"user{lobby['opponent_id']}") if o else None
        else:
            out["opponent_name"] = None
    return out


# ============================================================
# CREATE
# ============================================================

async def create_lobby(creator_id: int, inv_ids: list[int]) -> dict:
    if not inv_ids:
        return {"ok": False, "error": "Выбери хотя бы один скин"}
    if len(inv_ids) > 20:
        return {"ok": False, "error": "Максимум 20 предметов"}
    inv_ids = list({int(x) for x in inv_ids})

    async with pool().acquire() as conn:
        async with conn.transaction():
            # Concurrent-lobby cap
            open_count = await conn.fetchval(
                "select count(*) from coinflip_lobbies where creator_id = $1 and status = 'open'",
                creator_id,
            )
            if int(open_count or 0) >= MAX_LOBBIES_OPEN:
                return {"ok": False, "error": f"Уже создано {MAX_LOBBIES_OPEN} открытых лобби — закрой одно"}

            # Validate ownership + not locked
            rows = await conn.fetch(
                "select id, price, locked, coinflip_lobby_id "
                "from economy_inventory "
                "where id = any($1::bigint[]) and user_id = $2 for update",
                inv_ids, creator_id,
            )
            if len(rows) != len(inv_ids):
                return {"ok": False, "error": "Часть предметов не принадлежит тебе"}
            for r in rows:
                if r["locked"]:
                    return {"ok": False, "error": f"Предмет #{r['id']} заблокирован (на маркете/в трейде)"}
                if r["coinflip_lobby_id"] is not None:
                    return {"ok": False, "error": f"Предмет #{r['id']} уже в другом coinflip-лобби"}
            total_value = sum(int(r["price"]) for r in rows)
            if total_value < MIN_STAKE_VALUE:
                return {"ok": False, "error": f"Минимум {MIN_STAKE_VALUE} 🪙"}

            # Insert lobby
            lobby_id = await conn.fetchval(
                """
                insert into coinflip_lobbies (creator_id, creator_skins, creator_value)
                values ($1, $2::jsonb, $3) returning id
                """,
                creator_id, json.dumps(inv_ids), total_value,
            )
            # Lock inventory
            await conn.execute(
                "update economy_inventory set coinflip_lobby_id = $2 where id = any($1::bigint[])",
                inv_ids, lobby_id,
            )
            row = await conn.fetchrow("select * from coinflip_lobbies where id = $1", lobby_id)
            lobby = _row_to_lobby(dict(row))
            enriched = await _enrich_lobby(conn, lobby)
    return {"ok": True, "lobby": enriched}


# ============================================================
# JOIN (matches creator's stake within ±10%)
# ============================================================

async def join_lobby(opponent_id: int, lobby_id: int, inv_ids: list[int]) -> dict:
    if not inv_ids:
        return {"ok": False, "error": "Выбери скины для матча"}
    if len(inv_ids) > 20:
        return {"ok": False, "error": "Максимум 20 предметов"}
    inv_ids = list({int(x) for x in inv_ids})

    async with pool().acquire() as conn:
        async with conn.transaction():
            lrow = await conn.fetchrow(
                "select * from coinflip_lobbies where id = $1 for update", lobby_id,
            )
            if lrow is None:
                return {"ok": False, "error": "Лобби не найдено"}
            if lrow["status"] != "open":
                return {"ok": False, "error": "Лобби уже неактивно"}
            if int(lrow["creator_id"]) == opponent_id:
                return {"ok": False, "error": "Нельзя джойнить своё же лобби"}
            now = datetime.now(timezone.utc)
            if lrow["expires_at"] and lrow["expires_at"] < now:
                return {"ok": False, "error": "Лобби истекло"}

            # Validate opponent inventory
            rows = await conn.fetch(
                "select id, price, locked, coinflip_lobby_id "
                "from economy_inventory "
                "where id = any($1::bigint[]) and user_id = $2 for update",
                inv_ids, opponent_id,
            )
            if len(rows) != len(inv_ids):
                return {"ok": False, "error": "Часть предметов не твои"}
            for r in rows:
                if r["locked"] or r["coinflip_lobby_id"] is not None:
                    return {"ok": False, "error": f"Предмет #{r['id']} заблокирован"}
            opp_value = sum(int(r["price"]) for r in rows)
            cv = int(lrow["creator_value"])
            lo = int(cv * (1 - MATCH_TOLERANCE))
            hi = int(cv * (1 + MATCH_TOLERANCE))
            if opp_value < lo or opp_value > hi:
                return {
                    "ok": False,
                    "error": f"Сумма {opp_value:,} не в диапазоне ({lo:,}–{hi:,}) — нужен матч ±10% к {cv:,}",
                }

            # Mark inventory as locked to this lobby
            await conn.execute(
                "update economy_inventory set coinflip_lobby_id = $2 where id = any($1::bigint[])",
                inv_ids, lobby_id,
            )
            # Save opponent + flip to matched (settle in same tx)
            await conn.execute(
                """
                update coinflip_lobbies set
                  opponent_id    = $2,
                  opponent_skins = $3::jsonb,
                  opponent_value = $4,
                  status         = 'matched'
                where id = $1
                """,
                lobby_id, opponent_id, json.dumps(inv_ids), opp_value,
            )

            # Roll the coin RIGHT NOW to make this atomic. Provably fair:
            # P(creator_wins) = creator_value / (creator_value + opponent_value)
            # weighted by stake (bigger stack has higher chance — fair PvP).
            seed = secrets.token_hex(16)
            roll = _seed_to_unit_float(seed)
            pot_value = cv + opp_value
            creator_chance = cv / pot_value
            creator_wins = roll < creator_chance
            winner_id = int(lrow["creator_id"]) if creator_wins else opponent_id

            # Transfer ALL skins to the winner (just rewrite user_id) and clear locks
            await conn.execute(
                "update economy_inventory set user_id = $1, coinflip_lobby_id = null "
                "where coinflip_lobby_id = $2",
                winner_id, lobby_id,
            )

            # Mark lobby as settled
            await conn.execute(
                """
                update coinflip_lobbies set
                  status      = 'settled',
                  winner_id   = $2,
                  pot_value   = $3,
                  server_seed = $4,
                  roll_value  = $5,
                  rolled_at   = now()
                where id = $1
                """,
                lobby_id, winner_id, pot_value, seed, roll,
            )

            # Log transactions for both sides (positive for winner, negative for loser).
            # No coin movement — items only — but keep an audit trail.
            await conn.execute(
                "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                "values ($1, 0, 'coinflip', $2, "
                " coalesce((select balance from economy_users where tg_id = $1), 0))",
                winner_id, f"cf_win_lobby_{lobby_id}_pot_{pot_value}",
            )
            loser_id = opponent_id if creator_wins else int(lrow["creator_id"])
            await conn.execute(
                "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                "values ($1, 0, 'coinflip', $2, "
                " coalesce((select balance from economy_users where tg_id = $1), 0))",
                loser_id, f"cf_loss_lobby_{lobby_id}_pot_{pot_value}",
            )

            row = await conn.fetchrow("select * from coinflip_lobbies where id = $1", lobby_id)
            lobby = _row_to_lobby(dict(row))
            enriched = await _enrich_lobby(conn, lobby)

    # Retention hooks (outside the tx)
    try:
        from app.economy import retention as rt
        await rt.bump_stat_counter(winner_id, "cf_wins", 1)
        await rt.pvp_track(winner_id, "coinflip_won", 1)
        await rt.pvp_track(winner_id, "total_winnings", pot_value)
        await rt.bump_stat_counter(loser_id, "cf_losses", 1)
    except Exception as e:
        log.debug("coinflip retention hooks failed: %s", e)

    return {
        "ok": True,
        "lobby": enriched,
        "winner_id": winner_id,
        "creator_won": creator_wins,
        "pot_value": pot_value,
        "roll": roll,
        "creator_chance": creator_chance,
    }


def _seed_to_unit_float(seed: str) -> float:
    """Deterministic 0..1 from a hex seed. Provably fair: anyone who knows the
    seed can reproduce the roll. The seed is revealed after settle."""
    # Take first 13 hex chars → 52-bit int → divide by 2^52
    val = int(seed[:13], 16)
    return val / float(1 << 52)


# ============================================================
# CANCEL (creator only, before opponent joined)
# ============================================================

async def cancel_lobby(creator_id: int, lobby_id: int) -> dict:
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select * from coinflip_lobbies where id = $1 for update", lobby_id,
            )
            if row is None:
                return {"ok": False, "error": "Лобби не найдено"}
            if int(row["creator_id"]) != creator_id:
                return {"ok": False, "error": "Это не твоё лобби"}
            if row["status"] != "open":
                return {"ok": False, "error": "Лобби уже неактивно"}
            # Unlock skins
            await conn.execute(
                "update economy_inventory set coinflip_lobby_id = null where coinflip_lobby_id = $1",
                lobby_id,
            )
            await conn.execute(
                "update coinflip_lobbies set status = 'cancelled' where id = $1",
                lobby_id,
            )
    return {"ok": True}


# ============================================================
# EXPIRE (cleanup task — call periodically)
# ============================================================

async def expire_old() -> int:
    """Expire any lobby past its TTL while still open. Returns count."""
    async with pool().acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                "select id from coinflip_lobbies "
                "where status = 'open' and expires_at < now() for update",
            )
            ids = [int(r["id"]) for r in rows]
            if not ids:
                return 0
            await conn.execute(
                "update economy_inventory set coinflip_lobby_id = null "
                "where coinflip_lobby_id = any($1::bigint[])",
                ids,
            )
            await conn.execute(
                "update coinflip_lobbies set status = 'expired' where id = any($1::bigint[])",
                ids,
            )
    return len(ids)


# ============================================================
# LISTING / READ
# ============================================================

async def list_open_lobbies(viewer_id: int, limit: int = 30) -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            select * from coinflip_lobbies
            where status = 'open' and expires_at > now()
            order by created_at desc limit $1
            """,
            limit,
        )
        out = []
        for r in rows:
            l = _row_to_lobby(dict(r))
            l["is_mine"] = (int(r["creator_id"]) == viewer_id)
            enriched = await _enrich_lobby(conn, l)
            enriched["is_mine"] = l["is_mine"]
            out.append(enriched)
        return out


async def list_recent_settled(limit: int = 20) -> list[dict]:
    """Recent settled lobbies — for a 'recent battles' feed in the UI."""
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            select * from coinflip_lobbies
            where status = 'settled'
            order by rolled_at desc nulls last limit $1
            """,
            limit,
        )
        out = []
        for r in rows:
            l = _row_to_lobby(dict(r))
            enriched = await _enrich_lobby(conn, l)
            out.append(enriched)
        return out


async def get_lobby(lobby_id: int) -> dict | None:
    async with pool().acquire() as conn:
        row = await conn.fetchrow("select * from coinflip_lobbies where id = $1", lobby_id)
        if row is None:
            return None
        lobby = _row_to_lobby(dict(row))
        return await _enrich_lobby(conn, lobby)


# ============================================================
# SHARE TO CHAT (one-shot per lobby)
# ============================================================

async def mark_invited_to_chat(lobby_id: int, creator_id: int) -> dict:
    async with pool().acquire() as conn:
        async with conn.transaction():
            r = await conn.fetchrow(
                "select creator_id, status, invited_to_chat from coinflip_lobbies where id = $1 for update",
                lobby_id,
            )
            if r is None:
                return {"ok": False, "error": "Не найдено"}
            if int(r["creator_id"]) != creator_id:
                return {"ok": False, "error": "Не твоё лобби"}
            if r["status"] != "open":
                return {"ok": False, "error": "Лобби уже неактивно"}
            if r["invited_to_chat"]:
                return {"ok": False, "error": "Приглашение уже отправлялось"}
            await conn.execute(
                "update coinflip_lobbies set invited_to_chat = true where id = $1",
                lobby_id,
            )
    return {"ok": True}
