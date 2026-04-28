"""Mines (CS-themed Сапёр) — server-authoritative gambling minigame.

Mechanics:
- 5×5 grid (25 cells). Player picks `bombs` (1..24) before round.
- Each safe reveal grows the multiplier; player can cash out at any point.
- Hit a bomb → lose the entire bet. Cashout → bet * multiplier.
- Multiplier formula uses the exact "fair" probability:
      M(n) = (1 - HOUSE_EDGE) · C(25, n) / C(25 - bombs, n)
  where n is the number of safe cells already revealed. House edge = 4%
  (RTP ~96%). This matches industry-standard mines games (Stake/Roobet)
  with our tightened edge.

State persistence:
- One active game per user (PK on user_id). Starting a new round while one
  is active errors out — frontend must call /state first.
- Bet is deducted at start; payout credited at cashout/loss.
"""
from __future__ import annotations

import json
import logging
import math
import random
from pathlib import Path
from typing import Any

from app.db.client import pool

log = logging.getLogger(__name__)


# ============================================================
# CONFIG
# ============================================================

GRID_SIZE       = 25
MIN_BOMBS       = 1
MAX_BOMBS       = 24
MIN_BET         = 10
MAX_BET         = 10_000        # matches Crash cap — keeps jackpots from breaking economy
HOUSE_EDGE      = 0.04          # 4% — tighter than slots, looser than crash
RTP             = 1.0 - HOUSE_EDGE

# Sanity ceiling on the multiplier. Without it, bombs=10 + perfect run = 3.1M× which
# would let one game produce billions of coins. Cap = 500× ⇒ 5M coin max payout @ 10k bet.
MAX_PAYOUT_MULT = 500.0


# ============================================================
# SCHEMA
# ============================================================

async def ensure_schema() -> None:
    sql_path = Path(__file__).parent.parent / "db" / "migration_mines.sql"
    if not sql_path.exists():
        log.warning("mines migration SQL missing: %s", sql_path)
        return
    sql = sql_path.read_text(encoding="utf-8")
    async with pool().acquire() as conn:
        await conn.execute(sql)
    log.info("mines schema ensured")


# ============================================================
# CORE MATH
# ============================================================

def _multiplier(bombs: int, revealed: int) -> float:
    """Fair multiplier after `revealed` safe cells, given `bombs` bombs.

    M(0) = 1.0 (no progress yet — but cashout is disabled until first reveal).
    M(n) = RTP · C(25, n) / C(25 - bombs, n)
    """
    if revealed <= 0:
        return 1.0
    safe_total = GRID_SIZE - bombs
    if revealed > safe_total:
        return 0.0  # impossible — guard
    # comb is exact integer arithmetic, no float precision issues
    num = math.comb(GRID_SIZE, revealed)
    den = math.comb(safe_total, revealed)
    if den == 0:
        return 0.0
    m = RTP * num / den
    return min(MAX_PAYOUT_MULT, round(m, 4))


def _next_multiplier(bombs: int, revealed: int) -> float:
    """Multiplier the player would have AFTER one more safe reveal."""
    if revealed + 1 > GRID_SIZE - bombs:
        return _multiplier(bombs, revealed)
    return _multiplier(bombs, revealed + 1)


def _max_multiplier(bombs: int) -> float:
    """Theoretical max payout for picking ALL safe cells."""
    return _multiplier(bombs, GRID_SIZE - bombs)


def _public_state(row: dict) -> dict:
    """Strip secrets (bomb_cells) — only return them after game ends."""
    revealed = row["revealed"] if isinstance(row["revealed"], list) else json.loads(row["revealed"] or "[]")
    bombs = int(row["bombs_count"])
    return {
        "active": True,
        "bet": int(row["bet"]),
        "bombs": bombs,
        "revealed": revealed,
        "revealed_count": len(revealed),
        "multiplier": _multiplier(bombs, len(revealed)),
        "next_multiplier": _next_multiplier(bombs, len(revealed)),
        "max_multiplier": _max_multiplier(bombs),
        "potential_payout": int(int(row["bet"]) * _multiplier(bombs, len(revealed))),
        "next_payout": int(int(row["bet"]) * _next_multiplier(bombs, len(revealed))),
    }


