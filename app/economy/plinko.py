"""Plinko — drop-the-grenade through a peg pyramid into a multiplier bucket.

Mechanics:
- Stateless: each drop is independent. Ball makes `rows` left/right decisions
  (fair coinflip each peg). Final bucket k = number of right-bounces ⇒ binomial.
- Three modes (each with its own row count, payout table, and bet cap):

      casual : 8  rows · 9  buckets · max 4×    · RTP 93.7%
      classic: 12 rows · 13 buckets · max 20×   · RTP 98.4%
      savage : 16 rows · 17 buckets · max 1000× · RTP 99.0%

- Server returns the full path (list of "L"/"R") so the frontend can animate
  the exact route the ball took. Multiplier and payout are server-authoritative.
"""
from __future__ import annotations

import logging
import math
import random
from typing import Literal

from app.db.client import pool

log = logging.getLogger(__name__)


# ============================================================
# CONFIG
# ============================================================

# Each mode = (row count, payout table indexed by right-bounces 0..rows, max bet)
MODES: dict[str, dict] = {
    "casual": {
        "rows": 8,
        "pays": [4, 1.8, 1.2, 0.9, 0.5, 0.9, 1.2, 1.8, 4],
        "max_bet": 5_000,
        "label": "Casual",
        "color": "#5aa9ff",
    },
    "classic": {
        "rows": 12,
        "pays": [20, 7, 4, 2, 1.2, 0.6, 0.3, 0.6, 1.2, 2, 4, 7, 20],
        "max_bet": 2_000,
        "label": "Classic",
        "color": "#f5b042",
    },
    "savage": {
        "rows": 16,
        "pays": [1000, 130, 26, 9, 4, 2, 0.2, 0.2, 0.2, 0.2, 0.2, 2, 4, 9, 26, 130, 1000],
        "max_bet": 500,
        "label": "Savage",
        "color": "#eb4b4b",
    },
}

MIN_BET = 10


def _expected_rtp(rows: int, pays: list[float]) -> float:
    """Sum of p_k · m_k where p_k = C(rows,k)/2^rows. Used for /config display."""
    denom = 2 ** rows
    return sum(math.comb(rows, k) * pays[k] for k in range(rows + 1)) / denom


# Validate at import time — failed assertions indicate someone broke a payout table.
for _key, _mode in MODES.items():
    assert len(_mode["pays"]) == _mode["rows"] + 1, f"{_key}: pays length mismatch"
    _rtp = _expected_rtp(_mode["rows"], _mode["pays"])
    assert 0.85 <= _rtp <= 1.05, f"{_key}: RTP {_rtp:.3f} out of bounds"
    _mode["rtp"] = round(_rtp, 4)


# ============================================================
# PLAY
# ============================================================

async def play_drop(user_id: int, bet: int, mode: str) -> dict:
    if mode not in MODES:
        return {"ok": False, "error": "Invalid mode"}
    m = MODES[mode]
    if bet < MIN_BET:
        return {"ok": False, "error": f"Минимум {MIN_BET} 🪙"}
    if bet > m["max_bet"]:
        return {"ok": False, "error": f"В режиме {m['label']} макс ставка {m['max_bet']} 🪙"}

    rows: int = m["rows"]
    pays: list[float] = m["pays"]

    # Generate a fair binomial path. Each step independently 50/50.
    path = ["R" if random.random() < 0.5 else "L" for _ in range(rows)]
    bucket = sum(1 for d in path if d == "R")
    multiplier = float(pays[bucket])
    payout = int(round(bet * multiplier))
    delta = payout - bet  # net change to balance

    async with pool().acquire() as conn:
        async with conn.transaction():
            bal_row = await conn.fetchrow(
                "select balance from economy_users where tg_id = $1 for update", user_id,
            )
            if bal_row is None or int(bal_row["balance"]) < bet:
                return {"ok": False, "error": "Не хватает монет"}
            new_bal_row = await conn.fetchrow(
                "update economy_users set balance = balance + $2, "
                "total_earned = total_earned + greatest($2, 0), "
                "total_spent = total_spent + greatest(-$2, 0) "
                "where tg_id = $1 returning balance",
                user_id, delta,
            )
            new_bal = int(new_bal_row["balance"])
            await conn.execute(
                "insert into economy_transactions (user_id, amount, kind, reason, balance_after) "
                "values ($1, $2, 'casino', $3, $4)",
                user_id, delta, f"plinko_{mode}_b{bucket}_x{multiplier}", new_bal,
            )

    # Tax accrual on net winnings (only positive delta = real income)
    if delta > 0:
        try:
            from app.economy import tax as _tax
            await _tax.accrue_tax(user_id, delta, "plinko")
        except Exception:
            pass

    # Retention hooks (outside the tx)
    leveled = None
    achievements: list[dict] = []
    try:
        from app.economy import retention as rt
        leveled = await rt.grant_xp(user_id, "plinko_drop")
        await rt.bump_stat_counter(user_id, "plinko_drops", 1)
        if delta > 0:
            await rt.pvp_track(user_id, "plinko_won", 1)
            await rt.pvp_track(user_id, "total_winnings", delta)
            await rt.increment_stattrak_kills_on_win(user_id, 1)
            await rt.bump_stat_counter(user_id, "plinko_wins", 1)
        if multiplier >= 50:
            achievements = await rt.check_achievements_after_action(user_id, "plinko_big_win", {"mult": multiplier})
        if int(new_bal) >= 1_000_000:
            achievements += await rt.check_achievements_after_action(user_id, "balance")
        if leveled and leveled.get("leveled_up"):
            achievements += await rt.check_achievements_after_action(user_id, "level_up")
    except Exception as e:
        log.debug("plinko retention hooks failed: %s", e)

    win = delta > 0
    try:
        from app.economy import audit as _audit
        await _audit.log_bet(
            user_id, "plinko", bet=bet, win=payout, net=delta,
            details={"mode": mode, "bucket": bucket, "multiplier": multiplier, "rows": rows},
            balance_after=new_bal,
        )
    except Exception:
        pass
    return {
        "ok": True,
        "win": win,
        "mode": mode,
        "rows": rows,
        "path": path,             # ["L","R","R",...] — frontend animates along this
        "bucket": bucket,         # final landing index 0..rows
        "multiplier": multiplier,
        "payout": payout,
        "delta": delta,
        "bet": bet,
        "new_balance": new_bal,
        "level": leveled,
        "achievements": achievements,
    }


async def get_config() -> dict:
    """Public config — used by frontend to render mode selector + payout tables."""
    return {
        "min_bet": MIN_BET,
        "modes": {
            key: {
                "rows": m["rows"],
                "pays": m["pays"],
                "max_bet": m["max_bet"],
                "label": m["label"],
                "color": m["color"],
                "rtp": m["rtp"],
            }
            for key, m in MODES.items()
        },
    }
