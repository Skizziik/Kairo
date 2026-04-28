"""Jackpot — pool-style PvP game (CS skin/coin pot, winner takes all).

Round mechanics:
- Continuous: round N runs for 60 sec accepting deposits, then spins (5s
  animation), settles, then 5s breather, then round N+1 starts. Forever.
- Each player can deposit skins (from inventory) and/or raw coins. Both go
  into the same pot. Win chance = your_value / total_value.
- 0-3 bots deposit each round to keep the pot lively. Bot wins burn coins
  (house doesn't accumulate); player wins get the bot's coins added to their
  haul.
- Settlement: winner receives all deposited skins + coins (sums credited).
- Cancellation: if a round ends with <2 deposits, refund and start fresh.

Provably fair:
- At round creation we generate a 32-byte secret `server_seed` and publish
  its SHA256 hash. Everyone sees the hash before they bet.
- At settle we publish the seed; anyone can verify SHA256(seed) == hash.
- The winning ticket is computed deterministically from (round_id, seed),
  so the spin can be independently reproduced.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.db.client import pool

log = logging.getLogger(__name__)


# ============================================================
# CONFIG
# ============================================================

ROUND_DURATION_SEC      = 60       # deposit window
SPIN_DURATION_SEC       = 5        # how long the spinner animation runs
PAUSE_BETWEEN_ROUNDS    = 5        # breather after settle/cancel before next round

MIN_DEPOSIT_VALUE       = 1_000          # 1K min
MAX_PER_PLAYER_VALUE    = 10_000_000     # 10M cap per player per round
MAX_ITEMS_PER_DEPOSIT   = 10
MAX_PLAYERS_PER_ROUND   = 12             # soft visual cap (UI gets cluttered beyond this)

BOT_USER_ID = 1                          # shared with coinflip bot user

# Spinner sequence layout: 50 avatars total, winner at index 44 (so 6 more
# tail tiles after the winner for a smooth slow-stop effect).
SPIN_SEQ_LEN = 50
SPIN_WINNER_INDEX = 44

# Bots
BOT_NAMES = [
    "🤖 Mamba", "🤖 Cobra", "🤖 Viper", "🤖 Anaconda",
    "🤖 Hydra", "🤖 Phantom", "🤖 Kraken", "🤖 Wraith",
    "🤖 Drake",  "🤖 Echo",  "🤖 Onyx",  "🤖 Nova",
]

# Distinct colors assigned cyclically per deposit
DEPOSIT_COLORS = [
    "#eb4b4b", "#5aa9ff", "#5cc15c", "#f5b042",
    "#d32ce6", "#a988ff", "#ffd700", "#00d4ff",
    "#ff6b35", "#7340c4", "#1ed560", "#e4ae39",
]


# ============================================================
# SCHEMA
# ============================================================

async def ensure_schema() -> None:
    sql_path = Path(__file__).parent.parent / "db" / "migration_jackpot.sql"
    if not sql_path.exists():
        log.warning("jackpot migration SQL missing")
        return
    sql = sql_path.read_text(encoding="utf-8")
    async with pool().acquire() as conn:
        await conn.execute(sql)
    log.info("jackpot schema ensured")


# ============================================================
# PROVABLY FAIR
# ============================================================

def _gen_seed() -> str:
    return secrets.token_hex(32)


def _hash_seed(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _winning_ticket(round_id: int, server_seed: str, total_value: int) -> int:
    """Deterministic mapping of (round_id, seed) → ticket in [0, total_value)."""
    if total_value <= 0:
        return 0
    msg = f"{round_id}:{server_seed}".encode()
    h = hashlib.sha256(msg).hexdigest()
    n = int(h[:13], 16)              # 52-bit int
    return n % total_value


# ============================================================
# HELPERS
# ============================================================

def _parse_jsonb(val) -> Any:
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        try: return json.loads(val)
        except Exception: return None
    return None


async def _get_skin_total_value(conn, inventory_ids: list[int], user_id: int) -> tuple[int, list[dict]]:
    """Sum the price of inventory items belonging to user_id and not locked.
    Returns (total_value, rows) where rows is the validated list of items."""
    if not inventory_ids:
        return 0, []
    rows = await conn.fetch(
        "select id, price, locked, coinflip_lobby_id, jackpot_round_id "
        "from economy_inventory "
        "where id = any($1::bigint[]) and user_id = $2 for update",
        inventory_ids, user_id,
    )
    if len(rows) != len(inventory_ids):
        return 0, []
    for r in rows:
        if r["locked"] or r["coinflip_lobby_id"] is not None or r["jackpot_round_id"] is not None:
            return 0, []
    total = sum(int(r["price"]) for r in rows)
    return total, [dict(r) for r in rows]


async def _enrich_deposits(conn, deposits: list[dict]) -> list[dict]:
    """Attach display data (avatar URL, name) for each deposit.
    Bots use their display name + 🤖 emoji; real users get their saved
    Telegram photo_url if available."""
    if not deposits:
        return []
    user_ids = list({int(d["user_id"]) for d in deposits if not d["is_bot"]})
    user_map: dict[int, dict] = {}
    if user_ids:
        urows = await conn.fetch(
            "select tg_id, username, first_name, photo_url from users where tg_id = any($1::bigint[])",
            user_ids,
        )
        for u in urows:
            user_map[int(u["tg_id"])] = dict(u)
    out = []
    for d in deposits:
        d = dict(d)
        if d["is_bot"]:
            display = d.get("bot_name") or "🤖 Bot"
            avatar = None
        else:
            u = user_map.get(int(d["user_id"]))
            display = (u and (u.get("first_name") or u.get("username"))) or f"user{d['user_id']}"
            avatar = (u and u.get("photo_url")) or None
        d["name"] = display
        d["display_name"] = display
        d["avatar_url"] = avatar
        out.append(d)
    return out


def _next_color(used_colors: set[str]) -> str:
    for c in DEPOSIT_COLORS:
        if c not in used_colors:
            return c
    return random.choice(DEPOSIT_COLORS)


# ============================================================
# ROUND LIFECYCLE
# ============================================================

async def _create_round() -> dict:
    """Spawn a new pending round. Returns the row."""
    seed = _gen_seed()
    seed_hash = _hash_seed(seed)
    deposit_ends_at = datetime.now(timezone.utc) + timedelta(seconds=ROUND_DURATION_SEC)
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            insert into jackpot_rounds (status, deposit_ends_at, server_seed, server_seed_hash)
            values ('pending', $1, $2, $3) returning *
            """,
            deposit_ends_at, seed, seed_hash,
        )
    log.info("jackpot: new round #%d", int(row["id"]))
    return dict(row)