# ============================================================
# PUBLIC API
# ============================================================

async def get_state(user_id: int) -> dict:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "select * from casino_mines_games where user_id = $1", user_id,
        )
    if row is None:
        return {"active": False}
    return _public_state(dict(row))


async def start_game(user_id: int, bet: int, bombs: int) -> dict:
    if bet < MIN_BET:
        return {"ok": False, "error": f"Минимальная ставка {MIN_BET} 🪙"}
    if bet > MAX_BET:
        return {"ok": False, "error": f"Максимальная ставка {MAX_BET} 🪙"}
    if bombs < MIN_BOMBS or bombs > MAX_BOMBS:
        return {"ok": False, "error": f"Бомб должно быть {MIN_BOMBS}–{MAX_BOMBS}"}

    async with pool().acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchrow(
                "select user_id from casino_mines_games where user_id = $1 for update",
                user_id,
            )
            if existing is not None:
                return {"ok": False, "error": "У тебя уже есть активная игра — закрой её сначала", "needs_state": True}
            bal_row = await conn.fetchrow(
                "select balance from economy_users where tg_id = $1 for update",
                user_id,
            )
            if bal_row is None or int(bal_row["balance"]) < bet:
                return {"ok": False, "error": "Недостаточно монет"}
            # Place bombs
            bomb_cells = sorted(random.sample(range(GRID_SIZE), bombs))
            # Deduct bet immediately
            new_bal_row = await conn.fetchrow(
                "update economy_users set balance = balance - $2, "
                "total_spent = total_spent + $2, "
                "mines_games_played = mines_games_played + 1 "
                "where tg_id = $1 returning balance",
                user_id, bet,
            )
            new_bal = int(new_bal_row["balance"])
            await conn.execute(
                "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                "values ($1, $2, 'casino', $3, $4)",
                user_id, -bet, f"mines_start_b{bombs}", new_bal,
            )
            await conn.execute(
                "insert into casino_mines_games (user_id, bet, bombs_count, bomb_cells, revealed) "
                "values ($1, $2, $3, $4::jsonb, '[]'::jsonb)",
                user_id, bet, bombs, json.dumps(bomb_cells),
            )

    return {
        "ok": True,
        "state": {
            "active": True,
            "bet": bet,
            "bombs": bombs,
            "revealed": [],
            "revealed_count": 0,
            "multiplier": 1.0,
            "next_multiplier": _multiplier(bombs, 1),
            "max_multiplier": _max_multiplier(bombs),
            "potential_payout": bet,
            "next_payout": int(bet * _multiplier(bombs, 1)),
        },
        "new_balance": new_bal,
    }


