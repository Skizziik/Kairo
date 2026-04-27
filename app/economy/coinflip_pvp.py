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
        out["creator_name"] = (c["first_name"] or c["username"] or f"user{lobby['creator_id']}") if c else f"user{lobby['creator_id']}"
        if lobby.get("opponent_id"):
            o = umap.get(lobby["opponent_id"])
            out["opponent_name"] = (o["first_name"] or o["username"] or f"user{lobby['opponent_id']}") if o else None
        else:
            out["opponent_name"] = None
    # Flag bot-owned lobbies for the frontend
    out["is_bot"] = (int(lobby.get("creator_id") or 0) == BOT_USER_ID)
    return out


# ============================================================
# CREATE
# ============================================================

async def create_lobby(creator_id: int, inv_ids: list[int]) -> dict:
    if not inv_ids:
        return {"ok": False, "error": "Выбери хотя бы один скин"}
    if len(inv_ids) > 500:
        return {"ok": False, "error": "Максимум 500 предметов"}
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
    if len(inv_ids) > 500:
        return {"ok": False, "error": "Максимум 500 предметов"}
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

            # If the casino bot is the winner, delete the entire pot — the bot is
            # the house, it doesn't accumulate inventory. Otherwise transfer to winner.
            if winner_id == BOT_USER_ID:
                await conn.execute(
                    "delete from economy_inventory where coinflip_lobby_id = $1",
                    lobby_id,
                )
            else:
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
            invite_chat = row.get("invite_chat_id") if hasattr(row, "get") else row["invite_chat_id"]
            invite_msg  = row.get("invite_message_id") if hasattr(row, "get") else row["invite_message_id"]

    # Delete the group-chat invitation since the lobby is now resolved
    await _delete_invite_message(invite_chat, invite_msg)

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
    invite_chat = invite_msg = None
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
            invite_chat = row["invite_chat_id"]
            invite_msg  = row["invite_message_id"]
            # Unlock skins
            await conn.execute(
                "update economy_inventory set coinflip_lobby_id = null where coinflip_lobby_id = $1",
                lobby_id,
            )
            await conn.execute(
                "update coinflip_lobbies set status = 'cancelled' where id = $1",
                lobby_id,
            )
    await _delete_invite_message(invite_chat, invite_msg)
    return {"ok": True}


# ============================================================
# EXPIRE (cleanup task — call periodically)
# ============================================================

async def expire_old() -> int:
    """Expire any lobby past its TTL while still open. Returns count.

    Special handling for bot-owned lobbies: instead of just unlocking the
    inventory (which would leave it sitting unused on the bot), we DELETE the
    rows — the bot "sells" stale offers and rotates fresh stock.
    Real-user lobbies just have their lock cleared so the player gets skins back.
    """
    invites_to_delete: list[tuple[int, int]] = []
    async with pool().acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                "select id, creator_id, invite_chat_id, invite_message_id from coinflip_lobbies "
                "where status = 'open' and expires_at < now() for update",
            )
            if not rows:
                return 0
            for r in rows:
                if r["invite_chat_id"] and r["invite_message_id"]:
                    invites_to_delete.append((int(r["invite_chat_id"]), int(r["invite_message_id"])))
            bot_lobby_ids   = [int(r["id"]) for r in rows if int(r["creator_id"]) == BOT_USER_ID]
            human_lobby_ids = [int(r["id"]) for r in rows if int(r["creator_id"]) != BOT_USER_ID]
            if bot_lobby_ids:
                await conn.execute(
                    "delete from economy_inventory where coinflip_lobby_id = any($1::bigint[])",
                    bot_lobby_ids,
                )
            if human_lobby_ids:
                await conn.execute(
                    "update economy_inventory set coinflip_lobby_id = null "
                    "where coinflip_lobby_id = any($1::bigint[])",
                    human_lobby_ids,
                )
            all_ids = bot_lobby_ids + human_lobby_ids
            await conn.execute(
                "update coinflip_lobbies set status = 'expired' where id = any($1::bigint[])",
                all_ids,
            )
    for chat_id, message_id in invites_to_delete:
        await _delete_invite_message(chat_id, message_id)
    return len(rows)


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