async def get_current_round() -> dict | None:
    """The round currently accepting deposits, OR currently spinning (settle pending)."""
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "select * from jackpot_rounds "
            "where status in ('pending', 'spinning') "
            "order by id desc limit 1"
        )
    return dict(row) if row else None


async def get_round_full(round_id: int) -> dict | None:
    async with pool().acquire() as conn:
        row = await conn.fetchrow("select * from jackpot_rounds where id = $1", round_id)
        if row is None:
            return None
        deposits = await conn.fetch(
            "select * from jackpot_deposits where round_id = $1 order by deposited_at asc",
            round_id,
        )
        deposits = [dict(d) for d in deposits]
        for d in deposits:
            d["inventory_ids"] = _parse_jsonb(d["inventory_ids"]) or []
        deposits = await _enrich_deposits(conn, deposits)
    out = dict(row)
    out["spin_sequence"] = _parse_jsonb(out.get("spin_sequence"))
    out["deposits"] = deposits
    return out


def _round_to_public(round_row: dict) -> dict:
    """Render a round row for the API. Hides server_seed if round still active."""
    r = dict(round_row)
    r.pop("server_seed", None) if r.get("status") in ("pending", "spinning") else None
    # Convert datetimes to ISO
    for k in ("started_at", "deposit_ends_at", "spun_at", "settled_at"):
        if k in r and r[k] is not None and hasattr(r[k], "isoformat"):
            r[k] = r[k].isoformat()
    return r