async def reveal(user_id: int, cell: int) -> dict:
    if cell < 0 or cell >= GRID_SIZE:
        return {"ok": False, "error": "Invalid cell"}

    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select * from casino_mines_games where user_id = $1 for update", user_id,
            )
            if row is None:
                return {"ok": False, "error": "Нет активной игры"}
            bomb_cells = row["bomb_cells"] if isinstance(row["bomb_cells"], list) else json.loads(row["bomb_cells"])
            revealed   = row["revealed"]   if isinstance(row["revealed"],   list) else json.loads(row["revealed"] or "[]")
            bombs      = int(row["bombs_count"])
            bet        = int(row["bet"])

            if cell in revealed:
                return {"ok": False, "error": "Эта ячейка уже открыта"}

            is_bomb = cell in bomb_cells

            if is_bomb:
                # GAME OVER — already paid bet at start, no payout. Delete row, reveal all bombs.
                await conn.execute("delete from casino_mines_games where user_id = $1", user_id)
                await conn.execute(
                    "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                    "values ($1, $2, 'casino', $3, (select balance from economy_users where tg_id = $1))",
                    user_id, 0, f"mines_loss_b{bombs}_r{len(revealed)}",
                )
                bal_row = await conn.fetchrow(
                    "select balance from economy_users where tg_id = $1", user_id,
                )
                new_bal = int(bal_row["balance"])
                # All cells revealed (game over)
                all_safe = [i for i in range(GRID_SIZE) if i not in bomb_cells]
                _track_loss = {"user_id": user_id, "bet": bet, "bombs": bombs, "revealed": revealed, "hit_at": cell}
                _post_game_loss = True
                _post_payload: dict[str, Any] = {
                    "ok": True,
                    "safe": False,
                    "cell": cell,
                    "game_over": True,
                    "win": False,
                    "payout": 0,
                    "delta": -bet,
                    "bombs_revealed": bomb_cells,
                    "safe_revealed": revealed,
                    "all_safe": all_safe,
                    "bombs_count": bombs,
                    "new_balance": new_bal,
                }
            else:
                revealed.append(cell)
                # If player has revealed ALL safe cells → auto-cashout at max multiplier
                if len(revealed) >= GRID_SIZE - bombs:
                    mult = _multiplier(bombs, len(revealed))
                    payout = int(bet * mult)
                    delta = payout  # bet was already deducted at start
                    bal_row2 = await conn.fetchrow(
                        "update economy_users set balance = balance + $2, "
                        "total_earned = total_earned + greatest($2, 0), "
                        "mines_games_won = mines_games_won + 1, "
                        "mines_biggest_win = greatest(mines_biggest_win, $2) "
                        "where tg_id = $1 returning balance",
                        user_id, delta,
                    )
                    new_bal = int(bal_row2["balance"])
                    await conn.execute(
                        "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                        "values ($1, $2, 'casino', $3, $4)",
                        user_id, delta, f"mines_perfect_b{bombs}_x{mult}", new_bal,
                    )
                    await conn.execute("delete from casino_mines_games where user_id = $1", user_id)
                    _post_game_loss = False
                    _post_payload = {
                        "ok": True,
                        "safe": True,
                        "cell": cell,
                        "game_over": True,
                        "win": True,
                        "perfect": True,
                        "payout": payout,
                        "delta": delta,
                        "multiplier": mult,
                        "bombs_revealed": bomb_cells,
                        "safe_revealed": revealed,
                        "bombs_count": bombs,
                        "new_balance": new_bal,
                    }
                else:
                    await conn.execute(
                        "update casino_mines_games set revealed = $2::jsonb, updated_at = now() where user_id = $1",
                        user_id, json.dumps(revealed),
                    )
                    cur_mult = _multiplier(bombs, len(revealed))
                    nxt_mult = _next_multiplier(bombs, len(revealed))
                    _post_game_loss = False
                    _post_payload = {
                        "ok": True,
                        "safe": True,
                        "cell": cell,
                        "game_over": False,
                        "revealed_count": len(revealed),
                        "multiplier": cur_mult,
                        "next_multiplier": nxt_mult,
                        "potential_payout": int(bet * cur_mult),
                        "next_payout": int(bet * nxt_mult),
                        "bombs": bombs,
                    }

    # Mission/achievement hooks (outside the transaction)
    try:
        from app.economy import retention as rt
        if _post_payload.get("safe"):
            await rt.bump_stat_counter(user_id, "mines_safe_picks", 1)
        if _post_payload.get("game_over"):
            if _post_payload.get("win"):
                await rt.grant_xp(user_id, "mines_win")
                await rt.track_mission_progress(user_id, "mines_wins", 1)
                if _post_payload.get("delta", 0) > 0:
                    await rt.pvp_track(user_id, "total_winnings", _post_payload["delta"])
                    await rt.increment_stattrak_kills_on_win(user_id, 1)
            else:
                await rt.grant_xp(user_id, "mines_loss")
    except Exception as e:
        log.debug("mines retention hooks failed: %s", e)

    # Audit log only on game-end (one row per round, not per cell-reveal)
    if _post_payload.get("game_over"):
        try:
            from app.economy import audit as _audit
            await _audit.log_bet(
                user_id, "mines",
                bet=bet,
                win=int(_post_payload.get("payout") or 0),
                net=int(_post_payload.get("delta") or 0),
                details={
                    "bombs": _post_payload.get("bombs_count"),
                    "revealed_count": len(_post_payload.get("safe_revealed") or []),
                    "multiplier": _post_payload.get("multiplier"),
                    "win": bool(_post_payload.get("win")),
                    "perfect": bool(_post_payload.get("perfect", False)),
                },
                balance_after=_post_payload.get("new_balance"),
            )
        except Exception:
            pass

    return _post_payload