async def mark_invited_to_chat(
    lobby_id: int, creator_id: int,
    chat_id: int | None = None, message_id: int | None = None,
) -> dict:
    """Mark the lobby as having sent its chat invitation, plus remember the
    Telegram message_id so we can delete it once the lobby resolves."""
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
                "update coinflip_lobbies set invited_to_chat = true, "
                "invite_chat_id = $2, invite_message_id = $3 "
                "where id = $1",
                lobby_id, chat_id, message_id,
            )
    return {"ok": True}


async def _delete_invite_message(chat_id: int | None, message_id: int | None) -> None:
    """Best-effort delete the lobby's group-chat invitation message."""
    if not chat_id or not message_id:
        return
    try:
        from app.bot import get_bot
        bot = get_bot()
        await bot.delete_message(int(chat_id), int(message_id))
    except Exception as e:
        log.debug("coinflip: failed to delete invite message: %s", e)


# ============================================================
# CASINO BOT — autonomous lobby creator
# ============================================================
#
# A house bot keeps ~24 open lobbies at all times. One spawns every hour with
# random skins / value, lives 24h, then the inventory is auto-deleted (the bot
# "sells" stale offers). When a player beats the bot, they keep both stacks.
# When the bot wins (player joined and lost), all skins are deleted (the bot
# is the house — it doesn't accumulate inventory).

BOT_USER_ID  = 1                 # synthetic tg_id for the casino bot
BOT_USERNAME = "casino_bot"
BOT_DISPLAY_NAME = "🤖 RIP-BOT"
BOT_LOBBY_TARGET = 24


async def ensure_bot_user() -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "insert into users (tg_id, username, first_name) values ($1, $2, $3) "
            "on conflict (tg_id) do update set username = excluded.username, first_name = excluded.first_name",
            BOT_USER_ID, BOT_USERNAME, BOT_DISPLAY_NAME,
        )
        await conn.execute(
            "insert into economy_users (tg_id) values ($1) on conflict (tg_id) do nothing",
            BOT_USER_ID,
        )


# Stake brackets — drives the variance of bot offers so there are cheap/mid/whale lobbies
_BOT_BRACKETS = [
    # (label, weight, item_count_range, rarity_weights)
    ("cheap",   55, (1, 3),  {"consumer": 25, "industrial": 35, "mil-spec": 30, "restricted": 10}),
    ("mid",     30, (2, 4),  {"mil-spec": 25, "restricted": 35, "classified": 30, "covert": 10}),
    ("premium", 12, (2, 5),  {"restricted": 15, "classified": 35, "covert": 40, "exceedingly_rare": 10}),
    ("whale",    3, (1, 3),  {"covert": 55, "exceedingly_rare": 45}),
]


async def _pick_bot_skins() -> list[dict]:
    import random as _r
    from app.economy.pricing import compute_price, roll_float, wear_from_float

    labels  = [b[0] for b in _BOT_BRACKETS]
    weights = [b[1] for b in _BOT_BRACKETS]
    chosen  = _r.choices(labels, weights=weights, k=1)[0]
    bracket = next(b for b in _BOT_BRACKETS if b[0] == chosen)
    n_min, n_max  = bracket[2]
    rarity_weights = bracket[3]
    n_items = _r.randint(n_min, n_max)

    out: list[dict] = []
    rarities  = list(rarity_weights.keys())
    rweights  = list(rarity_weights.values())
    async with pool().acquire() as conn:
        for _ in range(n_items):
            rarity = _r.choices(rarities, weights=rweights, k=1)[0]
            row = await conn.fetchrow(
                "select id, base_price from economy_skins_catalog "
                "where rarity = $1 order by random() limit 1",
                rarity,
            )
            if row is None:
                continue
            float_val = roll_float()
            wear, _ = wear_from_float(float_val)
            stat_trak = _r.random() < 0.05
            price = compute_price(int(row["base_price"]), float_val, wear, stat_trak)
            out.append({
                "skin_id":   int(row["id"]),
                "float":     float(float_val),
                "wear":      wear,
                "stat_trak": stat_trak,
                "price":     int(price),
            })
    return out