# ============================================================
# DEPOSIT
# ============================================================

async def deposit(user_id: int, inventory_ids: list[int] | None = None,
                  coins: int = 0) -> dict:
    """Deposit skins (by inventory_id) and/or raw coins into the current pending round."""
    inventory_ids = inventory_ids or []
    coins = max(0, int(coins or 0))
    if len(inventory_ids) > MAX_ITEMS_PER_DEPOSIT:
        return {"ok": False, "error": f"Максимум {MAX_ITEMS_PER_DEPOSIT} предметов за раз"}
    inventory_ids = list({int(x) for x in inventory_ids})

    async with pool().acquire() as conn:
        async with conn.transaction():
            # Find current pending round
            r = await conn.fetchrow(
                "select * from jackpot_rounds where status = 'pending' "
                "order by id desc limit 1 for update"
            )
            if r is None:
                return {"ok": False, "error": "Нет активного раунда — подожди"}
            now = datetime.now(timezone.utc)
            if r["deposit_ends_at"] < now:
                return {"ok": False, "error": "Раунд закрылся, депозит не успел"}
            round_id = int(r["id"])

            # Validate skins
            skins_value = 0
            skin_rows: list[dict] = []
            if inventory_ids:
                skins_value, skin_rows = await _get_skin_total_value(conn, inventory_ids, user_id)
                if not skin_rows:
                    return {"ok": False, "error": "Часть скинов недоступна (не твои/заблокированы)"}

            # Validate coins
            if coins > 0:
                bal_row = await conn.fetchrow(
                    "select balance from economy_users where tg_id = $1 for update", user_id,
                )
                if bal_row is None or int(bal_row["balance"]) < coins:
                    return {"ok": False, "error": "Не хватает монет"}

            total_value = skins_value + coins
            if total_value < MIN_DEPOSIT_VALUE:
                return {"ok": False, "error": f"Минимум {MIN_DEPOSIT_VALUE:,} 🪙 за депозит".replace(",", " ")}

            # Per-player cap check (sum prior deposits + this one)
            prior_value = await conn.fetchval(
                "select coalesce(sum(value), 0) from jackpot_deposits "
                "where round_id = $1 and user_id = $2",
                round_id, user_id,
            )
            if int(prior_value or 0) + total_value > MAX_PER_PLAYER_VALUE:
                return {"ok": False, "error": f"Лимит {MAX_PER_PLAYER_VALUE:,} 🪙 на игрока за раунд".replace(",", " ")}

            # Soft cap on number of distinct players
            distinct_players = await conn.fetchval(
                "select count(distinct user_id) from jackpot_deposits where round_id = $1",
                round_id,
            )
            existing_player = await conn.fetchval(
                "select 1 from jackpot_deposits where round_id = $1 and user_id = $2 limit 1",
                round_id, user_id,
            )
            if int(distinct_players or 0) >= MAX_PLAYERS_PER_ROUND and not existing_player:
                return {"ok": False, "error": f"Раунд переполнен ({MAX_PLAYERS_PER_ROUND} игроков)"}

            # Lock skins
            if skin_rows:
                await conn.execute(
                    "update economy_inventory set jackpot_round_id = $2 "
                    "where id = any($1::bigint[])",
                    [int(s["id"]) for s in skin_rows], round_id,
                )

            # Deduct coins
            if coins > 0:
                await conn.execute(
                    "update economy_users set balance = balance - $2, "
                    "total_spent = total_spent + $2 where tg_id = $1",
                    user_id, coins,
                )
                # Record transaction
                bal_row = await conn.fetchrow(
                    "select balance from economy_users where tg_id = $1", user_id,
                )
                new_bal = int(bal_row["balance"]) if bal_row else 0
                await conn.execute(
                    "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                    "values ($1, $2, 'jackpot', $3, $4)",
                    user_id, -coins, f"deposit_round_{round_id}", new_bal,
                )

            # Pick color
            used_colors = set()
            color_rows = await conn.fetch(
                "select color from jackpot_deposits where round_id = $1", round_id,
            )
            for cr in color_rows:
                used_colors.add(cr["color"])
            color = _next_color(used_colors)

            # Insert deposit
            await conn.execute(
                """
                insert into jackpot_deposits (round_id, user_id, inventory_ids, coins, value, color, is_bot)
                values ($1, $2, $3::jsonb, $4, $5, $6, false)
                """,
                round_id, user_id,
                json.dumps([int(s["id"]) for s in skin_rows]),
                coins, total_value, color,
            )

            # Update round total
            await conn.execute(
                "update jackpot_rounds set total_value = total_value + $2 where id = $1",
                round_id, total_value,
            )
    return {"ok": True, "round_id": round_id, "value": total_value}