async def cashout(user_id: int) -> dict:
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select * from casino_mines_games where user_id = $1 for update", user_id,
            )
            if row is None:
                return {"ok": False, "error": "Нет активной игры"}
            bomb_cells = row["bomb_cells"] if isinstance(row["bomb_cells"], list) else json.loads(row["bomb_cells"])
            revealed   = row["revealed"]   if isinstance(row["revealed"],   list) else json.loads(row["revealed"] or "[]")
            bombs      = int(row["bombs_count"])
            bet        = int(row["bet"])
            if not revealed:
                return {"ok": False, "error": "Открой хотя бы одну ячейку"}

            mult = _multiplier(bombs, len(revealed))
            payout = int(bet * mult)
            delta = payout  # bet already deducted at start
            bal_row = await conn.fetchrow(
                "update economy_users set balance = balance + $2, "
                "total_earned = total_earned + greatest($2, 0), "
                "mines_games_won = mines_games_won + 1, "
                "mines_biggest_win = greatest(mines_biggest_win, $2) "
                "where tg_id = $1 returning balance",
                user_id, delta,
            )
            new_bal = int(bal_row["balance"])
            await conn.execute(
                "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                "values ($1, $2, 'casino', $3, $4)",
                user_id, delta, f"mines_cashout_b{bombs}_r{len(revealed)}_x{mult}", new_bal,
            )
            await conn.execute("delete from casino_mines_games where user_id = $1", user_id)

    # Tax accrual on the net win (bet - payout). Bet was already deducted at
    # game start, so payout > bet means a real gain; tax that delta.
    try:
        net = max(0, payout - bet)
        if net > 0:
            from app.economy import tax as _tax
            await _tax.accrue_tax(user_id, net, "mines_win")
    except Exception:
        pass

    try:
        from app.economy import retention as rt
        await rt.grant_xp(user_id, "mines_cashout")
        await rt.track_mission_progress(user_id, "mines_wins", 1)
        if delta > 0:
            await rt.pvp_track(user_id, "total_winnings", delta)
            await rt.increment_stattrak_kills_on_win(user_id, 1)
    except Exception as e:
        log.debug("mines retention hooks failed: %s", e)

    return {
        "ok": True,
        "win": True,
        "perfect": False,
        "game_over": True,
        "multiplier": mult,
        "payout": payout,
        "delta": delta,
        "bombs_revealed": bomb_cells,
        "safe_revealed": revealed,
        "bombs_count": bombs,
        "new_balance": new_bal,
    }


async def get_config() -> dict:
    """Public config — used by frontend to render the bomb-count selector with payouts."""
    cfg = {
        "grid_size": GRID_SIZE,
        "min_bet": MIN_BET,
        "max_bet": MAX_BET,
        "min_bombs": MIN_BOMBS,
        "max_bombs": MAX_BOMBS,
        "rtp": RTP,
        # Pre-computed first-reveal multipliers per bomb count (frontend hint)
        "first_pick_mult": {b: _multiplier(b, 1) for b in range(MIN_BOMBS, MAX_BOMBS + 1)},
        "max_mult":        {b: _max_multiplier(b) for b in range(MIN_BOMBS, MAX_BOMBS + 1)},
    }
    return cfg