async def create_bot_lobby(expire_in_hours: int = 24) -> int | None:
    """Spawn a fresh bot-owned lobby. Inventory rows are conjured at insertion."""
    skins = await _pick_bot_skins()
    if not skins:
        log.warning("bot lobby: no skins picked")
        return None

    async with pool().acquire() as conn:
        async with conn.transaction():
            inv_ids: list[int] = []
            for s in skins:
                inv_id = await conn.fetchval(
                    "insert into economy_inventory "
                    "(user_id, skin_id, float_value, wear, stat_trak, price, source) "
                    "values ($1, $2, $3, $4, $5, $6, 'bot_lobby') returning id",
                    BOT_USER_ID, s["skin_id"], s["float"], s["wear"], s["stat_trak"], s["price"],
                )
                inv_ids.append(int(inv_id))
            total_value = sum(int(s["price"]) for s in skins)
            lobby_id = await conn.fetchval(
                "insert into coinflip_lobbies (creator_id, creator_skins, creator_value, expires_at) "
                "values ($1, $2::jsonb, $3, now() + ($4 || ' hours')::interval) returning id",
                BOT_USER_ID, json.dumps(inv_ids), total_value, str(expire_in_hours),
            )
            await conn.execute(
                "update economy_inventory set coinflip_lobby_id = $2 where id = any($1::bigint[])",
                inv_ids, lobby_id,
            )
    return int(lobby_id)


async def _count_open_bot_lobbies() -> int:
    async with pool().acquire() as conn:
        n = await conn.fetchval(
            "select count(*) from coinflip_lobbies "
            "where creator_id = $1 and status = 'open' and expires_at > now()",
            BOT_USER_ID,
        )
    return int(n or 0)


async def cleanup_orphan_bot_inventory() -> int:
    """Hard-delete any bot-owned inventory rows not tied to an active lobby.
    Called after settles where bot won (so we don't accumulate ghost skins).
    Also covers any inconsistencies after expire/cancel paths."""
    async with pool().acquire() as conn:
        n = await conn.fetchval(
            "with del as ("
            "  delete from economy_inventory "
            "  where user_id = $1 and coinflip_lobby_id is null "
            "  returning 1"
            ") select count(*) from del",
            BOT_USER_ID,
        )
    return int(n or 0)


async def bot_coinflip_loop() -> None:
    """Background task: maintain ~24 active bot lobbies, +1 every hour.

    On startup, backfills with staggered expiry so they don't all expire at the
    same minute. Each cycle: create one fresh lobby + clean orphan inventory.
    """
    import asyncio
    try:
        await ensure_bot_user()
    except Exception:
        log.exception("bot coinflip: ensure_bot_user failed")

    # Startup backfill — stagger so they expire 1h apart
    try:
        existing = await _count_open_bot_lobbies()
        if existing < BOT_LOBBY_TARGET:
            need = BOT_LOBBY_TARGET - existing
            for i in range(need):
                hrs = max(1, i + 1)
                await create_bot_lobby(expire_in_hours=hrs)
            log.info("bot coinflip: backfilled %d lobbies on startup", need)
    except Exception:
        log.exception("bot coinflip: backfill failed")

    # Hourly: spawn a new full-life lobby + cleanup
    while True:
        try:
            await asyncio.sleep(3600)
            await create_bot_lobby(expire_in_hours=24)
            await expire_old()
            killed = await cleanup_orphan_bot_inventory()
            if killed:
                log.info("bot coinflip: cleaned %d orphan bot items", killed)
        except Exception:
            log.exception("bot coinflip: hourly tick failed")