# ============================================================
# BOT DEPOSIT (auto)
# ============================================================

# Stake brackets for bot deposits
# Bot stake brackets — (label, weight, min_value, max_value, item_count_range, rarity_weights)
_BOT_BRACKETS = [
    ("cheap",   55, 1_000,         8_000,     (1, 2), {"consumer": 30, "industrial": 35, "mil-spec": 25, "restricted": 10}),
    ("mid",     30, 8_001,         80_000,    (1, 3), {"mil-spec": 25, "restricted": 35, "classified": 30, "covert": 10}),
    ("premium", 12, 80_001,        500_000,   (1, 3), {"restricted": 15, "classified": 35, "covert": 40, "exceedingly_rare": 10}),
    ("whale",    3, 500_001,       3_000_000, (1, 3), {"covert": 55, "exceedingly_rare": 45}),
]


async def _gen_bot_skins(conn, target_value: int, bracket: tuple) -> tuple[list[int], int]:
    """Materialize 1-3 skin inventory rows for the bot user that sum near
    `target_value`. Returns (inventory_ids, actual_total_value)."""
    from app.economy.pricing import compute_price, roll_float, wear_from_float

    n_min, n_max = bracket[4]
    rarity_weights = bracket[5]
    rarities  = list(rarity_weights.keys())
    rweights  = list(rarity_weights.values())
    n_items   = random.randint(n_min, n_max)

    inv_ids: list[int] = []
    total = 0
    for _ in range(n_items):
        rarity = random.choices(rarities, weights=rweights, k=1)[0]
        cat_row = await conn.fetchrow(
            "select id, base_price from economy_skins_catalog "
            "where active and rarity = $1 order by random() limit 1",
            rarity,
        )
        if cat_row is None:
            continue
        float_val = roll_float()
        wear, _ = wear_from_float(float_val)
        stat_trak = random.random() < 0.05
        price = compute_price(int(cat_row["base_price"]), float_val, wear, stat_trak)
        inv_id = await conn.fetchval(
            "insert into economy_inventory "
            "(user_id, skin_id, float_value, wear, stat_trak, price, source) "
            "values ($1, $2, $3, $4, $5, $6, 'jackpot_bot') returning id",
            BOT_USER_ID, int(cat_row["id"]), float_val, wear, stat_trak, int(price),
        )
        inv_ids.append(int(inv_id))
        total += int(price)
    return inv_ids, total


async def _bot_deposit(round_id: int) -> None:
    """Bot drops a stake — either coins, skins, or mixed (random) — into a
    pending round. Skin deposits create real economy_inventory rows owned by
    BOT_USER_ID, locked to the round. On settle they transfer to the winner
    or burn if bot wins (same machinery as real-player deposits)."""
    bracket = random.choices(_BOT_BRACKETS, weights=[b[1] for b in _BOT_BRACKETS], k=1)[0]
    target = random.randint(bracket[2], bracket[3])
    # Random deposit shape:
    #   40% pure coins, 40% pure skins, 20% mixed (half/half by target value)
    shape = random.choices(["coins", "skins", "mixed"], weights=[40, 40, 20], k=1)[0]

    async with pool().acquire() as conn:
        async with conn.transaction():
            # Re-check round still pending and not closed
            r = await conn.fetchrow(
                "select id, status, deposit_ends_at, total_value from jackpot_rounds "
                "where id = $1 for update", round_id,
            )
            if r is None or r["status"] != "pending":
                return
            now = datetime.now(timezone.utc)
            if r["deposit_ends_at"] < now + timedelta(seconds=2):
                return  # too late to deposit safely

            # Cap on bot count (don't have more than 4 bots to avoid noise)
            bot_count = await conn.fetchval(
                "select count(*) from jackpot_deposits where round_id = $1 and is_bot = true",
                round_id,
            )
            if int(bot_count or 0) >= 4:
                return

            # Build the actual deposit by shape
            coins_part = 0
            skin_inv_ids: list[int] = []
            skin_value = 0
            if shape == "coins":
                coins_part = target
            elif shape == "skins":
                skin_inv_ids, skin_value = await _gen_bot_skins(conn, target, bracket)
                if not skin_inv_ids:
                    coins_part = target           # fallback if catalog couldn't supply
            else:  # mixed
                coin_target = target // 2
                skin_target = target - coin_target
                coins_part = coin_target
                skin_inv_ids, skin_value = await _gen_bot_skins(conn, skin_target, bracket)
                if not skin_inv_ids:
                    coins_part = target           # fallback

            value = coins_part + skin_value
            if value < MIN_DEPOSIT_VALUE:
                return

            # Lock the bot's freshly-minted skins to this round
            if skin_inv_ids:
                await conn.execute(
                    "update economy_inventory set jackpot_round_id = $2 "
                    "where id = any($1::bigint[])",
                    skin_inv_ids, round_id,
                )

            used_colors = set()
            color_rows = await conn.fetch(
                "select color from jackpot_deposits where round_id = $1", round_id,
            )
            for cr in color_rows:
                used_colors.add(cr["color"])
            color = _next_color(used_colors)
            bot_name = random.choice(BOT_NAMES)

            await conn.execute(
                """
                insert into jackpot_deposits (round_id, user_id, inventory_ids, coins, value, color, is_bot, bot_name)
                values ($1, $2, $3::jsonb, $4, $5, $6, true, $7)
                """,
                round_id, BOT_USER_ID,
                json.dumps(skin_inv_ids), coins_part, value, color, bot_name,
            )
            await conn.execute(
                "update jackpot_rounds set total_value = total_value + $2 where id = $1",
                round_id, value,
            )
    log.debug("jackpot bot: round=%d, name=%s, value=%d", round_id, bot_name, value)


# ============================================================
# SPIN + SETTLE
# ============================================================

def _build_spin_sequence(deposits: list[dict], total_value: int,
                         winner_user_id: int) -> list[dict]:
    """Pre-compute the strip of avatars the client will scroll through.
    Returns list of {user_id, name, color, is_bot} entries; winner placed at SPIN_WINNER_INDEX.

    Distribution: each tile is randomly chosen from deposits, weighted by stake
    (bigger pool share = more frequent on the strip — visually compelling).
    """
    if not deposits:
        return []
    weights = [max(1, int(d["value"])) for d in deposits]
    total = sum(weights) or 1

    seq: list[dict] = []
    for i in range(SPIN_SEQ_LEN):
        if i == SPIN_WINNER_INDEX:
            d = next((x for x in deposits if int(x["user_id"]) == int(winner_user_id)), deposits[0])
        else:
            r = random.uniform(0, total)
            acc = 0.0
            chosen = deposits[0]
            for d, w in zip(deposits, weights):
                acc += w
                if r <= acc:
                    chosen = d
                    break
            d = chosen
        seq.append({
            "user_id":    int(d["user_id"]),
            "name":       d.get("display_name") or d.get("bot_name") or f"user{d['user_id']}",
            "color":      d["color"],
            "is_bot":     bool(d["is_bot"]),
            "avatar_url": d.get("avatar_url"),
        })
    return seq


async def _spin_and_settle(round_id: int) -> None:
    """Spin (mark spinning + compute winner + build sequence), wait SPIN_DURATION_SEC,
    then settle (transfer skins/coins). On <2 deposits, cancel + refund."""
    async with pool().acquire() as conn:
        # Lock the round
        r = await conn.fetchrow(
            "select * from jackpot_rounds where id = $1 for update", round_id,
        )
        if r is None or r["status"] != "pending":
            return  # already handled

        deposits = await conn.fetch(
            "select * from jackpot_deposits where round_id = $1 order by deposited_at asc",
            round_id,
        )
        deposits = [dict(d) for d in deposits]

        # Cancel if not enough activity
        if len(deposits) < 2:
            await _cancel_round(round_id, deposits)
            return

        total_value = int(r["total_value"])
        ticket = _winning_ticket(int(r["id"]), r["server_seed"], total_value)

        # Walk deposits in order, find which one's range contains the ticket
        acc = 0
        winner_id = int(deposits[0]["user_id"])
        for d in deposits:
            v = int(d["value"])
            if ticket < acc + v:
                winner_id = int(d["user_id"])
                break
            acc += v

        # Enrich for sequence display
        deposits_enriched = await _enrich_deposits(conn, deposits)
        seq = _build_spin_sequence(deposits_enriched, total_value, winner_id)

        await conn.execute(
            """
            update jackpot_rounds set
              status = 'spinning',
              spun_at = now(),
              winner_id = $2,
              roll_value = $3,
              spin_sequence = $4::jsonb
            where id = $1
            """,
            round_id, winner_id, ticket, json.dumps(seq),
        )
    log.info("jackpot: round #%d → spinning, winner=%d, ticket=%d/%d",
             round_id, winner_id, ticket, total_value)

    # Wait for animation, then settle
    await asyncio.sleep(SPIN_DURATION_SEC)
    await _settle(round_id)


async def _settle(round_id: int) -> None:
    async with pool().acquire() as conn:
        async with conn.transaction():
            r = await conn.fetchrow(
                "select * from jackpot_rounds where id = $1 for update", round_id,
            )
            if r is None or r["status"] != "spinning":
                return
            winner_id = int(r["winner_id"]) if r["winner_id"] is not None else None
            if winner_id is None:
                return

            deposits = await conn.fetch(
                "select * from jackpot_deposits where round_id = $1", round_id,
            )

            # Total coin payout the winner gets (skins go in-kind below)
            total_coins = sum(int(d["coins"]) for d in deposits)
            total_value = int(r["total_value"])

            # Transfer skins
            if winner_id == BOT_USER_ID:
                # House won — burn all deposited skins (delete inventory rows)
                await conn.execute(
                    "delete from economy_inventory where jackpot_round_id = $1",
                    round_id,
                )
                # Coins from real-player deposits → also burned (not credited anywhere)
            else:
                # Skins → winner
                await conn.execute(
                    "update economy_inventory "
                    "set user_id = $2, jackpot_round_id = null "
                    "where jackpot_round_id = $1",
                    round_id, winner_id,
                )
                # Coins → winner's balance
                if total_coins > 0:
                    await conn.execute(
                        "update economy_users set balance = balance + $2, "
                        "total_earned = total_earned + $2 where tg_id = $1",
                        winner_id, total_coins,
                    )
                    new_bal_row = await conn.fetchrow(
                        "select balance from economy_users where tg_id = $1", winner_id,
                    )
                    new_bal = int(new_bal_row["balance"]) if new_bal_row else 0
                    await conn.execute(
                        "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                        "values ($1, $2, 'jackpot_win', $3, $4)",
                        winner_id, total_coins, f"win_round_{round_id}_pot_{total_value}", new_bal,
                    )

            await conn.execute(
                "update jackpot_rounds set status = 'settled', settled_at = now() where id = $1",
                round_id,
            )

    # Audit (best-effort)
    if winner_id and winner_id != BOT_USER_ID:
        try:
            from app.economy import audit as _audit
            # Each player's "bet" is their deposit value; "win" is total_value if winner
            winner_bet = sum(int(d["value"]) for d in deposits if int(d["user_id"]) == winner_id)
            await _audit.log_bet(
                winner_id, "jackpot",
                bet=winner_bet, win=total_value, net=total_value - winner_bet,
                details={
                    "round_id": round_id,
                    "total_value": total_value,
                    "players": len(set(int(d["user_id"]) for d in deposits)),
                },
                balance_after=None,
            )
        except Exception:
            pass

    log.info("jackpot: round #%d settled, winner=%d, payout=%d", round_id, winner_id, total_value)


async def _cancel_round(round_id: int, deposits: list[dict]) -> None:
    """Refund all deposits and mark cancelled."""
    async with pool().acquire() as conn:
        async with conn.transaction():
            # Unlock skins
            await conn.execute(
                "update economy_inventory set jackpot_round_id = null where jackpot_round_id = $1",
                round_id,
            )
            # Refund coins (per-deposit, only for real users not bots)
            for d in deposits:
                if d["is_bot"]:
                    continue
                coins = int(d["coins"])
                if coins > 0:
                    await conn.execute(
                        "update economy_users set balance = balance + $2, "
                        "total_spent = total_spent - $2 "
                        "where tg_id = $1",
                        int(d["user_id"]), coins,
                    )
            await conn.execute(
                "update jackpot_rounds set status = 'cancelled', settled_at = now() where id = $1",
                round_id,
            )
    log.info("jackpot: round #%d cancelled (only %d deposits)", round_id, len(deposits))


# ============================================================
# AUTOMATION (background loops)
# ============================================================

async def _ensure_round_running() -> None:
    """If there's no pending round → create one. If pending round expired →
    transition to spin/settle. Idempotent, safe to call repeatedly."""
    cur = await get_current_round()
    if cur is None:
        # Are we settling/cancelling something? After last round ends with PAUSE_BETWEEN_ROUNDS,
        # spawn next.
        async with pool().acquire() as conn:
            last = await conn.fetchrow(
                "select status, settled_at from jackpot_rounds order by id desc limit 1"
            )
        if last is None:
            await _create_round()
            return
        # Wait PAUSE_BETWEEN_ROUNDS after last settled/cancelled
        last_at = last["settled_at"]
        if last_at is None or last["status"] not in ("settled", "cancelled"):
            return
        if (datetime.now(timezone.utc) - last_at).total_seconds() >= PAUSE_BETWEEN_ROUNDS:
            await _create_round()
        return

    if cur["status"] == "pending":
        now = datetime.now(timezone.utc)
        if cur["deposit_ends_at"] <= now:
            # Time to spin
            asyncio.create_task(_spin_and_settle(int(cur["id"])))
        return


async def _maybe_drop_bot_deposit() -> None:
    cur = await get_current_round()
    if cur is None or cur["status"] != "pending":
        return
    now = datetime.now(timezone.utc)
    seconds_left = (cur["deposit_ends_at"] - now).total_seconds()
    if seconds_left < 4:
        return
    # Probability per tick (we tick every ~2 seconds). ~0.18 → mean ~0.5 bots per round.
    # We allow up to 4 bots per round (gated inside _bot_deposit).
    if random.random() < 0.18:
        await _bot_deposit(int(cur["id"]))


async def round_loop() -> None:
    """Master loop — ticks every 2s. Manages round transitions + bot deposits."""
    while True:
        try:
            await asyncio.sleep(2)
            await _ensure_round_running()
            await _maybe_drop_bot_deposit()
        except Exception:
            log.exception("jackpot round_loop tick failed")


# ============================================================
# READ APIs
# ============================================================

async def api_current() -> dict:
    """Return the current round (with deposits, no server_seed) so client can render."""
    cur = await get_current_round()
    now = datetime.now(timezone.utc)
    if cur is None:
        # Show last settled briefly during pause, plus countdown to next round
        async with pool().acquire() as conn:
            last = await conn.fetchrow(
                "select * from jackpot_rounds where status in ('settled', 'cancelled') "
                "order by id desc limit 1"
            )
        if last is not None:
            full = await get_round_full(int(last["id"]))
            full["pause_ends_at"] = (last["settled_at"] + timedelta(seconds=PAUSE_BETWEEN_ROUNDS)).isoformat()
            full["server_seconds_left"] = max(0, int((last["settled_at"] + timedelta(seconds=PAUSE_BETWEEN_ROUNDS) - now).total_seconds()))
            return _round_to_public(full)
        return {"status": "idle"}
    full = await get_round_full(int(cur["id"]))
    full["server_seconds_left"] = max(0, int((cur["deposit_ends_at"] - now).total_seconds()))
    return _round_to_public(full)


async def api_round_detail(round_id: int) -> dict | None:
    full = await get_round_full(round_id)
    if full is None:
        return None
    return _round_to_public(full)


async def api_history(limit: int = 30) -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "select id, status, started_at, settled_at, total_value, winner_id, "
            "server_seed_hash, roll_value "
            "from jackpot_rounds "
            "where status in ('settled', 'cancelled') "
            "order by id desc limit $1",
            limit,
        )
        out = []
        for r in rows:
            r = dict(r)
            if r["winner_id"]:
                u = await conn.fetchrow(
                    "select username, first_name from users where tg_id = $1",
                    int(r["winner_id"]),
                )
                if u:
                    r["winner_name"] = u["first_name"] or u["username"] or f"user{r['winner_id']}"
                elif int(r["winner_id"]) == BOT_USER_ID:
                    r["winner_name"] = "🤖 Bot"
                else:
                    r["winner_name"] = f"user{r['winner_id']}"
            else:
                r["winner_name"] = None
            for k in ("started_at", "settled_at"):
                if r.get(k) and hasattr(r[k], "isoformat"):
                    r[k] = r[k].isoformat()
            out.append(r)
    return out


async def api_verify(round_id: int) -> dict | None:
    """Provably-fair verification data for a settled round."""
    full = await get_round_full(round_id)
    if full is None:
        return None
    if full["status"] not in ("settled", "cancelled"):
        return {"ok": False, "error": "Round not finished yet"}
    seed = full.get("server_seed")
    seed_hash = full.get("server_seed_hash")
    total_value = int(full["total_value"])
    ticket = _winning_ticket(int(full["id"]), seed, total_value) if seed and total_value > 0 else None
    # Build cumulative ranges for verification
    ranges = []
    acc = 0
    for d in full["deposits"]:
        v = int(d["value"])
        ranges.append({
            "user_id": int(d["user_id"]),
            "name":    d.get("display_name") or d.get("bot_name"),
            "value":   v,
            "from":    acc,
            "to":      acc + v - 1,
            "is_bot":  bool(d["is_bot"]),
        })
        acc += v
    return {
        "ok": True,
        "round_id":         int(full["id"]),
        "status":           full["status"],
        "server_seed":      seed,
        "server_seed_hash": seed_hash,
        "verified_hash":    _hash_seed(seed) if seed else None,
        "hash_matches":     (_hash_seed(seed) == seed_hash) if seed else False,
        "total_value":      total_value,
        "winning_ticket":   ticket,
        "stored_ticket":    int(full["roll_value"]) if full.get("roll_value") is not None else None,
        "winner_id":        int(full["winner_id"]) if full.get("winner_id") else None,
        "ranges":           ranges,
        "formula":          "ticket = int(SHA256(round_id + ':' + server_seed)[:13], 16) % total_value",
    }
